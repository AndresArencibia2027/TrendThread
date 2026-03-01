import os
import re
from google.genai import types
from google import genai

try:
    import pandas as pd
except ImportError:
    pd = None

def get_client():
    """Initializes the Vertex AI client for 2026 Trend Intelligence."""
    return genai.Client(
        vertexai=True,
        project=os.getenv("VERTEX_PROJECT_ID"),
        location=os.getenv("VERTEX_LOCATION", "us-central1")
    )

def _format_bq_data(bq_context):
    if not bq_context: return "No search trend data available."
    return "\n".join([f"- {item['term']} (Momentum: {item.get('momentum', 'N/A')})" for item in bq_context])

def _format_gdelt_data(gdelt_context):
    if not gdelt_context: return "No news coverage data available."
    return "\n".join([f"- {art.get('title')} (Source: {art.get('source', 'N/A')})" for art in gdelt_context[:10]])

def _load_tweets(excel_path):
    if not excel_path or not os.path.exists(excel_path): return "No social media data available."
    if pd is None: return "Pandas not installed."
    df = pd.read_excel(excel_path, engine="openpyxl")
    text_col = next((c for c in df.columns if "text" in str(c).lower()), df.columns[0])
    return "\n".join([f"- {t[:150]}" for t in df[text_col].astype(str).tolist()[:30]])

def _prepare_image_part(image_path):
    """Encodes local images for multimodal vision analysis."""
    try:
        with open(image_path, "rb") as f:
            img_data = f.read()
        return types.Part.from_bytes(data=img_data, mime_type="image/jpeg")
    except Exception as e:
        print(f"       Failed to load image {image_path}: {e}")
        return None

def distill_search_terms(client, bq_context, gdelt_context, excel_path):
    """
    STAGE 1: Strategic Curation.
    Extracts 2026 viral motifs and enriches search terms with cultural anchors 
    to ensure precise visual discovery and identity expression.
    """
    bq_str = _format_bq_data(bq_context)
    news_str = _format_gdelt_data(gdelt_context)
    social_str = _load_tweets(excel_path)

    system_instruction = """
    You are a 2026 Trend Signal Extraction Engine.

    Your objective:
    Output 3 REAL, SEARCH-VALIDATABLE viral trend motifs from 2026.

    CORE PRINCIPLE: RETRIEVAL PRECISION

    Each TERM must be:
    - Specific enough to return the correct visual cluster when pasted into Google, X, or TikTok.
    - Disambiguated using a Cultural Anchor.

    -----------------------------------
    CULTURAL ANCHOR REQUIREMENT
    -----------------------------------
    If the motif could refer to multiple things, you MUST append a disambiguator.

    Valid anchors include:
    - Franchise / series name
    - Film studio
    - Artist name
    - Brand
    - Event name
    - Location + event
    - Platform-native hashtag (if dominant)

    The anchor must be ESSENTIAL to identifying the correct trend.

    GOOD:
    - Vibe the Cat Panty and Stocking
    - Backrooms movie A24 horror
    - Sydney Sweeney Met Gala protest dress
    - GTA 6 trailer Miami leak

    BAD:
    - Vibe the Cat
    - Backrooms movie
    - Met Gala dress
    - GTA trailer

    Do NOT randomly append brands unless they are genuinely tied to the trend.

    -----------------------------------
    MARKETABILITY FILTER
    -----------------------------------
    1. WEARABILITY TEST
    Discard anything that is:
    - Local politics
    - Minor crime
    - Routine corporate updates
    - Mundane announcements

    If someone would not wear it to signal identity or cultural alignment, discard it.

    2. MEME RULE
    If the trend is narrative-driven and lacks a singular visual icon,
    explicitly include "Meme Treatment" in CONTEXT.

    -----------------------------------
    SEARCH DISCIPLINE
    -----------------------------------
    - Use language already circulating publicly.
    - TERM must be 3–8 words.
    - No quotes, no bolding, no parentheses.
    - No hashtags unless the hashtag is the primary identifier.
    - No invented names.

    -----------------------------------
    THE NO-TRASH PROTOCOL
    -----------------------------------

    1. EVALUATE THE PROVIDED DATA
    You must prioritize motifs that appear across:
    - BQ (search acceleration)
    - GDELT (media amplification)
    - X/social discourse (repeat participation)

    2. DISCARD LOW-SIGNAL DATA
    Ignore:
    - Local politics or municipal figures
    - Radio visits or routine press stops
    - Minor crime
    - Corporate earnings calls
    - Generic AI commentary
    - 2025 holdovers without measurable 2026 spike

    3. HIGH-SIGNAL FILTER
    A valid trend must show:
    - Identity signaling potential (wearable, memetic, symbolic)
    - Clear visual DNA (recognizable character/object/style)
    - Replication behavior (memes, edits, discourse, remixes)

    4. NO FABRICATION RULE
    You MAY NOT invent trends outside the provided datasets. You may utilize well known current memes and motifs.

    OUTPUT FORMAT:
    TERM: [Anchor-Enriched Search Term] | SUBJECT: [Specific icon/character] | CONTEXT: [Narrative/Story]
    """
    
    prompt = f"DATA SOURCES:\nBQ: {bq_str}\nNEWS: {news_str}\nSOCIAL: {social_str}"
    
    response = client.models.generate_content(
        model="gemini-2.0-flash",
        config=types.GenerateContentConfig(system_instruction=system_instruction),
        contents=prompt
    )
    
    # regex refined to strip illegal path characters like quotes, colons, and slashes [WinError 123 fix]
    raw_results = re.findall(r"TERM:\s*(.*?)\s*\|\s*SUBJECT:\s*(.*?)\s*\|\s*CONTEXT:\s*(.*)", response.text)
    
    clean_trends = []
    for r in raw_results:
        # HARD SANITIZATION: Removes characters that cause WinError 123 (", *, :, /, \, ?, <, >, |)
        raw_term = r[0].strip()
        clean_term = re.sub(r'[\\/*?:"<>|*]', '', raw_term)
        
        clean_trends.append({
            "term": clean_term, 
            "subject": r[1].strip(), 
            "context": r[2].strip()
        })
        
    return clean_trends[:3]

def analyze_visual_strategy(client, trend_visuals_map, trend_data):
    """
    STAGE 2: Multi-modal Creative Direction.
    Decides the path to 'Commercial Readiness' based on Subject visibility.
    """
    system_instruction = """
    You are a Senior Creative Director specializing in 2026 viral motifs and identity-driven commerce.
    Your goal is to transform "raw signal" into "trustworthy product."

    OBJECTIVE:
    Identify the singular SUBJECT of the trend. Customers buy to express identity through icons, not generic scenes.

    STRICT DECISION LOGIC:

    1. [REGEN] - MANDATORY IF:
       - The subject (e.g., Vibe the cat) is sleeping, obscured, or poorly framed.
       - The images look like low-quality screengrabs, grainy cell photos, or cluttered news footage.
       - No single image stands out as a "professional graphic."
       - PROMPT REQUIREMENT: Write a prompt for a "Flat Vector Illustration" or "Isolated Die-cut Sticker" on a SOLID WHITE BACKGROUND.

    2. [MEME] - MANDATORY IF:
       - The trend is based on a "Funny News Story" or narrative discourse rather than a singular character icon.
       - Adding a specific narrative punchline (Social Proof) increases the emotional appeal.
       - TEXT RULE: Max 6 words. Must provide the "Context" the customer needs to trust the trend.

    3. [CLEAN] - USE ONLY IF:
       - An image is ALREADY a high-resolution, professional-grade icon on a simple, removable background.
       - Subject is 100% visible and centered.

    MARKETABILITY CRITERIA:
    - DISCARD GENERIC NOISE: If a trend lacks an emotional hook or recognizable visual DNA (e.g., local politics, mundane news), ignore it. 
    - IDENTITY EXPRESSION: Ask: "Would a customer wear this to start a conversation?" If not, REGEN it into a visual motif that is iconic.

    OUTPUT FORMAT (STRICTLY ONE LINE PER TREND):
    TREND: [term] | DECISION: [CLEAN/MEME/REGEN] | ACTION: [If MEME: text. If REGEN: detailed prompt. If CLEAN: None] | SOURCE: [path_of_BEST_visible_image]
    """

    content_parts = ["Analyze these 5-image groups for Subject visibility and Narrative relevance:"]
    for item in trend_data:
        term, subject, context = item['term'], item['subject'], item['context']
        content_parts.append(f"\n--- TREND: {term} ---\nSUBJECT: {subject}\nNARRATIVE: {context}")
        
        if term in trend_visuals_map:
            for path in trend_visuals_map[term]:
                img_part = _prepare_image_part(path)
                if img_part:
                    content_parts.append(f"Candidate Path: {path}")
                    content_parts.append(img_part)

    response = client.models.generate_content(
        model="gemini-2.0-flash", 
        config=types.GenerateContentConfig(system_instruction=system_instruction),
        contents=content_parts
    )
    return response.text