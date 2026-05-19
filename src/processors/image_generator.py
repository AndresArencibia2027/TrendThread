"""
image_generator.py
==================
Wraps Imagen 4 (with fallback to Imagen 3) for TrendThread asset generation.

Key improvements over v1:
- Accepts an optional negative_prompt_override so asset_engine can pass
  the ImagenPrompt-derived negative prompt rather than the hardcoded default.
- Accepts ImagenPrompt objects directly via generate_from_prompt() — a typed
  entry point that calls generate_images() internally.
- Exposes seed parameter for reproducible generation during regeneration.
- Model fallback chain is unchanged (imagen-4 → imagen-3.0-002 → imagen-3.0-001).
"""

import os
from typing import Optional, Union

from google import genai
from google.genai import types


# ---------------------------------------------------------------------------
# Default negative prompt — used when no override is provided
# ---------------------------------------------------------------------------
DEFAULT_NEGATIVE_PROMPT = (
    "t-shirt, hoodie, mockup, mannequin, model, hanger, fabric, folds, "
    "3d render, photo, person wearing, realistic shirt, watermark, signature, "
    "copyright symbol, blurry, low quality, cluttered background"
)

MODEL_FALLBACK_CHAIN = [
    "imagen-4.0-generate-001",
    "imagen-3.0-generate-002",
    "imagen-3.0-generate-001",
]


def _build_image_client(project_id: str, location: str) -> tuple[genai.Client, bool]:
    """Returns (client, using_vertex)."""
    api_key = os.getenv("GOOGLE_API_KEY", "").strip()
    if project_id:
        return genai.Client(vertexai=True, project=project_id, location=location), True
    if api_key:
        return genai.Client(api_key=api_key), False
    raise ValueError(
        "Set VERTEX_PROJECT_ID for Vertex mode, or GOOGLE_API_KEY for API-key mode."
    )


def _call_imagen(
    client: genai.Client,
    model: str,
    prompt: str,
    negative_prompt: str,
    seed: Optional[int],
) -> Optional[bytes]:
    """
    Makes a single generate_image call. Returns image bytes or None.
    Raises on hard errors so the caller can handle fallback.
    """
    config = types.GenerateImageConfig(
        number_of_images=1,
        aspect_ratio="1:1",
        person_generation="ALLOW_ADULT",
        safety_filter_level="BLOCK_MEDIUM_AND_ABOVE",
        negative_prompt=negative_prompt,
        **({"seed": seed} if seed is not None else {}),
    )

    response = client.models.generate_image(
        model=model,
        prompt=prompt,
        config=config,
    )

    if response and response.generated_images:
        return response.generated_images[0].image.image_bytes
    return None


def generate_images(
    project_id: str,
    location: str,
    prompts: list[str],
    out_dir: str = "output",
    model_name: str = "imagen-4.0-generate-001",
    negative_prompt_override: Optional[str] = None,
    seed: Optional[int] = None,
) -> list[str]:
    """
    Generates up to 5 PNG images from a list of prompt strings.

    Args:
        project_id:               GCP project ID (or empty string for API key mode)
        location:                 GCP region
        prompts:                  List of prompt strings (max 5 used)
        out_dir:                  Output directory for saved PNGs
        model_name:               Primary model to try first
        negative_prompt_override: If provided, replaces the default negative prompt
        seed:                     Optional seed for reproducible outputs

    Returns:
        List of absolute paths to saved PNG files.
    """
    os.makedirs(out_dir, exist_ok=True)
    negative_prompt = negative_prompt_override or DEFAULT_NEGATIVE_PROMPT

    client, using_vertex = _build_image_client(project_id, location)

    # Build fallback chain starting from the requested model
    model_chain = [model_name] + [m for m in MODEL_FALLBACK_CHAIN if m != model_name]

    generated_paths = []

    for i, prompt in enumerate(prompts[:5], 1):
        out_path = os.path.join(out_dir, f"trend_image_{i}.png")
        print(f"  Generating image {i}/{min(len(prompts), 5)}...")

        img_bytes = None
        last_error = None

        for model in model_chain:
            try:
                img_bytes = _call_imagen(client, model, prompt, negative_prompt, seed)
                if img_bytes is not None:
                    break
            except Exception as err:
                last_error = err
                err_str = str(err)

                # Vertex permission denied → try API key fallback
                if (
                    using_vertex
                    and "aiplatform.endpoints.predict" in err_str
                    and os.getenv("GOOGLE_API_KEY", "").strip()
                ):
                    print("    Vertex permission denied — falling back to API key mode.")
                    client = genai.Client(api_key=os.getenv("GOOGLE_API_KEY", "").strip())
                    using_vertex = False
                    try:
                        img_bytes = _call_imagen(client, model, prompt, negative_prompt, seed)
                        if img_bytes is not None:
                            break
                    except Exception as fallback_err:
                        last_error = fallback_err
                continue

        if img_bytes is not None:
            with open(out_path, "wb") as f:
                f.write(img_bytes)
            generated_paths.append(out_path)
            print(f"    Saved: {out_path}")
        elif last_error is not None:
            print(f"    Error on prompt {i}: {last_error}")
        else:
            print(f"    Prompt {i} blocked by safety filters.")

    return generated_paths


def generate_from_prompt(
    project_id: str,
    location: str,
    imagen_prompt,           # ImagenPrompt instance from gemini_analyzer
    out_dir: str = "output",
    seed: Optional[int] = None,
) -> list[str]:
    """
    Typed entry point that accepts an ImagenPrompt object directly.
    Calls generate_images() internally.

    Args:
        imagen_prompt: ImagenPrompt instance (from gemini_analyzer.build_imagen_prompt)

    Returns:
        List of saved PNG paths (0 or 1 items for a single prompt).
    """
    return generate_images(
        project_id=project_id,
        location=location,
        prompts=[imagen_prompt.build()],
        out_dir=out_dir,
        negative_prompt_override=imagen_prompt.negative_prompt(),
        seed=seed,
    )