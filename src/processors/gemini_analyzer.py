import os
import re
from google import genai
from google.genai import types

try:
    import pandas as pd
except ImportError:
    pd = None

def get_client():
    """Initializes the unified Vertex AI client via Service Account."""
    return genai.Client(
        vertexai=True,
        project=os.getenv("VERTEX_PROJECT_ID"),
        location=os.getenv("VERTEX_LOCATION", "us-central1")
    )

def _format_bq_data(bq_context):
    if not bq_context: return "No search trend data available."
    return "\n".join([f"- {item['term']} (Momentum: {item['momentum']})" for item in bq_context])

def _format_gdelt_data(gdelt_context):
    if not gdelt_context: return "No news coverage data available."
    return "\n".join([f"- {art.get('title')} ({art.get('source')})" for art in gdelt_context[:15]])

def _load_tweets(excel_path):
    if not excel_path or not os.path.exists(excel_path): return "No social media data available."
    df = pd.read_excel(excel_path, engine="openpyxl")
    text_col = next((c for c in df.columns if "text" in str(c).lower()), df.columns[0])
    return "\n".join([f"- {t[:200]}" for t in df[text_col].astype(str).tolist()[:100]])

def analyze_trends_and_generate_prompts(client, bq_context, gdelt_context, excel_path):
    bq_str = _format_bq_data(bq_context)
    news_str = _format_gdelt_data(gdelt_context)
    social_str = _load_tweets(excel_path)

    system_instruction = """
    You are a Technical Graphic Illustrator. Your job is to create RAW 2D ASSETS for print.
    
    STRICT RULES:
    1. NO MOCKUPS: Do not mention shirts, hoodies, mugs, or models.
    2. NO 3D: No fabric folds, shadows on surfaces, or realistic lighting.
    3. FLAT ART ONLY: Describe graphics as if they are Adobe Illustrator vector files.
    4. IP SAFETY: Extract the 'Vibe' of a trend (e.g., 'The Penguin' -> Noir avian silhouette) without using names or logos.
    """

    user_prompt = f"""
    Analyze these trends:
    SEARCH: {bq_str}
    NEWS: {news_str}
    SOCIAL: {social_str}

    TASK:
    1. Write a 3-sentence TREND ANALYSIS (Emotional Driver, Visual Motifs, Commercial Angle).
    2. Write 5 IMAGE PROMPTS.

    PROMPT STYLE GUIDE:
    - Focus: Specific icons, centered compositions, bold outlines.
    - Style: "Minimalist flat vector icon," "High-contrast linework," or "90s Risograph texture."
    - Background: Always specify "Isolated on a solid white background."
    - Specificity: Use distinct iconography (e.g., instead of 'nostalgia', use 'A pixelated 8-bit handheld console heart').

    OUTPUT FORMAT:
    ---TREND ANALYSIS---
    [Summary]
    ---PROMPT 1---
    [Describe the raw graphic only]
    ...up to ---PROMPT 5---
    """

    print(" \nSynthesizing trends...")
    response = client.models.generate_content(
        model="gemini-2.0-flash", 
        config=types.GenerateContentConfig(system_instruction=system_instruction),
        contents=user_prompt
    )
    
    full_text = response.text
    analysis = re.search(r"---TREND ANALYSIS---\s*(.*?)(?=---PROMPT 1---)", full_text, re.DOTALL | re.IGNORECASE)
    analysis_text = analysis.group(1).strip() if analysis else "Synthesis complete."

    prompts = [re.search(rf"---PROMPT {i}---\s*(.*?)(?=---PROMPT \d+---|\Z)", full_text, re.DOTALL | re.IGNORECASE).group(1).strip() 
               for i in range(1, 6) if re.search(rf"---PROMPT {i}---\s*(.*?)(?=---PROMPT \d+---|\Z)", full_text, re.DOTALL | re.IGNORECASE)]

    return analysis_text, prompts