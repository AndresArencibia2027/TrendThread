"""
image_generator.py
==================
Pass reference shirt images to Gemini 3 Pro Image and get a new design
back that matches their visual style. Minimal instruction — Gemini
decides what to draw based on the references.

Requires GOOGLE_API_KEY (AI Studio) since Gemini image generation
isn't available on Vertex AI.
"""

import io
import os
from typing import Optional

from google import genai
from google.genai import types
from PIL import Image


GEMINI_IMAGE_MODELS = [
    "gemini-3-pro-image-preview",
    "gemini-2.5-flash-image",
]


def generate_shirt_design(
    reference_images: list[bytes],
    out_path: str,
    term: str = "",
) -> Optional[str]:
    """
    Generates a shirt design from reference images.
    Returns the saved path, or None if generation failed.
    """
    api_key = os.getenv("GOOGLE_API_KEY", "").strip()
    if not api_key:
        print("  [gen] No GOOGLE_API_KEY — cannot generate")
        return None

    if not reference_images:
        print("  [gen] No reference images — cannot generate")
        return None

    client = genai.Client(api_key=api_key)

    instruction = (
        "These images are reference shirt designs. "
        "Create a new shirt design that fits visually with these references — "
        "same style, same aesthetic, same kind of subject matter and composition. "
        "It should look like it belongs in the same collection as these shirts, "
        "but be an original design (not a copy of any specific one shown).\n\n"
        "Output only the design graphic itself on a clean white background — "
        "no shirt, no mockup, no person wearing it, no product photo."
    )

    content_parts: list = [instruction]
    valid_count = 0
    for img_bytes in reference_images[:5]:
        try:
            mime = "image/jpeg"
            if img_bytes[:8] == b'\x89PNG\r\n\x1a\n':
                mime = "image/png"
            elif img_bytes[:4] == b'RIFF':
                mime = "image/webp"
            content_parts.append(
                types.Part.from_bytes(data=img_bytes, mime_type=mime)
            )
            valid_count += 1
        except Exception as e:
            print(f"  [gen] Skipping invalid reference: {e}")

    if valid_count == 0:
        print("  [gen] No valid reference parts could be built")
        return None

    print(f"  [gen] Generating with {valid_count} reference(s)…")
    os.makedirs(os.path.dirname(out_path), exist_ok=True)

    for model in GEMINI_IMAGE_MODELS:
        try:
            response = client.models.generate_content(
                model=model,
                config=types.GenerateContentConfig(
                    response_modalities=["TEXT", "IMAGE"],
                    image_config=types.ImageConfig(
                        aspect_ratio="1:1",
                        image_size="1K",
                    ),
                ),
                contents=content_parts,
            )

            for part in (response.parts or []):
                if part.inline_data and part.inline_data.data:
                    raw_bytes = part.inline_data.data
                    try:
                        img = Image.open(io.BytesIO(raw_bytes))
                        img.save(out_path, format="PNG")
                    except Exception:
                        with open(out_path, "wb") as f:
                            f.write(raw_bytes)
                    print(f"  [gen] Saved via {model}: {out_path}")
                    return out_path

            print(f"  [gen] {model}: no image in response")

        except Exception as e:
            err = str(e)
            print(f"  [gen] {model} failed: {err[:120]}")
            if "404" in err or "not found" in err.lower():
                continue
            break

    return None