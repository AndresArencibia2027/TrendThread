"""
gemini_analyzer.py
==================
Stage 1 — Strategic Curation:   distill_search_terms / distill_theme_terms
Stage 2 — Creative Direction:   analyze_visual_strategy
Stage 3 — Prompt Engineering:   build_imagen_prompt / generate_single_regen

Key improvements over v1:
- ImagenPrompt dataclass replaces freeform prompt strings. Gemini fills
  structured fields; the prompt is assembled deterministically.
- TOPIC_TYPE classification drives generation strategy (fictional character,
  viral phrase, real person, sporting event, abstract meme).
- IP pre-screening via ip_screener.assess_ip_risk before prompts are built.
- distill_theme_terms for strict theme-locked generation.
- generate_single_regen for the per-image regenerate endpoint.
"""

import os
import re
from dataclasses import dataclass, field
from typing import Optional

from google.genai import types
from google import genai

from src.processors.ip_screener import assess_ip_risk, rewrite_prompt_for_ip_safety

try:
    import pandas as pd
except ImportError:
    pd = None


# ---------------------------------------------------------------------------
# Topic types — drive generation strategy in asset_engine
# ---------------------------------------------------------------------------
TOPIC_TYPES = {
    "FICTIONAL_CHARACTER",  # anime, game, film character → always REGEN, archetype only
    "VIRAL_PHRASE",         # text/quote-driven → typography-first generation
    "REAL_PERSON",          # celebrity, athlete → abstract motif only, never likeness
    "SPORTING_EVENT",       # sport/team moment → generic iconography, avoid trademarks
    "ABSTRACT_MEME",        # format/concept meme → REGEN visual metaphor
    "GENERAL",              # catch-all
}


# ---------------------------------------------------------------------------
# Structured prompt schema
# ---------------------------------------------------------------------------
@dataclass
class ImagenPrompt:
    """
    Structured Imagen prompt. Gemini fills each field; the final prompt
    string is assembled by build() in a consistent, testable way.
    """
    subject: str                          # "white-haired anime fighter, six eyes glowing blue"
    style: str = "flat vector illustration"
    mood: str = "bold, high contrast"
    composition: str = "centered subject, isolated, no background"
    color_palette: str = "black, white, with vivid accent color"
    topic_type: str = "GENERAL"
    negative: str = (
        "t-shirt mockup, hoodie, mannequin, model wearing, fabric folds, "
        "3d render, photo, realistic shirt, watermark, signature, copyright symbol, "
        "blurry, low quality, cluttered background, text overlay"
    )

    def build(self) -> str:
        return (
            f"{self.subject}, "
            f"{self.style}, "
            f"{self.mood}, "
            f"{self.composition}, "
            f"color palette: {self.color_palette}, "
            "die-cut sticker, white background, no text, no watermark, "
            "high detail, print-ready"
        )

    def negative_prompt(self) -> str:
        return self.negative


# ---------------------------------------------------------------------------
# Client factory
# ---------------------------------------------------------------------------
def get_client():
    """Initializes the Vertex AI client."""
    return genai.Client(
        vertexai=True,
        project=os.getenv("VERTEX_PROJECT_ID"),
        location=os.getenv("VERTEX_LOCATION", "us-central1"),
    )


# ---------------------------------------------------------------------------
# Internal formatters (unchanged from v1)
# ---------------------------------------------------------------------------
def _format_bq_data(bq_context):
    if not bq_context:
        return "No search trend data available."
    return "\n".join(
        [f"- {item['term']} (Velocity: {item.get('velocity', item.get('momentum', 'N/A'))})"
         for item in bq_context]
    )


def _format_gdelt_data(gdelt_context):
    if not gdelt_context:
        return "No news coverage data available."
    return "\n".join(
        [f"- {art.get('title')} (Source: {art.get('source', 'N/A')})"
         for art in gdelt_context[:10]]
    )


def _format_kym_data(kym_context):
    if not kym_context:
        return "No confirmed meme data available."
    return "\n".join([f"- {item['title']} (Confirmed Motif)" for item in kym_context])


def _load_tweets(excel_path):
    if not excel_path or not os.path.exists(excel_path):
        return "No social media data available."
    if pd is None:
        return "Pandas not installed."
    df = pd.read_excel(excel_path, engine="openpyxl")
    text_col = next((c for c in df.columns if "text" in str(c).lower()), df.columns[0])
    return "\n".join([f"- {t[:150]}" for t in df[text_col].astype(str).tolist()[:30]])


def _prepare_image_part(image_path):
    try:
        with open(image_path, "rb") as f:
            img_data = f.read()
        return types.Part.from_bytes(data=img_data, mime_type="image/jpeg")
    except Exception as e:
        print(f"       Failed to load image {image_path}: {e}")
        return None


# ---------------------------------------------------------------------------
# Stage 1a — Trend-driven curation (no theme)
# ---------------------------------------------------------------------------
def distill_search_terms(client, bq_context, gdelt_context, excel_path, kym_context=[]):
    """
    STAGE 1: Strategic Curation.
    Extracts 5 viral motifs from trend data and enriches with cultural anchors.
    Now outputs TOPIC_TYPE for each motif to drive downstream strategy.
    """
    bq_str = _format_bq_data(bq_context)
    news_str = _format_gdelt_data(gdelt_context)
    social_str = _load_tweets(excel_path)
    kym_str = _format_kym_data(kym_context)

    system_instruction = """
    You are a 2026 Trend Signal Extraction Engine.

    Your objective: Output 5 REAL, SEARCH-VALIDATABLE viral trend motifs from 2026.

    CORE PRINCIPLE: RETRIEVAL PRECISION
    Each TERM must be specific enough to return the correct visual cluster
    when pasted into Google, X, or TikTok.

    CULTURAL ANCHOR REQUIREMENT
    If the motif could refer to multiple things, append a disambiguator:
    franchise name, artist name, brand, event name, or platform hashtag.

    MARKETABILITY FILTER (THE WEARABILITY TEST)
    - Ask: "Would a customer wear this to express identity?"
    - Discard: Minor memes, local politics, utility trends, 2025 holdovers.
    - Favor: Specific icons, characters, or Visual DNA that signals cultural alignment.
    - NEVER include copyrighted brand names (BTS, Disney, etc.) in the TERM.

    TOPIC_TYPE CLASSIFICATION
    Classify each motif as exactly one of:
    - FICTIONAL_CHARACTER — an anime, game, or film character/icon
    - VIRAL_PHRASE — a quote, caption, or text-driven meme
    - REAL_PERSON — a celebrity, athlete, or public figure
    - SPORTING_EVENT — a sports moment, team achievement, or athletic trend
    - ABSTRACT_MEME — a format, concept, or reaction meme without a specific character
    - GENERAL — anything that doesn't fit the above

    NO-TRASH PROTOCOL
    1. Prioritize motifs appearing across BQ + GDELT + Social + KYM.
    2. Discard: local politics, radio visits, minor crime, corporate earnings,
       generic AI commentary, 2025 holdovers, eclipse/blood moon type utility trends.
    3. HIGH-SIGNAL: identity signaling potential, clear visual DNA, replication behavior.
    4. NO FABRICATION: do not invent trends outside the provided datasets.

    SOURCE HIERARCHY
    1. PRIMARY: BigQuery velocity scores
    2. SECONDARY: GDELT & Social for cross-platform momentum
    3. TERTIARY: KYM for Visual DNA context only

    OUTPUT FORMAT (exactly 5 lines):
    TERM: [term] | SUBJECT: [specific icon/character] | CONTEXT: [narrative] | TOPIC_TYPE: [type]
    """

    prompt = f"""
    PRIORITY 1 - BQ (SEARCH VELOCITY):
    {bq_str}

    PRIORITY 2 - MEDIA & SOCIAL (MOMENTUM):
    NEWS: {news_str}
    SOCIAL: {social_str}

    PRIORITY 3 - KYM (VISUAL CONTEXT):
    {kym_str}
    """

    response = client.models.generate_content(
        model="gemini-2.0-flash",
        config=types.GenerateContentConfig(system_instruction=system_instruction),
        contents=prompt,
    )

    raw_results = re.findall(
        r"TERM:\s*(.*?)\s*\|\s*SUBJECT:\s*(.*?)\s*\|\s*CONTEXT:\s*(.*?)\s*\|\s*TOPIC_TYPE:\s*(\w+)",
        response.text,
    )

    clean_trends = []
    for r in raw_results:
        raw_term = r[0].strip()
        clean_term = re.sub(r'[\\/*?:"<>|*]', "", raw_term)
        topic_type = r[3].strip().upper()
        if topic_type not in TOPIC_TYPES:
            topic_type = "GENERAL"
        clean_trends.append({
            "term": clean_term,
            "subject": r[1].strip(),
            "context": r[2].strip(),
            "topic_type": topic_type,
        })

    return clean_trends[:5]


# ---------------------------------------------------------------------------
# Stage 1b — Theme-locked curation
# ---------------------------------------------------------------------------
def distill_theme_terms(client, theme: str):
    """
    STAGE 1 (THEME MODE): Generates 5 motifs locked strictly to a single theme.
    Bypasses trend data entirely. Used when the user specifies a theme.
    Also outputs TOPIC_TYPE for downstream strategy routing.
    """
    system_instruction = f"""
    You are a creative director specializing in identity-driven print-on-demand design.

    The user has selected a STRICT THEME: "{theme}"

    RULES — read carefully:
    1. Every motif MUST be directly and obviously part of "{theme}". No exceptions.
    2. Be specific — not just "{theme}" as a whole, but a specific character,
       arc, moment, symbol, or icon within it that fans would immediately recognize.
    3. Motifs must be wearable as identity signals (graphic tee / sticker).
    4. TERM must be 2–8 words. No quotes, colons, or special characters.
    5. Do NOT include any copyrighted brand or franchise name in the TERM field itself.
       The SUBJECT and CONTEXT may describe the IP for internal use.
    6. Classify each motif with a TOPIC_TYPE (see below).

    TOPIC_TYPE options:
    - FICTIONAL_CHARACTER — a character, creature, or icon from the theme's universe
    - VIRAL_PHRASE — a famous quote or text from the theme
    - REAL_PERSON — a real athlete, celebrity, or creator associated with the theme
    - SPORTING_EVENT — a sports moment within the theme
    - ABSTRACT_MEME — a format or reaction meme from the theme's fandom
    - GENERAL — anything else within the theme

    OUTPUT FORMAT (exactly 5 lines, no extra text):
    TERM: [specific motif] | SUBJECT: [specific character/icon] | CONTEXT: [one sentence] | TOPIC_TYPE: [type]
    """

    response = client.models.generate_content(
        model="gemini-2.0-flash",
        config=types.GenerateContentConfig(system_instruction=system_instruction),
        contents=f'Generate 5 motifs for the theme: "{theme}"',
    )

    raw_results = re.findall(
        r"TERM:\s*(.*?)\s*\|\s*SUBJECT:\s*(.*?)\s*\|\s*CONTEXT:\s*(.*?)\s*\|\s*TOPIC_TYPE:\s*(\w+)",
        response.text,
    )

    clean_trends = []
    for r in raw_results:
        raw_term = r[0].strip()
        clean_term = re.sub(r'[\\/*?:"<>|*]', "", raw_term)
        topic_type = r[3].strip().upper()
        if topic_type not in TOPIC_TYPES:
            topic_type = "GENERAL"
        clean_trends.append({
            "term": clean_term,
            "subject": r[1].strip(),
            "context": r[2].strip(),
            "topic_type": topic_type,
        })

    return clean_trends[:5]


# ---------------------------------------------------------------------------
# Stage 2 — Multi-modal creative direction
# ---------------------------------------------------------------------------
def analyze_visual_strategy(client, trend_visuals_map, trend_data):
    """
    STAGE 2: Multi-modal Creative Direction.
    Now outputs structured fields including TOPIC_TYPE passthrough and
    a structured PROMPT_SCHEMA for downstream ImagenPrompt assembly.

    Decision logic per TOPIC_TYPE:
    - FICTIONAL_CHARACTER → always REGEN, archetype language, no specific IP names
    - VIRAL_PHRASE        → REGEN with typography-first prompt
    - REAL_PERSON         → REGEN with abstract/symbolic motif, never likeness
    - SPORTING_EVENT      → REGEN with generic sport iconography
    - ABSTRACT_MEME       → MEME if raw image is iconic, else REGEN visual metaphor
    - GENERAL             → standard CLEAN / MEME / REGEN logic
    """
    system_instruction = """
    You are a Senior Creative Director for a 2026 print-on-demand shop.

    For each trend you will receive: TERM, SUBJECT, CONTEXT, TOPIC_TYPE,
    and 1-5 reference images from Google.

    DECISION RULES BY TOPIC_TYPE:

    FICTIONAL_CHARACTER:
      → Always REGEN. Never use the character's real name in the prompt.
        Use archetype/aesthetic description ("silver-haired anime sorcerer,
        domain expansion motif") to avoid direct IP reproduction.

    VIRAL_PHRASE:
      → REGEN with a typography-first prompt. The text/phrase IS the product.
        Describe a bold lettering treatment with a supporting graphic element.

    REAL_PERSON:
      → Always REGEN. Never depict their likeness. Create a symbolic or
        abstract motif that represents their cultural impact instead.

    SPORTING_EVENT:
      → REGEN with generic sport iconography (silhouettes, trophies, stadium
        abstraction). Never use team logos, uniforms, or league marks.

    ABSTRACT_MEME:
      → MEME only if the source image is genuinely iconic and public-domain-safe.
        Otherwise REGEN a visual metaphor that captures the meme's energy.

    GENERAL:
      → Standard logic: CLEAN if already professional-grade, MEME only if the
        narrative IS the product and image is wearable, REGEN for everything else.

    PROMPT SCHEMA — for every REGEN decision, output these fields:
      SUBJECT: [what to draw, archetype language, no IP names]
      STYLE: [flat vector illustration | minimalist vector | bold graphic | typography]
      MOOD: [e.g. bold high contrast | playful vibrant | dark moody | clean minimal]
      COMPOSITION: [e.g. centered isolated | full bleed | radial symmetry]
      COLOR_PALETTE: [3–4 specific colors or color description]

    OUTPUT FORMAT (one line per trend, strictly):
    TREND: [term] | TOPIC_TYPE: [type] | DECISION: [CLEAN/MEME/REGEN] | SUBJECT: [text] | STYLE: [text] | MOOD: [text] | COMPOSITION: [text] | COLOR_PALETTE: [text] | ACTION: [meme text if MEME, else None] | SOURCE: [best image path or None]
    """

    content_parts = [
        "Analyze these trend groups. Apply TOPIC_TYPE decision rules strictly."
    ]
    for item in trend_data:
        term = item["term"]
        subject = item["subject"]
        context = item["context"]
        topic_type = item.get("topic_type", "GENERAL")

        content_parts.append(
            f"\n--- TREND: {term} ---\n"
            f"SUBJECT: {subject}\n"
            f"CONTEXT: {context}\n"
            f"TOPIC_TYPE: {topic_type}"
        )

        if term in trend_visuals_map:
            for path in trend_visuals_map[term]:
                img_part = _prepare_image_part(path)
                if img_part:
                    content_parts.append(f"Candidate Path: {path}")
                    content_parts.append(img_part)

    response = client.models.generate_content(
        model="gemini-2.0-flash",
        config=types.GenerateContentConfig(system_instruction=system_instruction),
        contents=content_parts,
    )
    return response.text


# ---------------------------------------------------------------------------
# Stage 3a — Structured prompt builder
# ---------------------------------------------------------------------------
def build_imagen_prompt(
    term: str,
    subject: str,
    style: str,
    mood: str,
    composition: str,
    color_palette: str,
    topic_type: str = "GENERAL",
    user_hint: str = "",
) -> ImagenPrompt:
    """
    Constructs an ImagenPrompt from fields parsed out of analyze_visual_strategy.
    Applies IP pre-screening and rewrites HIGH-risk prompts automatically.
    """
    risk_level, risk_reason = assess_ip_risk(term, subject)

    if risk_level == "HIGH":
        print(f"    [IP] HIGH risk detected for '{term}': {risk_reason}")
        rewritten = rewrite_prompt_for_ip_safety(term, subject, mood)
        subject = rewritten["subject"]

    # Typography-first override for viral phrases
    if topic_type == "VIRAL_PHRASE":
        style = "bold typographic poster design, hand-lettered"
        composition = "text-dominant layout, large central lettering"

    # Abstract motif override for real people
    if topic_type == "REAL_PERSON":
        subject = f"symbolic abstract motif representing {subject}, no human face, no likeness"

    prompt = ImagenPrompt(
        subject=f"{subject}{(', ' + user_hint) if user_hint else ''}",
        style=style or "flat vector illustration",
        mood=mood or "bold, high contrast",
        composition=composition or "centered subject, isolated",
        color_palette=color_palette or "black, white, vivid accent",
        topic_type=topic_type,
    )
    return prompt


# ---------------------------------------------------------------------------
# Stage 3b — Single-image regeneration prompt
# ---------------------------------------------------------------------------
def generate_single_regen(
    client,
    term: str,
    subject: str,
    context: str,
    topic_type: str = "GENERAL",
    user_prompt: str = "",
) -> ImagenPrompt:
    """
    Generates a structured ImagenPrompt for a single design regeneration.
    Used by the /api/regenerate endpoint.
    Returns an ImagenPrompt object (call .build() to get the final string).
    """
    extra = f"Additional direction from user: {user_prompt.strip()}." if user_prompt.strip() else ""

    # Apply IP pre-screen before sending to Gemini
    risk_level, risk_reason = assess_ip_risk(term, subject)
    if risk_level == "HIGH":
        rewritten = rewrite_prompt_for_ip_safety(term, subject, context)
        term = rewritten["term"]
        subject = rewritten["subject"]
        context = rewritten["context"]

    system_instruction = """
    You are a senior art director filling a structured prompt schema for an
    Imagen 4 image generation request.

    Output ONLY a JSON object — no markdown, no explanation, no code fences.

    Schema:
    {
        "subject": "<what to draw — archetype language, no trademarked names>",
        "style": "<flat vector illustration | minimalist vector | bold graphic | typographic poster>",
        "mood": "<e.g. bold high contrast | playful vibrant | dark moody | clean minimal>",
        "composition": "<e.g. centered isolated subject | radial symmetry | full bleed>",
        "color_palette": "<3-4 specific colors>"
    }

    The result must describe a wearable print-on-demand design that:
    - Has clear Visual DNA fans of the subject would recognize
    - Is a professional sticker/graphic, not a raw photo or screenshot
    - Contains no copyrighted logos, text, or character names
    """

    response = client.models.generate_content(
        model="gemini-2.0-flash",
        config=types.GenerateContentConfig(system_instruction=system_instruction),
        contents=(
            f"Term: {term}\n"
            f"Subject: {subject}\n"
            f"Context: {context}\n"
            f"Topic type: {topic_type}\n"
            f"{extra}\n"
            "Fill the prompt schema."
        ),
    )

    import json
    raw = response.text.strip()
    raw = re.sub(r"^```[a-z]*\n?", "", raw)
    raw = re.sub(r"\n?```$", "", raw)

    try:
        fields = json.loads(raw)
    except json.JSONDecodeError:
        # Fallback: build a reasonable prompt from what we have
        fields = {
            "subject": subject,
            "style": "flat vector illustration",
            "mood": "bold, high contrast",
            "composition": "centered isolated subject",
            "color_palette": "black, white, vivid accent color",
        }

    return ImagenPrompt(
        subject=fields.get("subject", subject),
        style=fields.get("style", "flat vector illustration"),
        mood=fields.get("mood", "bold, high contrast"),
        composition=fields.get("composition", "centered isolated subject"),
        color_palette=fields.get("color_palette", "black, white, vivid accent color"),
        topic_type=topic_type,
    )