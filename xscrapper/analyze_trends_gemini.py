"""
Analyze X (Twitter) trends from the scraped Excel file using Google Gemini,
then generate 5 image-generation prompts and optionally generate 5 images with Vertex AI Imagen 4.0.

Requires:
- GOOGLE_API_KEY or GEMINI_API_KEY for Gemini (trend analysis)
- Google Cloud Project ID for Vertex AI (image generation)
- Google Cloud authentication: gcloud auth application-default login

Get Gemini API key at https://aistudio.google.com/apikey
"""

import os
import re
import glob
import argparse
import time
from datetime import datetime

try:
    import pandas as pd
except ImportError:
    pd = None

try:
    from google import genai
    from google.genai import types
    GENAI_AVAILABLE = True
except ImportError:
    GENAI_AVAILABLE = False

try:
    import vertexai
    from vertexai.preview.vision_models import ImageGenerationModel
    VERTEX_AI_AVAILABLE = True
except ImportError:
    VERTEX_AI_AVAILABLE = False



def find_excel_file(path=None):
    """Return path to use: given path, or file with most tweets, or latest x_tweets_*.xlsx."""
    if path and os.path.isfile(path):
        return path
    candidates = []
    # Prefer files with more rows (more tweets)
    if pd is not None:
        for p in glob.glob("*.xlsx"):
            try:
                df = pd.read_excel(p, engine="openpyxl")
                row_count = len(df)
                candidates.append((p, row_count, os.path.getmtime(p)))
            except Exception:
                pass
        if candidates:
            # Sort by row count (descending), then by modification time (descending)
            candidates.sort(key=lambda x: (x[1], x[2]), reverse=True)
            print(f"  Found {len(candidates)} Excel files. Selected '{candidates[0][0]}' with {candidates[0][1]} rows.")
            return candidates[0][0]
    # Fallback: use modification time
    candidates = []
    if os.path.isfile("global_tweets.xlsx"):
        candidates.append(("global_tweets.xlsx", os.path.getmtime("global_tweets.xlsx")))
    for p in glob.glob("x_tweets_*.xlsx"):
        candidates.append((p, os.path.getmtime(p)))
    if not candidates:
        return None
    candidates.sort(key=lambda x: x[1], reverse=True)
    return candidates[0][0]


def load_tweets_from_excel(filepath, max_tweets=None):
    """Load ONLY tweet text from Excel (ignores account, likes, retweets, impressions). Returns list of tweet strings."""
    if pd is None:
        raise ImportError("pandas is required. Install with: pip install pandas openpyxl")
    # Read Excel without skipping any rows
    df = pd.read_excel(filepath, engine="openpyxl", keep_default_na=False)
    print(f"  Excel file has {len(df)} total rows and columns: {list(df.columns)}")
    
    # Find column with tweet text (prefer "Tweet text" or "text")
    text_col = None
    for c in df.columns:
        col_lower = str(c).lower()
        if "tweet" in col_lower and "text" in col_lower:
            text_col = c
            break
    if text_col is None:
        for c in df.columns:
            col_lower = str(c).lower()
            if col_lower == "text" or ("tweet" in col_lower and "account" not in col_lower):
                text_col = c
                break
    if text_col is None:
        # Fallback: use first column that isn't account/likes/retweets/impressions
        skip_cols = {"account", "likes", "retweets", "impressions", "id", "handle", "account (id / handle)"}
        for c in df.columns:
            if str(c).lower() not in skip_cols:
                text_col = c
                break
    if text_col is None:
        text_col = df.columns[0]
    
    print(f"  Using column: '{text_col}'")
    # Get all values, convert to string, filter out truly empty ones
    texts = df[text_col].astype(str).tolist()
    print(f"  Total cells in column: {len(texts)}")
    # Filter: keep non-null, non-empty, non-whitespace strings
    texts = [t.strip() for t in texts if t and t.strip() and str(t).lower() not in ("nan", "none", "")]
    print(f"  After filtering empty/whitespace/NaN: {len(texts)} tweets")
    
    if max_tweets:
        texts = texts[:max_tweets]
        print(f"  After max_tweets limit: {len(texts)} tweets")
    return texts


# -----------------------------------------------------------------------------
# Gemini: trend analysis + 5 prompts (text model)
# -----------------------------------------------------------------------------

def _load_env_file():
    """Load .env from script directory if present (KEY=value per line)."""
    for d in (os.path.dirname(os.path.abspath(__file__)), os.getcwd()):
        path = os.path.join(d, ".env")
        if os.path.isfile(path):
            try:
                with open(path, "r") as f:
                    for line in f:
                        line = line.strip()
                        if line and not line.startswith("#") and "=" in line:
                            k, v = line.split("=", 1)
                            k, v = k.strip(), v.strip().strip('"').strip("'")
                            if k and v and k not in os.environ:
                                os.environ[k] = v
            except Exception:
                pass
            break


def get_client():
    """Gemini client. Uses GOOGLE_API_KEY or GEMINI_API_KEY from env or .env file."""
    if not GENAI_AVAILABLE:
        raise ImportError("google-genai is required. Install with: pip install google-genai")
    _load_env_file()
    api_key = os.environ.get("GOOGLE_API_KEY") or os.environ.get("GEMINI_API_KEY")
    if not api_key:
        raise ValueError(
            "Set GOOGLE_API_KEY or GEMINI_API_KEY in the environment or in a .env file. "
            "Get a key at https://aistudio.google.com/apikey"
        )
    return genai.Client(api_key=api_key)


def _extract_retry_delay(error_msg):
    """Extract retry delay from 429 error message. Returns seconds to wait, or None."""
    m = re.search(r"retry in ([\d.]+)s", str(error_msg), re.IGNORECASE)
    if m:
        return float(m.group(1)) + 1  # Add 1s buffer
    return None


def _is_daily_limit(error_str):
    """Check if error is a daily quota limit (not just rate limit)."""
    return "PerDay" in error_str or "limit: 20" in error_str or "daily" in error_str.lower()


def analyze_chunk(client, tweets_chunk, chunk_num, total_chunks, max_retries=2):
    """Analyze one chunk of tweets. Returns trend analysis text. Handles 429 quota errors with retry."""
    # Limit each tweet to ~200 chars to avoid token limits
    tweets_text = "\n".join(f"- {t[:200]}" for t in tweets_chunk)
    print(f"    Sending {len(tweets_chunk)} tweets (~{len(tweets_text)} chars) to Gemini...")
    user = f"""Below are {len(tweets_chunk)} tweets from X (Twitter) - this is chunk {chunk_num} of {total_chunks} total chunks.

TWEETS:
{tweets_text}

TASK: In 2-3 sentences, summarize the main themes and current trends you see in these tweets. Focus on what topics, events, moods, or patterns stand out.

OUTPUT FORMAT:
---TREND ANALYSIS---
(Your 2-3 sentence trend summary here.)
"""
    text_models = ("gemini-2.5-flash", "gemini-2.0-flash", "gemini-3-flash-preview")
    
    for attempt in range(max_retries):
        response = None
        for model_name in text_models:
            try:
                response = client.models.generate_content(model=model_name, contents=user)
                break
            except Exception as e:
                error_str = str(e)
                if "404" in error_str or "not found" in error_str.lower():
                    continue
                # Handle 429 quota errors
                if "429" in error_str or "RESOURCE_EXHAUSTED" in error_str or "QUOTA" in error_str:
                    # Daily limit - don't retry, just raise
                    if _is_daily_limit(error_str):
                        raise RuntimeError(f"Daily quota limit exceeded (20 requests/day on free tier). {error_str}")
                    # Rate limit - retry with delay
                    delay = _extract_retry_delay(error_str)
                    if delay and attempt < max_retries - 1:
                        print(f"    ⚠️  Rate limit hit. Waiting {delay:.1f}s before retry {attempt + 1}/{max_retries}...")
                        time.sleep(delay)
                        continue
                    else:
                        raise
                raise
        if response:
            break
    
    if response is None:
        raise RuntimeError(f"None of {text_models} are available after {max_retries} attempts.")
    text = getattr(response, "text", None) or ""
    if not text and getattr(response, "candidates", None) and response.candidates:
        parts = response.candidates[0].content.parts
        text = " ".join(getattr(p, "text", "") or "" for p in parts)
    # Extract analysis section
    m = re.search(r"---TREND ANALYSIS---\s*(.*?)(?=---|\Z)", text, re.DOTALL | re.IGNORECASE)
    return m.group(1).strip() if m else text.strip()


def analyze_trends_and_generate_prompts(client, tweets_list, chunk_size=200):
    """
    Chunk all tweets, analyze each chunk, combine analyses, then generate 5 prompts.
    Returns (analysis_text, list of 5 prompt strings).
    """
    total_tweets = len(tweets_list)
    if total_tweets == 0:
        raise ValueError("No tweets found in Excel file.")
    
    # Split into chunks
    chunks = []
    for i in range(0, total_tweets, chunk_size):
        chunks.append(tweets_list[i:i + chunk_size])
    num_chunks = len(chunks)
    
    print(f"\nProcessing {total_tweets} tweets in {num_chunks} chunks (chunk size: {chunk_size})...")
    print(f"Chunk breakdown: {[len(c) for c in chunks]}\n")
    
    # Analyze each chunk
    chunk_analyses = []
    for i, chunk in enumerate(chunks, 1):
        print(f"  Analyzing chunk {i}/{num_chunks} ({len(chunk)} tweets)...")
        try:
            analysis = analyze_chunk(client, chunk, i, num_chunks)
            chunk_analyses.append(analysis)
            print(f"    ✓ Chunk {i} analysis complete ({len(analysis)} chars)")
        except Exception as e:
            error_str = str(e)
            if "429" in error_str or "RESOURCE_EXHAUSTED" in error_str or "QUOTA" in error_str:
                # Check if it's a daily limit (not just rate limit)
                if _is_daily_limit(error_str):
                    print(f"    ⚠️  Daily quota limit reached (20 requests/day on free tier).")
                    if len(chunk_analyses) > 0:
                        print(f"    Using {len(chunk_analyses)} chunks analyzed so far to generate prompts.")
                        break
                    else:
                        raise RuntimeError(
                            "Daily quota limit exceeded (20 requests/day). "
                            "Free tier allows only 20 requests per day. "
                            "Wait until tomorrow or enable billing for higher limits."
                        )
                # Otherwise, it's a rate limit - retry with delay
                delay = _extract_retry_delay(error_str)
                if delay:
                    print(f"    ⚠️  Rate limit hit. Waiting {delay:.1f}s then retrying...")
                    time.sleep(delay)
                    try:
                        analysis = analyze_chunk(client, chunk, i, num_chunks)
                        chunk_analyses.append(analysis)
                        print(f"    ✓ Chunk {i} analysis complete after retry ({len(analysis)} chars)")
                    except Exception as e2:
                        if _is_daily_limit(str(e2)):
                            print(f"    ⚠️  Daily quota limit reached during retry.")
                            break
                        print(f"    ✗ Error analyzing chunk {i} after retry: {e2}")
                        continue
                else:
                    print(f"    ✗ Error analyzing chunk {i}: {e}")
                    continue
            else:
                print(f"    ✗ Error analyzing chunk {i}: {e}")
                continue
        time.sleep(1.0)  # Delay between API calls to avoid rate limits
    
    if not chunk_analyses:
        print("\n" + "="*70)
        print("  ⚠️  FREE TIER QUOTA EXCEEDED")
        print("="*70)
        print("  Gemini API free tier allows only 20 requests per day.")
        print("  You've hit this limit. Options:")
        print("\n  1. Wait until tomorrow (quota resets at midnight Pacific)")
        print("  2. Enable billing for higher limits:")
        print("     https://aistudio.google.com/api-keys → Set up Billing")
        print("  3. Use larger chunks to reduce API calls:")
        print(f"     python analyze_trends_gemini.py --chunk-size {min(500, total_tweets)}")
        print(f"     (With {total_tweets} tweets, this would use ~{((total_tweets-1)//min(500, total_tweets))+1} API calls instead of {num_chunks})")
        print("="*70)
        raise RuntimeError("No chunks were successfully analyzed. Free tier quota exceeded (20 requests/day).")
    
    print(f"\n  Successfully analyzed {len(chunk_analyses)}/{num_chunks} chunks")
    
    # Combine all chunk analyses
    combined_analyses = "\n\n".join(f"Chunk {i+1} analysis:\n{a}" for i, a in enumerate(chunk_analyses))
    
    # Generate final 5 prompts from combined analysis
    user = f"""Below are trend analyses from {num_chunks} chunks of tweets (total {total_tweets} tweets) scraped from X (Twitter).

CHUNK ANALYSES:
{combined_analyses}

TASKS:
1) In 2-4 sentences, synthesize the main themes and current trends across ALL these analyses.
2) Create exactly 5 image-generation prompts for Vertex AI Imagen 4.0. Each prompt should:
   - Be a single, vivid visual description suitable for generating one image
   - Reflect the overall trends/themes from all the analyses (news, events, mood, topics)
   - Be detailed and concrete (style, composition, mood) so the image model can produce a strong image
   - Be one paragraph per prompt, no bullet points inside the prompt

OUTPUT FORMAT (use this exactly):
---TREND ANALYSIS---
(Your 2-4 sentence synthesized trend summary here.)

---PROMPT 1---
(First image prompt, one paragraph.)

---PROMPT 2---
(Second image prompt.)

---PROMPT 3---
(Third image prompt.)

---PROMPT 4---
(Fourth image prompt.)

---PROMPT 5---
(Fifth image prompt.)
"""
    
    print("Generating final 5 prompts from combined analysis...")
    text_models = ("gemini-2.5-flash", "gemini-2.0-flash", "gemini-3-flash-preview")
    response = None
    for model_name in text_models:
        try:
            response = client.models.generate_content(model=model_name, contents=user)
            break
        except Exception as e:
            if "404" in str(e) or "not found" in str(e).lower():
                continue
            raise
    if response is None:
        raise RuntimeError(f"None of {text_models} are available.")
    text = getattr(response, "text", None) or ""
    if not text and getattr(response, "candidates", None) and response.candidates:
        parts = response.candidates[0].content.parts
        text = " ".join(getattr(p, "text", "") or "" for p in parts)
    if not text:
        raise RuntimeError("Gemini returned no text.")

    # Parse sections
    analysis = ""
    prompts = []
    section = re.search(r"---TREND ANALYSIS---\s*(.*?)(?=---PROMPT 1---)", text, re.DOTALL | re.IGNORECASE)
    if section:
        analysis = section.group(1).strip()

    for i in range(1, 6):
        pat = rf"---PROMPT {i}---\s*(.*?)(?=---PROMPT \d+---|\Z)"
        m = re.search(pat, text, re.DOTALL | re.IGNORECASE)
        if m:
            prompts.append(m.group(1).strip())
    if len(prompts) < 5:
        # Fallback parsing
        lines = text.split("\n")
        for line in lines:
            line = line.strip()
            if re.match(r"^\d+[.)]\s*", line) or line.startswith("-"):
                prompts.append(re.sub(r"^\d+[.)]\s*", "", line).strip())
        prompts = [p for p in prompts if len(p) > 20][:5]
    return analysis, prompts[:5]


# -----------------------------------------------------------------------------
# Vertex AI: generate images with Imagen 4.0
# -----------------------------------------------------------------------------

def generate_image_vertex_ai(project_id, location, prompt, output_path, model_name="imagen-4.0-generate-001"):
    """Generate one image with Vertex AI Imagen 4.0 (default) or other Imagen models."""
    if not VERTEX_AI_AVAILABLE:
        raise ImportError("vertexai is required. Install with: pip install google-cloud-aiplatform")
    
    # Note: Vertex AI uses Google Cloud authentication, not API keys
    # Vertex AI will use:
    # 1. Application Default Credentials (gcloud auth application-default login)
    # 2. GOOGLE_APPLICATION_CREDENTIALS env var pointing to service account JSON
    # 3. Credentials from gcloud SDK
    
    # Initialize Vertex AI
    try:
        vertexai.init(project=project_id, location=location)
    except Exception as e:
        error_msg = str(e)
        if "authentication" in error_msg.lower() or "credentials" in error_msg.lower():
            raise RuntimeError(
                f"Authentication error: {e}\n\n"
                "Vertex AI requires Google Cloud authentication. Set it up:\n"
                "  1. Install gcloud CLI: https://cloud.google.com/sdk/docs/install\n"
                "  2. Run: gcloud auth application-default login\n"
                "  3. Or set GOOGLE_APPLICATION_CREDENTIALS=/path/to/service-account-key.json\n"
                "  4. Ensure Vertex AI API is enabled: https://console.cloud.google.com/apis/library/aiplatform.googleapis.com"
            )
        raise RuntimeError(f"Failed to initialize Vertex AI: {e}")
    
    model = ImageGenerationModel.from_pretrained(model_name)
    
    try:
        images = model.generate_images(
            prompt=prompt,
            number_of_images=1,
            language="en",
            aspect_ratio="1:1",
            safety_filter_level="block_some",
            person_generation="allow_adult",
        )
        if images and len(images) > 0:
            images[0].save(location=output_path, include_generation_parameters=False)
            return True
    except Exception as e:
        error_msg = str(e)
        if "PERMISSION_DENIED" in error_msg or "authentication" in error_msg.lower():
            raise RuntimeError(
                f"Authentication error: {e}\n"
                "Vertex AI requires Google Cloud authentication. Try:\n"
                "  1. Run: gcloud auth application-default login\n"
                "  2. Or set GOOGLE_APPLICATION_CREDENTIALS to a service account JSON key\n"
                "  3. Or ensure your project has Vertex AI API enabled"
            )
        raise
    return False


# -----------------------------------------------------------------------------
# Image generation wrapper
# -----------------------------------------------------------------------------

def generate_five_images(vertex_project_id, vertex_location, prompts, out_dir=".", model_name="imagen-4.0-generate-001"):
    """Generate 5 images from the 5 prompts using Vertex AI Imagen 4.0; save to out_dir."""
    os.makedirs(out_dir, exist_ok=True)
    generated = []
    for i, prompt in enumerate(prompts[:5], 1):
        path = os.path.join(out_dir, f"trend_image_{i}.png")
        try:
            if generate_image_vertex_ai(vertex_project_id, vertex_location, prompt, path, model_name):
                generated.append(path)
                print(f"  ✓ Saved: {path}")
            else:
                print(f"  ✗ No image returned for prompt {i}.")
        except Exception as e:
            print(f"  ✗ Error generating image {i}: {e}")
    return generated


# -----------------------------------------------------------------------------
# Main
# -----------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Analyze X trends from Excel with Gemini and generate 5 image prompts (and optionally images).")
    parser.add_argument("excel", nargs="?", default=None, help="Path to Excel file (default: latest x_tweets_*.xlsx or global_tweets.xlsx)")
    parser.add_argument("--no-images", action="store_true", help="Only analyze and generate prompts; do not call the image model.")
    parser.add_argument("--chunk-size", type=int, default=200, help="Tweets per chunk for API calls (default 200). All tweets are processed.")
    parser.add_argument("--out-dir", default=".", help="Directory for trend_analysis_prompts.txt and trend_image_*.png (default: current dir)")
    parser.add_argument("--vertex-project-id", default=None, help="Google Cloud project ID for Vertex AI (required for image generation). Can also set VERTEX_PROJECT_ID env var.")
    parser.add_argument("--vertex-location", default="us-central1", help="Vertex AI location/region (default: us-central1)")
    parser.add_argument("--imagen-model", default="imagen-4.0-generate-001", help="Imagen model to use: imagen-4.0-generate-001 (default), imagen-4.0-fast-generate-001, imagen-4.0-ultra-generate-001")
    args = parser.parse_args()

    filepath = find_excel_file(args.excel)
    if not filepath:
        print("No Excel file found. Run the scraper first or pass a path: python analyze_trends_gemini.py path/to/tweets.xlsx")
        return 1

    print(f"Loading tweets from: {filepath}")
    tweets_list = load_tweets_from_excel(filepath)
    print(f"Loaded {len(tweets_list)} tweets (text only, other columns ignored).\n")

    if not GENAI_AVAILABLE:
        print("Install google-genai: pip install google-genai")
        return 1

    client = get_client()

    print("Analyzing trends and generating 5 image prompts...")
    analysis, prompts = analyze_trends_and_generate_prompts(client, tweets_list, chunk_size=args.chunk_size)

    os.makedirs(args.out_dir, exist_ok=True)
    out_file = os.path.join(args.out_dir, "trend_analysis_prompts.txt")
    with open(out_file, "w", encoding="utf-8") as f:
        f.write("TREND ANALYSIS (from X / Twitter scraped data)\n")
        f.write("=" * 60 + "\n\n")
        f.write(analysis)
        f.write("\n\n")
        f.write("5 IMAGE PROMPTS FOR VERTEX AI IMAGEN 4.0\n")
        f.write("=" * 60 + "\n\n")
        for i, p in enumerate(prompts, 1):
            f.write(f"--- PROMPT {i} ---\n{p}\n\n")
    print(f"Saved analysis and prompts to: {out_file}\n")

    print("Trend summary:")
    print(analysis)
    print("\n5 prompts for Vertex AI Imagen:")
    for i, p in enumerate(prompts, 1):
        print(f"  {i}. {p[:80]}...")

    if not args.no_images and prompts:
        if not VERTEX_AI_AVAILABLE:
            print("\nError: Vertex AI not available. Install with: pip install google-cloud-aiplatform")
            return 1
        project_id = args.vertex_project_id or os.environ.get("VERTEX_PROJECT_ID")
        if not project_id:
            print("\n" + "="*70)
            print("  ⚠️  VERTEX AI PROJECT ID REQUIRED")
            print("="*70)
            print("  To generate images, you need:")
            print("  1. Your Google Cloud Project ID")
            print("  2. Google Cloud authentication set up")
            print("\n  SETUP:")
            print("  1. Get your Project ID from: https://console.cloud.google.com")
            print("  2. Authenticate: gcloud auth application-default login")
            print("  3. Enable Vertex AI API: https://console.cloud.google.com/apis/library/aiplatform.googleapis.com")
            print("\n  Then run:")
            print("    python analyze_trends_gemini.py --vertex-project-id YOUR_PROJECT_ID")
            print("="*70)
            return 1
        
        print(f"\nGenerating 5 images with Vertex AI Imagen ({args.imagen_model}, project: {project_id})...")
        print("  Note: Make sure you've run: gcloud auth application-default login")
        generate_five_images(project_id, args.vertex_location, prompts, args.out_dir, model_name=args.imagen_model)
    elif args.no_images:
        print("\nSkipping image generation (--no-images). Remove --no-images to generate 5 images.")

    return 0


if __name__ == "__main__":
    exit(main())
