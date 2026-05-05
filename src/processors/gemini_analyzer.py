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

def _format_kym_data(kym_context):
    """Formats the independent KnowYourMeme signal for the analyzer."""
    if not kym_context: return "No confirmed meme data available."
    return "\n".join([f"- {item['title']} (Confirmed Motif)" for item in kym_context])

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

def distill_search_terms(client, bq_context, gdelt_context, excel_path, kym_context = []):
    """
    STAGE 1: Strategic Curation.
    Extracts 2026 viral motifs and enriches search terms with cultural anchors 
    to ensure precise visual discovery and identity expression.
    """
    bq_str = _format_bq_data(bq_context)
    news_str = _format_gdelt_data(gdelt_context)
    social_str = _load_tweets(excel_path)
    kym_str = _format_kym_data(kym_context)

    system_instruction = """
    You are a 2026 Trend Signal Extraction Engine.

    Your objective:
    Output 5 REAL, SEARCH-VALIDATABLE viral trend motifs from 2026.

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

    The anchor must be ESSENTIAL to identifying the correct trend. Do NOT randomly append brands unless they are genuinely tied to the trend.

    -----------------------------------
    MARKETABILITY FILTER (THE WEARABILITY TEST)
    -----------------------------------
    - Ask: "Would a customer wear this to express identity?"
    - Discard: Minor memes, local politics, radio visits, and 2025 holdovers.
    - Discard: Generic copyright brands (like BTS) or utility trends (Blood Moons).
    - Favor: Specific icons, characters, or "Visual DNA" that signals cultural alignment.

    -----------------------------------
    SEARCH DISCIPLINE
    -----------------------------------
    - Use language already circulating publicly.
    - TERM must be 2–8 words.
    - NAKED TEXT: No quotes, bolding, or fluff words like "theory" or "leak."

    -----------------------------------
    THE NO-TRASH PROTOCOL
    -----------------------------------

    1. EVALUATE THE PROVIDED DATA
    You must prioritize motifs that appear across:
    - BQ (search acceleration)
    - GDELT (media amplification)
    - X/social discourse (repeat participation)
    - Know Your Meme (KYM)

    2. DISCARD LOW-SIGNAL DATA
    Ignore:
    - Local politics or municipal figures
    - Radio visits or routine press stops
    - Minor crime
    - Corporate earnings calls
    - Generic AI commentary
    - 2025 holdovers without measurable 2026 spike
    - Irrelevant subjects that would not make good products. People will not buy products relating to short--lived trends (e.g. eclipses, blood moons, google snake, etc.)
    - Should not refer to copyrighted brands (like bands such as BTS)

    3. HIGH-SIGNAL FILTER
    A valid trend must show:
    - Identity signaling potential (wearable, memetic, symbolic)
    - Clear visual DNA (recognizable character/object/style)
    - Replication behavior (memes, edits, discourse, remixes)

    4. NO FABRICATION RULE
    You MAY NOT invent trends outside the provided datasets. You may utilize well known current memes and motifs.

    -----------------------------------
    SOURCE HIERARCHY (THE WEIGHTING RULE)
    -----------------------------------
    1. PRIMARY SIGNAL: BigQuery (BQ). BQ spikes indicate massive search intent. 
       Prioritize terms found here as they represent high-volume consumer demand.
    
    2. SECONDARY SIGNAL: GDELT & SOCIAL. Use these to verify if the BQ spike 
       has cross-platform momentum.
    
    3. THE "KYM" FILTER: Know Your Meme (KYM) is a double-edged sword. 
       - Use KYM to find the 'Visual DNA' (Franchise/Artist/Icon) for BQ trends.
       - If a KYM entry has NO corresponding search spike in BQ, it is likely UNMARKETABLE.
       - Discard memes that are funny but visually "trashy" or low-effort scraps.

    OUTPUT FORMAT:
    TERM: [Anchor-Enriched Search Term] | SUBJECT: [Specific icon/character] | CONTEXT: [Narrative/Story]
    """
    
    prompt = f"""
    PRIORITY 1 - BQ (SEARCH INTENT):
    {bq_str}

    PRIORITY 2 - MEDIA & SOCIAL (MOMENTUM):
    NEWS: {news_str}
    SOCIAL: {social_str}

    PRIORITY 3 - KYM (VISUAL CONTEXT & SUPPLEMENTAL):
    {kym_str}
    """
    
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
        
    return clean_trends[:5]

def analyze_visual_strategy(client, trend_visuals_map, trend_data):
    """
    STAGE 2: Multi-modal Creative Direction.
    Decides the path to 'Commercial Readiness' based on Subject visibility.
    """
    system_instruction = """
    You are a Senior Creative Director specializing in 2026 identity-driven commerce.
    Your objective: Select the most MARKETABLE visual direction. 

    CRITICAL MARKETABILITY FILTER:
    A trend is only 'Marketable' if a customer would pay to wear it as a signal of identity.
    - If a meme is funny but visually 'trashy' (low-res, cluttered, ugly), it is UNMARKETABLE as a raw image.
    - If a trend is narrative-driven but lacks a clean icon, you MUST choose REGEN to create a professional graphic that represents that narrative.
    - Do NOT output 'MEME' or 'CLEAN' for something that looks like a low-effort internet scrap.

    STRICT DECISION LOGIC:

    1) REGEN — THE DEFAULT FOR QUALITY:
    - Use this for most of the trends to ensure a 'High-End' shop aesthetic.
    - MANDATORY IF: The raw image is unmarketable, cluttered, or looks 'cheap.'
    - REGEN PROMPT: Must create a 'Flat vector illustration' or 'Die-cut sticker' that distills the Subject's DNA into a professional motif.
    - STYLE: Focus on 'Sticker Art,' 'Minimalist Vector,' or 'Iconic Emblem.'

    2) MEME — USE ONLY IF:
    - The narrative is the EXCLUSIVE value of the product and the original image is iconic enough to be wearable.
    - If the raw image is not marketable, switch to REGEN and describe a graphic that 'represents' the meme.

    3) CLEAN — USE ONLY IF:
    - The image is ALREADY professional-grade (e.g., a high-res 2026 character render or official-looking logo).
    - If it's a 'funny photo' from X or Reddit, it is likely NOT CLEAN enough for POD.
    - Do NOT include images that just display generic text. They need recognizable imagery.

    VISUAL DNA PRESERVATION:
    Every REGEN prompt must include the SUBJECT and the CULTURAL ANCHOR (e.g., 'Umbrella Corp logo from Resident Evil') to ensure the product remains recognizable to fans.

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

def distill_theme_terms(client, theme: str):
    """
    STAGE 1 (THEME MODE): Generates 5 motifs locked strictly to a single theme.
    Bypasses trend data entirely — used when the user has specified a theme.
    """
    system_instruction = f"""
    You are a creative director specializing in identity-driven print-on-demand design.

    The user has selected a STRICT THEME: "{theme}"

    Your ONLY job is to generate exactly 5 specific, visually rich motifs that belong
    exclusively to this theme. Do NOT pull in unrelated trends, current events, or 
    anything outside this theme.

    RULES:
    - Every single motif MUST be directly and obviously part of "{theme}".
    - Motifs must have clear Visual DNA: a recognizable character, symbol, or icon 
      that fans of "{theme}" would immediately identify.
    - Motifs must be wearable as identity signals (graphic tee / sticker aesthetic).
    - Be specific — not just "{theme}" but a specific character, arc, moment, or symbol within it.
    - TERM must be 2–8 words. No quotes, no colons, no special characters.

    OUTPUT FORMAT (exactly 5 lines, no extra text):
    TERM: [specific motif name] | SUBJECT: [specific character/icon/symbol] | CONTEXT: [one sentence visual description]
    """

    response = client.models.generate_content(
        model="gemini-2.0-flash",
        config=types.GenerateContentConfig(system_instruction=system_instruction),
        contents=f'Generate 5 motifs for the theme: "{theme}"',
    )

    raw_results = re.findall(
        r"TERM:\s*(.*?)\s*\|\s*SUBJECT:\s*(.*?)\s*\|\s*CONTEXT:\s*(.*)", response.text
    )

    clean_trends = []
    for r in raw_results:
        raw_term = r[0].strip()
        clean_term = re.sub(r'[\\/*?:"<>|*]', "", raw_term)
        clean_trends.append({
            "term": clean_term,
            "subject": r[1].strip(),
            "context": r[2].strip(),
        })

    return clean_trends[:5]


def generate_single_regen(client, term: str, subject: str, context: str, user_prompt: str = "") -> str:
    """
    Generates a detailed REGEN image prompt for a single design.
    Returns the prompt string to pass directly to generate_images().
    """
    extra = f" Additional direction: {user_prompt.strip()}." if user_prompt.strip() else ""
    system_instruction = """
    You are a senior art director writing a single Imagen generation prompt.
    Output ONLY the prompt text — no labels, no explanation, no markdown.
    The prompt must describe a flat vector illustration or die-cut sticker 
    suitable for print-on-demand. It must be detailed, specific, and 
    reference the subject's Visual DNA so a fan would instantly recognize it.
    End with: flat vector illustration, die-cut sticker style, white background,
    high contrast, no text.
    """

    response = client.models.generate_content(
        model="gemini-2.0-flash",
        config=types.GenerateContentConfig(system_instruction=system_instruction),
        contents=(
            f"Subject: {subject}\n"
            f"Term: {term}\n"
            f"Context: {context}\n"
            f"{extra}\n"
            "Write the image generation prompt."
        ),
    )
    return response.text.strip()