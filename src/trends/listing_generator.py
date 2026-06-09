"""Generate a sellable, IP-safe product listing from a saved event.

Produces:
    - listing copy (title, description, tags) via Gemini, and
    - a product graphic via the existing Imagen image generator.

Everything is anchored on the event's SAFE design angle (never the trademarked
trend name). Unsafe terms are explicitly excluded from the prompt, the title,
the description and the tags.
"""

import json
import os
import re
from datetime import datetime
from typing import Any

from . import config, gemini_engine

LISTING_IMAGE_DIR = os.path.join("output", "listings")


def _slugify(text: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "_", (text or "listing").lower()).strip("_")
    return slug or "listing"


def _build_copy_prompt(event: dict[str, Any]) -> tuple[str, str]:
    unsafe = event.get("legal_risk_check", {}) or {}
    unsafe_terms = unsafe.get("unsafe_terms") or []
    keywords = event.get("seo_keywords") or []
    concepts = event.get("design_ideas") or []

    system = f"""
You write Etsy/print-on-demand listing copy for original, IP-SAFE T-shirt designs.
Today is {datetime.now().date().isoformat()}.

ABSOLUTE RULES:
- Base everything on this SAFE design angle: "{event.get('safe_design_angle') or event.get('name')}".
- NEVER use any trademarked / copyrighted names. Specifically NEVER use any of
  these unsafe terms (or close variants): {json.dumps(unsafe_terms)}.
- No movie/show/game/franchise names, character names, celebrity/athlete names,
  team/league names, song lyrics, brand names, logos or slogans.
- Keep it original and generic-but-evocative (vibe-based), suitable to sell.

Return ONLY JSON:
{{
  "title": "<=140 char SEO title, no trademarked terms",
  "description": "2-4 short paragraphs, friendly, mentions fit/comfort/gift use",
  "tags": ["13 short Etsy tags", "<=20 chars each", "no trademarked terms"]
}}
""".strip()

    user = f"""
Design angle: {event.get('safe_design_angle') or event.get('name')}
Category: {event.get('category')}
Target buyer: {event.get('target_buyer')}
Design concepts: {json.dumps(concepts)}
Suggested SEO keywords (reuse/refine, drop any risky ones): {json.dumps(keywords)}
""".strip()
    return system, user


def generate_listing_copy(event: dict[str, Any]) -> dict[str, Any]:
    """Call Gemini for IP-safe title/description/tags."""
    from google.genai import types

    system, user = _build_copy_prompt(event)
    client = gemini_engine.get_client()
    response = client.models.generate_content(
        model=config.GEMINI_TREND_MODEL,
        config=types.GenerateContentConfig(
            system_instruction=system,
            response_mime_type="application/json",
            temperature=0.8,
        ),
        contents=user,
    )
    data = gemini_engine._extract_json(response.text or "")
    return {
        "title": (data.get("title") or event.get("name") or "Original Graphic Tee").strip(),
        "description": (data.get("description") or "").strip(),
        "tags": [str(t).strip() for t in (data.get("tags") or []) if str(t).strip()][:13],
    }


def _build_image_prompt(event: dict[str, Any]) -> str:
    angle = event.get("safe_design_angle") or event.get("name")
    concepts = event.get("design_ideas") or []
    concept_hint = f" Incorporate: {', '.join(concepts[:3])}." if concepts else ""
    return (
        f"{angle}.{concept_hint} Flat vector illustration, die-cut sticker style, "
        "bold clean shapes, high contrast, centered composition, transparent-friendly "
        "background, original artwork (no logos, no real brands, no copyrighted characters)."
    )


def generate_listing_image(event: dict[str, Any]) -> str | None:
    """Generate one product graphic via the existing Imagen generator."""
    from src.processors.image_generator import generate_five_images

    os.makedirs(LISTING_IMAGE_DIR, exist_ok=True)
    prompt = _build_image_prompt(event)
    out_dir = os.path.join(LISTING_IMAGE_DIR, _slugify(event.get("name", "")))
    paths = generate_five_images(
        project_id=config.get_project_id(),
        location=config.get_location(),
        prompts=[prompt],
        out_dir=out_dir,
    )
    return paths[0] if paths else None


def generate_listing(event: dict[str, Any], with_image: bool = True) -> dict[str, Any]:
    """Full listing generation: copy + (optional) image. Returns a draft dict."""
    copy = generate_listing_copy(event)
    image_path = generate_listing_image(event) if with_image else None
    return {
        "event_id": event.get("id"),
        "title": copy["title"],
        "description": copy["description"],
        "tags": copy["tags"],
        "image_path": image_path,
        "image_prompt": _build_image_prompt(event),
    }
