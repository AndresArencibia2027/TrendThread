"""Server-side Gemini / Vertex AI trend engine.

This is the new default trend source. It replaces the legacy scraping pipeline
(X/Twitter, GDELT, BigQuery, KnowYourMeme, SerpApi). It is intended to be
called only from the server (API routes / cron), never from a browser.

It reuses the existing Vertex AI auth used for image generation and returns a
structured JSON object describing merch opportunities, upcoming events, viral
trends, evergreen ideas, high-risk trends and an immediate action plan.
"""

import json
import re
from datetime import date
from typing import Any

from . import config

# --- Scan types -------------------------------------------------------------

SCAN_MANUAL = "manual"
SCAN_DAILY = "daily"
SCAN_WEEKLY = "weekly"
VALID_SCAN_TYPES = {SCAN_MANUAL, SCAN_DAILY, SCAN_WEEKLY}


# --- Client -----------------------------------------------------------------

def get_client():
    """Gemini client. Prefers GOOGLE_API_KEY; falls back to Vertex service account.

    The ``google-genai`` SDK is imported lazily so read-only paths (dashboard,
    GET /api/trends/events) work even where the SDK/credentials are absent.
    """
    from google import genai

    api_key = config.get_api_key()
    if api_key:
        return genai.Client(api_key=api_key)

    project = config.get_project_id()
    if not project:
        raise ValueError(
            "No Gemini credentials configured. Set GOOGLE_API_KEY or "
            "GOOGLE_CLOUD_PROJECT + GOOGLE_APPLICATION_CREDENTIALS in .env"
        )
    return genai.Client(
        vertexai=True,
        project=project,
        location=config.get_location(),
    )


# --- Prompting --------------------------------------------------------------

_IP_SAFETY_RULES = """
IP / LEGAL SAFETY (NON-NEGOTIABLE):
You are designing print-on-demand merch (mostly T-shirts) for an Etsy shop.
NEVER recommend products that reproduce or directly reference protected IP.
Treat the following as UNSAFE and never put them in a design or in SEO keywords:
- Movie, show, game, or franchise names
- Character names (real or fictional)
- Celebrity, athlete, musician, or influencer names and likenesses
- Sports teams, leagues, tournaments, and their names/logos
- Song lyrics, band names, album names
- Company logos, brand names, official slogans
- Copied meme images / screenshots

INSTEAD: transform every risky cultural trend into a SAFE, generic, original
"vibe" or "energy" angle that captures the feeling without naming the IP.
Examples of the transformation you MUST perform:
- Unsafe: "Spider-Man shirt"        -> Safer: "web-slinging summer energy"
- Unsafe: "FIFA World Cup shirt"    -> Safer: "world soccer summer"
- Unsafe: "Taylor Swift Eras"       -> Safer: "friendship bracelet concert season"
- Unsafe: "Stranger Things"         -> Safer: "80s small-town mystery vibes"

Assign legal_risk honestly: Low / Medium / High / Very High.
Any opportunity that still leans on protected IP after transformation must be
marked High or Very High and flagged requires_manual_review = true. Prefer to
Skip or Transform rather than Act Now on anything risky.
"""

_OUTPUT_CONTRACT = """
Return ONLY a single JSON object (no markdown fences, no commentary) with this shape:

{
  "scan_type": "<manual|daily|weekly>",
  "merch_opportunities": [        // TOP 5-10, ranked best-first
    {
      "trend_event_name": "string",
      "category": "string",
      "event_date": "YYYY-MM-DD or null",
      "trend_type": "event | viral | evergreen",
      "is_major_event": true|false,
      "target_buyer": "string",
      "safe_design_angle": "string (generic, IP-safe)",
      "unsafe_terms_to_avoid": ["string", ...],
      "design_concepts": ["string", ...],
      "etsy_seo_keywords": ["string", ...],
      "legal_risk": "Low | Medium | High | Very High",
      "saturation_risk": "Low | Medium | High",
      "trend_score": 0-100,
      "recommended_action": "Act Now | Monitor | Skip"
    }
  ],
  "upcoming_events": [            // predictable calendar events worth planning
    { "trend_event_name": "...", "category": "...", "event_date": "YYYY-MM-DD",
      "trend_type": "event", "is_major_event": true, "target_buyer": "...",
      "safe_design_angle": "...", "unsafe_terms_to_avoid": [...],
      "design_concepts": [...], "etsy_seo_keywords": [...],
      "legal_risk": "...", "saturation_risk": "...", "trend_score": 0-100,
      "recommended_action": "..." }
  ],
  "viral_trends_to_monitor": [   // fast-moving, short shelf life
    { ...same fields, trend_type: "viral", event_date: null... }
  ],
  "evergreen_ideas": [           // always-sellable, low-risk
    { ...same fields, trend_type: "evergreen", event_date: null... }
  ],
  "high_risk_trends_to_avoid": [ // explicitly risky; show the safe transform
    { "trend_event_name": "...", "why_risky": "...", "safe_alternative": "...",
      "legal_risk": "High | Very High", "unsafe_terms_to_avoid": [...] }
  ],
  "immediate_action_plan": ["short actionable step", ...]
}

Every item in merch_opportunities, upcoming_events, viral_trends_to_monitor and
evergreen_ideas MUST include ALL the fields shown above.
"""


def _daily_focus() -> str:
    return (
        "FOCUS: current and viral trends RIGHT NOW. Prioritize fast-moving cultural "
        "moments, memes-as-vibes, seasonal micro-trends and anything spiking today. "
        "Most opportunities should be trend_type 'viral' with a short action window. "
        "Set event_date to null for viral items."
    )


def _weekly_focus() -> str:
    return (
        "FOCUS: an upcoming-event calendar and production planning for the next "
        "3-12 months. Prioritize predictable, dateable events (seasons, holidays, "
        "recurring cultural moments, weather/lifestyle seasons) as trend_type "
        "'event' with is_major_event true and a real event_date so the shop can "
        "work backwards. Include a few evergreen ideas. This is strategic planning, "
        "not chasing today's spikes."
    )


def _manual_focus() -> str:
    return (
        "FOCUS: a balanced, on-demand opportunity scan. Mix current viral trends, "
        "near-term predictable events, and evergreen ideas. Rank by overall merch "
        "potential (demand vs. saturation vs. legal risk)."
    )


_FOCUS = {SCAN_DAILY: _daily_focus, SCAN_WEEKLY: _weekly_focus, SCAN_MANUAL: _manual_focus}


def build_system_instruction(scan_type: str) -> str:
    return f"""
You are the TrendThread Merch Opportunity Engine: a strategist for a
print-on-demand Etsy shop. Today's date is {date.today().isoformat()}.

Your job: identify the highest-potential, LEGALLY SAFE merch opportunities and
turn cultural momentum into original, sellable designs.

{_FOCUS.get(scan_type, _manual_focus)()}

{_IP_SAFETY_RULES}

QUALITY BAR:
- Favor identity-signaling designs people would actually wear.
- Weigh demand against saturation_risk; a saturated low-margin trend is a Skip.
- trend_score (0-100) should reflect demand x wearability x freshness, penalized
  by saturation and legal risk.

{_OUTPUT_CONTRACT}
""".strip()


# --- Response parsing -------------------------------------------------------

def _extract_json(text: str) -> dict[str, Any]:
    """Robustly pull a JSON object out of the model response."""
    if not text:
        raise ValueError("Empty response from Gemini")
    cleaned = text.strip()
    # Strip ```json ... ``` fences if present.
    fence = re.match(r"^```(?:json)?\s*(.*?)\s*```$", cleaned, re.DOTALL)
    if fence:
        cleaned = fence.group(1).strip()
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        # Fall back to the first {...} balanced-ish blob.
        start = cleaned.find("{")
        end = cleaned.rfind("}")
        if start != -1 and end != -1 and end > start:
            return json.loads(cleaned[start : end + 1])
        raise


def run_scan(scan_type: str, extra_context: str | None = None) -> dict[str, Any]:
    """Call Gemini and return parsed structured trend JSON.

    Args:
        scan_type: one of manual | daily | weekly.
        extra_context: optional free-text steering (e.g. a niche to focus on).

    Returns:
        dict with keys merch_opportunities, upcoming_events, viral_trends_to_monitor,
        evergreen_ideas, high_risk_trends_to_avoid, immediate_action_plan, plus
        a "_raw" key holding the raw model text.
    """
    if scan_type not in VALID_SCAN_TYPES:
        raise ValueError(f"Invalid scan_type {scan_type!r}; must be one of {VALID_SCAN_TYPES}")

    from google.genai import types

    client = get_client()
    system_instruction = build_system_instruction(scan_type)

    user_prompt = f"Run a {scan_type} trend scan and return the JSON object."
    if extra_context:
        user_prompt += f"\n\nAdditional focus from the operator:\n{extra_context}"

    response = client.models.generate_content(
        model=config.GEMINI_TREND_MODEL,
        config=types.GenerateContentConfig(
            system_instruction=system_instruction,
            response_mime_type="application/json",
            temperature=0.7,
        ),
        contents=user_prompt,
    )

    raw_text = response.text or ""
    data = _extract_json(raw_text)
    data["_raw"] = raw_text
    data.setdefault("scan_type", scan_type)
    # Guarantee the collection keys exist so downstream code is simple.
    for key in (
        "merch_opportunities",
        "upcoming_events",
        "viral_trends_to_monitor",
        "evergreen_ideas",
        "high_risk_trends_to_avoid",
        "immediate_action_plan",
    ):
        data.setdefault(key, [])
    return data
