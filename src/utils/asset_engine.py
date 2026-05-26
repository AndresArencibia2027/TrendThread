"""
asset_engine.py
===============
Pipeline per motif:
  1. Fetch reference shirt images for the term
  2. If <2 valid references → skip this trend
  3. Pass references to Gemini → get a design back
  4. Post-gen QC (photo/mockup detection only)
  5. Post-gen IP screen for risk labeling
"""

import json
import os
import re
import shutil
from pathlib import Path

from google.genai import types

from src.fetchers.market_research import fetch_reference_images, MIN_VALID_REFERENCES
from src.processors.image_generator import generate_shirt_design
from src.processors.ip_screener import screen_generated_image


_FINAL_DIR = Path(__file__).resolve().parents[2] / "output" / "final_assets"
_REF_DIR = Path(__file__).resolve().parents[2] / "output" / "references" / "market_refs"


_QC_SYSTEM = """
You are a QC screener for print-on-demand shirt designs.

Return ONLY a JSON object:
{
  "has_realistic_photo": <true if the image is a photorealistic photo of a
    real person or product, NOT illustration/graphic art>,
  "has_mockup_elements": <true if the image shows a shirt mockup, mannequin,
    person wearing the shirt, fabric folds, or hanger>
}

Text in the design is FINE — do not flag for containing text.
"""


def _slugify(term: str) -> str:
    slug = term.lower().replace(" ", "_")
    slug = re.sub(r'[\\/*?:"<>|]', "", slug)
    return slug[:60]


def _screen_qc(gemini_client, image_path: str) -> dict:
    if gemini_client is None:
        return {"has_realistic_photo": False, "has_mockup_elements": False}
    try:
        with open(image_path, "rb") as f:
            img_bytes = f.read()
        img_part = types.Part.from_bytes(data=img_bytes, mime_type="image/png")
        response = gemini_client.models.generate_content(
            model="gemini-2.0-flash",
            config=types.GenerateContentConfig(system_instruction=_QC_SYSTEM),
            contents=[img_part, "Screen this image."],
        )
        raw = response.text.strip()
        raw = re.sub(r"^```[a-z]*\n?", "", raw)
        raw = re.sub(r"\n?```$", "", raw)
        return json.loads(raw)
    except Exception as e:
        print(f"  [QC] Screen error: {e} — assuming usable")
        return {"has_realistic_photo": False, "has_mockup_elements": False}


def _screen_ip(gemini_client, image_path: str) -> dict:
    if gemini_client is None:
        return {"risk_level": "MEDIUM", "risk_reason": "Screening skipped."}
    result = screen_generated_image(gemini_client, image_path)
    return {
        "risk_level": result.get("risk_level", "MEDIUM"),
        "risk_reason": result.get("risk_reason"),
    }


def _save_references(term: str, image_bytes_list: list[bytes]) -> None:
    ref_dir = _REF_DIR / _slugify(term)
    ref_dir.mkdir(parents=True, exist_ok=True)
    for idx, img_bytes in enumerate(image_bytes_list, start=1):
        ext = "png" if img_bytes[:8] == b'\x89PNG\r\n\x1a\n' else "jpg"
        with open(ref_dir / f"ref_{idx}.{ext}", "wb") as f:
            f.write(img_bytes)


def process_final_assets(
    trend_data: list[dict],
    project_id: str = "",
    location: str = "",
    gemini_client=None,
    is_theme_mode: bool = False,
) -> list[dict]:
    """
    Generates one design per motif. Each motif needs: { term, topic_type }
    Returns: [{ path, term, risk_level, risk_reason }]
    """
    _FINAL_DIR.mkdir(parents=True, exist_ok=True)
    results = []

    for item in trend_data:
        term = (item.get("term") or "").strip()
        if not term:
            continue
        topic_type = item.get("topic_type", "GENERAL")
        output_path = str(_FINAL_DIR / f"{_slugify(term)}_final.png")

        print(f"\n  ── {term} [{topic_type}]")

        try:
            references = fetch_reference_images(term=term, max_images=5)
            if len(references) < MIN_VALID_REFERENCES:
                print(f"  [SKIP] Insufficient references for: {term}")
                continue

            _save_references(term, references)

            gen_path = generate_shirt_design(
                reference_images=references,
                out_path=output_path,
                term=term,
            )
            if not gen_path or not os.path.exists(gen_path):
                print(f"  [SKIP] Generation failed for: {term}")
                continue

            qc = _screen_qc(gemini_client, gen_path)
            if qc.get("has_realistic_photo") or qc.get("has_mockup_elements"):
                reason = "photorealistic" if qc.get("has_realistic_photo") else "mockup"
                print(f"  [QC] {reason} detected — retrying once")
                os.remove(gen_path)
                gen_path = generate_shirt_design(
                    reference_images=references,
                    out_path=output_path,
                    term=term,
                )
                if not gen_path or not os.path.exists(gen_path):
                    print(f"  [SKIP] Retry failed for: {term}")
                    continue

            risk = _screen_ip(gemini_client, gen_path)
            if risk["risk_level"] != "LOW":
                print(f"  [IP] {risk['risk_level']}: {risk['risk_reason']}")

            results.append({
                "path": gen_path,
                "term": term,
                "risk_level": risk["risk_level"],
                "risk_reason": risk["risk_reason"],
            })

        except Exception as e:
            print(f"  [ERROR] Failed for '{term}': {e}")

    return results