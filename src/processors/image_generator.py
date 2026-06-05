"""
image_generator.py
==================
Generates shirt designs using Gemini 3 Pro Image with reference images,
then removes the background with a two-layer approach:
  1. rembg with birefnet-general model (better than isnet for graphics)
  2. Brightness safeguard: any pixel darker than ~near-white is FORCED
     to stay opaque, regardless of what the ML model thinks.

This prevents the failure mode where rembg erases dark interior parts
of a design (cartoon outlines, text, etc.) by mistaking them for
background or shadow.
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

# Pixels with RGB Euclidean distance ≤ this to pure white are eligible to
# be made transparent. Anything farther from white (≥ this distance) is
# treated as part of the design and FORCED opaque, overriding rembg.
# Higher value = more aggressive background removal but more risk of
# erasing light parts of the design.
# 60 is a safe default for designs on white backgrounds. Anti-aliased
# edges (which sit between white and design colors) typically have
# distances of 30-80; this keeps the lighter ones removable.
WHITE_DISTANCE_THRESHOLD = 60


_REMBG_SESSION = None


def _get_rembg_session():
    """Lazy-loads the rembg session once per process."""
    global _REMBG_SESSION
    if _REMBG_SESSION is None:
        try:
            from rembg import new_session
            # birefnet-general handles graphic content better than isnet.
            # Falls back to isnet-general-use if birefnet isn't installed.
            try:
                _REMBG_SESSION = new_session("birefnet-general")
                print("  [gen] rembg session initialized (birefnet-general)")
            except Exception:
                _REMBG_SESSION = new_session("isnet-general-use")
                print("  [gen] rembg session initialized (isnet-general-use fallback)")
        except Exception as e:
            print(f"  [gen] rembg unavailable: {e}")
            _REMBG_SESSION = False
    return _REMBG_SESSION


def _force_opaque_below_threshold(
    img: Image.Image,
    original: Image.Image,
    white_threshold: int = WHITE_DISTANCE_THRESHOLD,
) -> Image.Image:
    """
    Safeguard pass: any pixel whose ORIGINAL color is far from white
    must be opaque, regardless of what rembg decided.

    This corrects rembg's main failure mode on graphic designs: erasing
    dark parts of the design that the model misinterpreted as shadow
    or background.

    CRITICAL: we compare against the ORIGINAL image's RGB values, not
    against the rembg output. rembg may zero out the RGB channels of
    "background" pixels (resulting in transparent BLACK), so we cannot
    trust the post-rembg color — we need the original colors to know
    what was design vs background.

    Args:
        img:        RGBA image after rembg processing (subject + transparent bg)
        original:   The original image before rembg (used to read true colors)
        white_threshold: RGB Euclidean distance from pure white.
                         Original pixels ≥ this distance from white → forced opaque.

    Returns:
        RGBA image with originally-dark pixels guaranteed opaque.
    """
    if img.mode != "RGBA":
        img = img.convert("RGBA")
    if original.mode != "RGB":
        original = original.convert("RGB")

    # Both images must be the same size
    if img.size != original.size:
        # Resize original to match (rembg should preserve size, but just in case)
        original = original.resize(img.size, Image.LANCZOS)

    out_pixels = img.load()
    orig_pixels = original.load()
    width, height = img.size
    threshold_sq = white_threshold * white_threshold

    forced_count = 0
    for y in range(height):
        for x in range(width):
            # Look at the ORIGINAL color to decide if this is design or bg
            r, g, b = orig_pixels[x, y]
            dist_sq = (255 - r) ** 2 + (255 - g) ** 2 + (255 - b) ** 2

            if dist_sq >= threshold_sq:
                # Original was a dark / non-white pixel → must be design → opaque
                cur_r, cur_g, cur_b, cur_a = out_pixels[x, y]
                if cur_a < 255:
                    # Restore original color AND make fully opaque.
                    # We use the original RGB because rembg may have zeroed
                    # the RGB channels on "background" pixels.
                    out_pixels[x, y] = (r, g, b, 255)
                    forced_count += 1

    if forced_count > 0:
        print(f"  [gen] Forced {forced_count:,} dark pixels back to opaque (rembg over-eroded)")

    return img


def _remove_background(image_bytes: bytes) -> Optional[Image.Image]:
    """
    Removes background using rembg + brightness safeguard.

    Returns None if rembg is unavailable.
    """
    session = _get_rembg_session()
    if not session:
        return None
    try:
        from rembg import remove

        # Open the original first so we can compare colors after rembg
        original = Image.open(io.BytesIO(image_bytes)).convert("RGB")

        output_bytes = remove(image_bytes, session=session)
        cutout = Image.open(io.BytesIO(output_bytes)).convert("RGBA")

        return _force_opaque_below_threshold(cutout, original)
    except Exception as e:
        print(f"  [gen] Background removal failed: {e}")
        return None


def generate_shirt_design(
    reference_images: list[bytes],
    out_path: str,
    term: str = "",
) -> Optional[str]:
    """
    Generates a shirt design from reference images.

    Pipeline:
      1. Pass references + instruction to Gemini (asks for solid white bg)
      2. rembg isolates the design subject
      3. Brightness safeguard prevents dark design parts from being erased
      4. Save as RGBA PNG
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
        "The attached images are reference shirt designs — full shirt photos "
        "showing the artwork printed on them. Study the ARTWORK in each "
        "reference (ignore the shirt, fabric, model, and background).\n\n"
        "Create a new ORIGINAL design that fits visually with these references: "
        "same illustration style, same aesthetic, same kind of subject matter, "
        "same composition approach. It should look like it belongs in the same "
        "collection as these designs, but be original — not a copy of any "
        "specific reference.\n\n"
        "CRITICAL OUTPUT REQUIREMENTS:\n"
        "- Output ONLY the design artwork itself — the graphic that would be "
        "printed on a shirt, not a shirt with the graphic on it.\n"
        "- Place the artwork on a PURE SOLID WHITE background (RGB 255,255,255). "
        "Do NOT use any checkerboard pattern, gray/white grid, or transparency "
        "visualization. Do NOT use off-white, cream, or any tinted background. "
        "The background must be a clean uniform white fill.\n"
        "- Do NOT include any shirt, fabric, mannequin, model, hanger, person, "
        "product mockup, or photo composition. Just the isolated artwork "
        "centered on white.\n"
        "- The design itself should be vivid and detailed; only the surrounding "
        "background should be plain white."
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

                    cutout = _remove_background(raw_bytes)
                    if cutout is not None:
                        cutout.save(out_path, format="PNG")
                        print(f"  [gen] Saved cutout via {model}: {out_path}")
                    else:
                        try:
                            img = Image.open(io.BytesIO(raw_bytes)).convert("RGBA")
                            img.save(out_path, format="PNG")
                        except Exception:
                            with open(out_path, "wb") as f:
                                f.write(raw_bytes)
                        print(f"  [gen] Saved RAW (rembg unavailable) via {model}: {out_path}")

                    return out_path

            print(f"  [gen] {model}: no image in response")

        except Exception as e:
            err = str(e)
            print(f"  [gen] {model} failed: {err[:120]}")
            if "404" in err or "not found" in err.lower():
                continue
            break

    return None