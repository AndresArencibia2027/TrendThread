"""
asset_engine.py
===============
Manufactures product-ready assets from the visual strategy report.

Key improvements over v1:
- Parses the new structured SUBJECT/STYLE/MOOD/COMPOSITION/COLOR_PALETTE
  fields from analyze_visual_strategy output.
- Routes generation strategy by TOPIC_TYPE rather than just CLEAN/MEME/REGEN.
- Calls ip_screener.screen_generated_image on every output before queuing.
- Attaches risk_level to each asset so the DB and review UI can surface warnings.
- Returns a list of result dicts (path + risk metadata) instead of just saving files.
"""

import os
import shutil
from pathlib import Path
from typing import Optional

from PIL import Image, ImageDraw, ImageFont

from src.processors.gemini_analyzer import build_imagen_prompt, ImagenPrompt
from src.processors.ip_screener import screen_generated_image, assess_ip_risk


# ---------------------------------------------------------------------------
# Topic-type generation strategy table
# ---------------------------------------------------------------------------
# Maps TOPIC_TYPE to the forced DECISION when the visual strategy model's
# recommendation would conflict with IP/quality rules.
TOPIC_FORCE_REGEN = {
    "FICTIONAL_CHARACTER",  # always REGEN — raw images are copyrighted
    "REAL_PERSON",          # always REGEN — never reproduce a likeness
    "SPORTING_EVENT",       # always REGEN — avoid league trademarks
}


def _parse_visual_report(visual_report: str) -> list[dict]:
    """
    Parses the structured output from analyze_visual_strategy into a list
    of dicts, one per trend line.

    Expected line format:
    TREND: x | TOPIC_TYPE: x | DECISION: x | SUBJECT: x | STYLE: x |
    MOOD: x | COMPOSITION: x | COLOR_PALETTE: x | ACTION: x | SOURCE: x
    """
    results = []
    for line in visual_report.strip().split("\n"):
        if "TREND:" not in line or "|" not in line:
            continue
        try:
            parts = {
                p.split(":", 1)[0].strip().upper(): p.split(":", 1)[1].strip()
                for p in line.split("|")
                if ":" in p
            }
            if not parts.get("TREND"):
                continue
            results.append(parts)
        except Exception as e:
            print(f"  [parse] Skipping malformed line: {e}")
    return results


def _force_regen_for_topic(parts: dict) -> dict:
    """
    Overrides DECISION to REGEN for topic types that should never use
    raw images regardless of what analyze_visual_strategy recommended.
    """
    topic_type = parts.get("TOPIC_TYPE", "GENERAL").upper()
    if topic_type in TOPIC_FORCE_REGEN:
        if parts.get("DECISION", "").upper() != "REGEN":
            print(
                f"  [topic] Forcing REGEN for {parts.get('TREND')} "
                f"(TOPIC_TYPE={topic_type}, was {parts.get('DECISION')})"
            )
            parts["DECISION"] = "REGEN"
    return parts


def _build_prompt_from_parts(parts: dict) -> ImagenPrompt:
    """
    Assembles an ImagenPrompt from the structured fields parsed out of
    the visual strategy report.
    """
    return build_imagen_prompt(
        term=parts.get("TREND", ""),
        subject=parts.get("SUBJECT", "stylized graphic motif"),
        style=parts.get("STYLE", "flat vector illustration"),
        mood=parts.get("MOOD", "bold, high contrast"),
        composition=parts.get("COMPOSITION", "centered isolated subject"),
        color_palette=parts.get("COLOR_PALETTE", "black, white, vivid accent"),
        topic_type=parts.get("TOPIC_TYPE", "GENERAL"),
    )


def _screen_and_annotate(
    gemini_client,
    output_path: str,
    skip_screen: bool = False,
) -> dict:
    """
    Runs the post-generation IP screen on a saved image.
    Returns a dict with risk_level and risk_reason.
    If skip_screen is True (e.g. CLEAN path), returns LOW with no scan.
    """
    if skip_screen:
        return {"risk_level": "LOW", "risk_reason": None}

    if gemini_client is None:
        return {"risk_level": "MEDIUM", "risk_reason": "No Gemini client — skipping screen."}

    screen = screen_generated_image(gemini_client, output_path)
    return {
        "risk_level": screen.get("risk_level", "MEDIUM"),
        "risk_reason": screen.get("risk_reason"),
    }


def process_final_assets(
    visual_report: str,
    project_id: str,
    location: str,
    gemini_client=None,
) -> list[dict]:
    """
    Main entry point. Processes each trend line from visual_report,
    generates or copies the final asset, screens it, and returns results.

    Args:
        visual_report:  Raw text output from analyze_visual_strategy
        project_id:     GCP project ID for Imagen
        location:       GCP region
        gemini_client:  Initialized Gemini client for post-gen IP screening.
                        Pass None to skip screening (assets get MEDIUM risk).

    Returns:
        List of dicts: [{"path": str, "term": str, "risk_level": str, "risk_reason": str}]
    """
    from src.processors.image_generator import generate_images

    final_dir = "output/final_assets"
    os.makedirs(final_dir, exist_ok=True)

    parsed = _parse_visual_report(visual_report)
    results = []

    for parts in parsed:
        term = parts.get("TREND", "").strip()
        if not term:
            continue

        parts = _force_regen_for_topic(parts)
        decision = parts.get("DECISION", "REGEN").upper()
        action = parts.get("ACTION", "")
        source = parts.get("SOURCE", "")

        term_slug = term.lower().replace(" ", "_")
        # Strip characters illegal in Windows paths
        import re
        term_slug = re.sub(r'[\\/*?:"<>|]', "", term_slug)
        output_path = os.path.join(final_dir, f"{term_slug}_final.png")

        risk_meta = {"risk_level": "MEDIUM", "risk_reason": "Not yet screened"}

        try:
            # ------------------------------------------------------------------
            # CLEAN — copy source directly
            # ------------------------------------------------------------------
            if "CLEAN" in decision and source and os.path.exists(source):
                shutil.copy(source, output_path)
                print(f"  [CLEAN] {output_path}")
                # CLEAN images are user-fetched reference images — still screen them
                risk_meta = _screen_and_annotate(gemini_client, output_path)

            # ------------------------------------------------------------------
            # MEME — apply text to source image
            # ------------------------------------------------------------------
            elif "MEME" in decision and source and os.path.exists(source):
                _apply_meme_text(source, action, output_path)
                print(f"  [MEME]  {output_path}")
                risk_meta = _screen_and_annotate(gemini_client, output_path)

            # ------------------------------------------------------------------
            # REGEN — structured Imagen generation
            # ------------------------------------------------------------------
            elif "REGEN" in decision:
                imagen_prompt = _build_prompt_from_parts(parts)
                prompt_str = imagen_prompt.build()
                negative_str = imagen_prompt.negative_prompt()

                print(f"  [REGEN] Generating: {term}")
                print(f"          Prompt: {prompt_str[:120]}...")

                paths = generate_images(
                    project_id,
                    location,
                    [prompt_str],
                    out_dir=final_dir,
                    negative_prompt_override=negative_str,
                )

                if paths and os.path.exists(paths[0]):
                    shutil.move(paths[0], output_path)
                    print(f"  [REGEN] Saved: {output_path}")
                    risk_meta = _screen_and_annotate(gemini_client, output_path)
                else:
                    print(f"  [REGEN] No image produced for: {term}")
                    continue

            else:
                print(f"  [SKIP]  No valid decision for: {term} (decision={decision})")
                continue

            if os.path.exists(output_path):
                if risk_meta["risk_level"] == "HIGH":
                    print(f"  [IP]    HIGH risk flagged: {risk_meta['risk_reason']}")
                results.append({
                    "path": output_path,
                    "term": term,
                    "risk_level": risk_meta["risk_level"],
                    "risk_reason": risk_meta["risk_reason"],
                })

        except Exception as e:
            print(f"  [ERROR] Manufacturing error for '{term}': {e}")

    return results


# ---------------------------------------------------------------------------
# Meme text overlay (unchanged from v1, extracted for clarity)
# ---------------------------------------------------------------------------
def _apply_meme_text(image_path: str, text: str, output_path: str) -> None:
    try:
        with Image.open(image_path).convert("RGB") as img:
            draw = ImageDraw.Draw(img)
            w, h = img.size

            try:
                font = ImageFont.truetype("impact.ttf", int(h * 0.09))
            except Exception:
                font = ImageFont.load_default()

            display_text = (text or "").upper()
            bbox = draw.textbbox((0, 0), display_text, font=font)
            text_w = bbox[2] - bbox[0]

            draw.text(
                ((w - text_w) // 2, int(h * 0.82)),
                display_text,
                font=font,
                fill="white",
                stroke_width=5,
                stroke_fill="black",
            )
            img.save(output_path)
    except Exception as e:
        print(f"  [MEME]  Text overlay failed: {e}")