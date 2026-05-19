"""
ip_screener.py
==============
Two-stage copyright / IP risk filter for TrendThread.

Stage 1 — Pre-generation (prompt level):
    assess_ip_risk(term, subject) -> "HIGH" | "MEDIUM" | "LOW"
    Called before an image is generated. HIGH-risk prompts are rewritten
    to use archetype language instead of specific IP.

Stage 2 — Post-generation (image level):
    screen_generated_image(client, image_path) -> dict
    Called after an image is saved. Results are stored in the DB so the
    review UI can surface a warning banner for MEDIUM/HIGH items.
"""

import json
import re
from pathlib import Path

# ---------------------------------------------------------------------------
# Franchise / trademark blocklist
# Extend this list as needed. Keys are lowercase match strings.
# ---------------------------------------------------------------------------
HIGH_RISK_FRANCHISES = {
    # Animation / Film studios
    "disney", "pixar", "dreamworks", "studio ghibli", "ghibli",
    # Marvel / DC
    "marvel", "dc comics", "spider-man", "spiderman", "batman", "superman",
    "iron man", "avengers", "x-men", "deadpool", "wolverine",
    # Nintendo
    "nintendo", "mario", "zelda", "pokemon", "pikachu", "kirby",
    "splatoon", "metroid", "donkey kong",
    # Other game publishers
    "capcom", "konami", "bandai", "namco", "square enix", "final fantasy",
    "resident evil", "street fighter", "sonic", "sega",
    # Anime with strict enforcement
    "dragon ball", "one piece", "naruto", "demon slayer", "attack on titan",
    "my hero academia", "jujutsu kaisen", "bleach", "fullmetal alchemist",
    # Sports leagues
    "nfl", "nba", "mlb", "nhl", "mls", "fifa", "uefa", "premier league",
    # Music / entertainment
    "bts", "blackpink", "disney+",
    # Other major IP
    "star wars", "harry potter", "lord of the rings", "game of thrones",
    "stranger things", "the simpsons", "family guy", "south park",
}

# These terms are risky but not automatic blocks — flag for human review
MEDIUM_RISK_TERMS = {
    "logo", "mascot", "jersey", "uniform", "seal", "emblem",
    "official", "licensed", "trademark", "registered",
}


def assess_ip_risk(term: str, subject: str) -> tuple[str, str]:
    """
    Pre-generation IP risk assessment based on term and subject text alone.

    Returns:
        (risk_level, reason)
        risk_level: "HIGH" | "MEDIUM" | "LOW"
        reason: human-readable explanation string, or empty string for LOW
    """
    combined = f"{term} {subject}".lower()

    for franchise in HIGH_RISK_FRANCHISES:
        if franchise in combined:
            return (
                "HIGH",
                f"Contains protected franchise reference: '{franchise}'. "
                "Prompt will be rewritten to use archetype language.",
            )

    for medium_term in MEDIUM_RISK_TERMS:
        if medium_term in combined:
            return (
                "MEDIUM",
                f"Contains potentially trademark-sensitive term: '{medium_term}'. "
                "Flagged for human review before publishing.",
            )

    return ("LOW", "")


def rewrite_prompt_for_ip_safety(term: str, subject: str, context: str) -> dict:
    """
    For HIGH-risk terms, rewrites term/subject/context to use archetype /
    aesthetic language rather than specific IP names.

    This is a rule-based rewrite — fast, no LLM call needed.
    The result is passed back into the prompt builder instead of the original.

    Returns a dict with keys: term, subject, context
    """
    # Strip known franchise names from the subject
    clean_subject = subject
    for franchise in HIGH_RISK_FRANCHISES:
        pattern = re.compile(re.escape(franchise), re.IGNORECASE)
        clean_subject = pattern.sub("", clean_subject).strip()

    # Describe the aesthetic rather than the specific character
    archetype_context = (
        f"Visual archetype inspired by: {context}. "
        "Design must NOT reproduce any specific copyrighted character, logo, or trademark. "
        "Use symbolic, abstract, or genre-representative imagery only."
    )

    clean_term = re.sub(
        "|".join(re.escape(f) for f in HIGH_RISK_FRANCHISES),
        "",
        term,
        flags=re.IGNORECASE,
    ).strip(" -–—,")

    return {
        "term": clean_term or "cultural motif",
        "subject": clean_subject or "stylized character silhouette",
        "context": archetype_context,
    }


def screen_generated_image(client, image_path: str) -> dict:
    """
    Post-generation image screen using Gemini Vision.

    Analyzes the generated image for recognizable copyrighted characters,
    logos, trademarks, real person likenesses, and embedded text.

    Args:
        client: Initialized Gemini genai client
        image_path: Absolute path to the generated image file

    Returns:
        {
            "contains_recognizable_character": bool,
            "contains_logo_or_trademark": bool,
            "contains_real_person_likeness": bool,
            "contains_text": bool,
            "risk_level": "HIGH" | "MEDIUM" | "LOW",
            "risk_reason": str | None,
        }
        On any failure, returns a MEDIUM-risk result so the item is
        flagged for human review rather than silently passed or blocked.
    """
    # Import here to avoid circular imports at module load time
    from google.genai import types

    FALLBACK = {
        "contains_recognizable_character": False,
        "contains_logo_or_trademark": False,
        "contains_real_person_likeness": False,
        "contains_text": False,
        "risk_level": "MEDIUM",
        "risk_reason": "Screening failed — flagged for manual review.",
    }

    if not Path(image_path).exists():
        return {**FALLBACK, "risk_reason": f"Image file not found: {image_path}"}

    system_instruction = """
    You are an IP risk screener for a print-on-demand merchandise business.

    Analyze the provided image carefully and return a JSON object — nothing else.
    No markdown, no code fences, no explanation outside the JSON.

    JSON schema:
    {
        "contains_recognizable_character": <true if the image contains a character
            that is clearly recognizable as belonging to a specific copyrighted
            franchise, game, film, anime, or TV show — even if stylized>,
        "contains_logo_or_trademark": <true if the image contains any logo, wordmark,
            emblem, or symbol that belongs to a brand, sports team, or franchise>,
        "contains_real_person_likeness": <true if the image contains a realistic
            or clearly recognizable depiction of a real, named public figure>,
        "contains_text": <true if the image contains any readable text, words,
            letters, or numbers>,
        "risk_level": <"HIGH" if any of the above are true and would likely result
            in a DMCA takedown or legal challenge; "MEDIUM" if ambiguous and needs
            human review; "LOW" if clearly safe to publish>,
        "risk_reason": <one concise sentence describing the specific risk, or null
            if risk_level is LOW>
    }

    Be conservative: when in doubt, rate MEDIUM rather than LOW.
    """

    try:
        with open(image_path, "rb") as f:
            img_bytes = f.read()

        img_part = types.Part.from_bytes(data=img_bytes, mime_type="image/png")

        response = client.models.generate_content(
            model="gemini-2.0-flash",
            config=types.GenerateContentConfig(system_instruction=system_instruction),
            contents=[img_part, "Screen this image for IP and copyright risk."],
        )

        raw = response.text.strip()
        # Strip markdown fences if the model adds them despite instructions
        raw = re.sub(r"^```[a-z]*\n?", "", raw)
        raw = re.sub(r"\n?```$", "", raw)

        result = json.loads(raw)

        # Validate required keys are present
        required = {
            "contains_recognizable_character",
            "contains_logo_or_trademark",
            "contains_real_person_likeness",
            "contains_text",
            "risk_level",
            "risk_reason",
        }
        if not required.issubset(result.keys()):
            raise ValueError(f"Missing keys in screener response: {result}")

        # Normalise risk_level to uppercase
        result["risk_level"] = str(result.get("risk_level", "MEDIUM")).upper()
        if result["risk_level"] not in {"HIGH", "MEDIUM", "LOW"}:
            result["risk_level"] = "MEDIUM"

        return result

    except json.JSONDecodeError as e:
        return {**FALLBACK, "risk_reason": f"Screener JSON parse error: {e}"}
    except Exception as e:
        return {**FALLBACK, "risk_reason": f"Screener error: {e}"}