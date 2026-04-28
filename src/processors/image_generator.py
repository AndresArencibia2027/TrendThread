import os
from google import genai
from google.genai import types


def _build_image_client(project_id, location):
    api_key = os.getenv("GOOGLE_API_KEY", "").strip()
    if project_id:
        return genai.Client(vertexai=True, project=project_id, location=location)
    if api_key:
        return genai.Client(api_key=api_key)
    raise ValueError(
        "Set VERTEX_PROJECT_ID for Vertex mode, or GOOGLE_API_KEY for API-key mode."
    )


def generate_five_images(project_id, location, prompts, out_dir="output", model_name="imagen-4.0-generate-001"):
    """Generates 5 PNG images. Uses 'generate_image' (singular) for 2026 SDK stability."""
    os.makedirs(out_dir, exist_ok=True)
    using_vertex = bool(project_id)
    client = _build_image_client(project_id, location)
    model_candidates = [model_name, "imagen-3.0-generate-002", "imagen-3.0-generate-001"]
    negative_prompt = "t-shirt, hoodie, mockup, mannequin, model, hanger, fabric, folds, 3d render, photo, person wearing, realistic shirt"

    generated_paths = []
    for i, prompt in enumerate(prompts[:5], 1):
        path = os.path.join(out_dir, f"trend_image_{i}.png")
        try:
            print(f" Generating Visual {i}/5...")

            response = None
            last_error = None
            for candidate_model in model_candidates:
                try:
                    response = client.models.generate_image(
                        model=candidate_model,
                        prompt=prompt,
                        config=types.GenerateImageConfig(
                            number_of_images=1,
                            aspect_ratio="1:1",
                            person_generation="ALLOW_ADULT",
                            safety_filter_level="BLOCK_MEDIUM_AND_ABOVE",
                            negative_prompt=negative_prompt,
                        ),
                    )
                    break
                except Exception as model_error:
                    last_error = model_error
                    err_text = str(model_error)
                    if using_vertex and "aiplatform.endpoints.predict" in err_text and os.getenv("GOOGLE_API_KEY", "").strip():
                        print("    Vertex permission denied. Falling back to API key mode.")
                        client = genai.Client(api_key=os.getenv("GOOGLE_API_KEY", "").strip())
                        using_vertex = False
                        try:
                            response = client.models.generate_image(
                                model=candidate_model,
                                prompt=prompt,
                                config=types.GenerateImageConfig(
                                    number_of_images=1,
                                    aspect_ratio="1:1",
                                    person_generation="ALLOW_ADULT",
                                    safety_filter_level="BLOCK_MEDIUM_AND_ABOVE",
                                    negative_prompt=negative_prompt,
                                ),
                            )
                            break
                        except Exception as fallback_error:
                            last_error = fallback_error
                            continue
                    continue

            if response is None and last_error is not None:
                raise last_error

            # The singular call returns a list of generated_images
            if response and response.generated_images:
                img_data = response.generated_images[0].image.image_bytes

                # Check if we actually got bytes
                if img_data is not None:
                    with open(path, "wb") as f:
                        f.write(img_data)
                    generated_paths.append(path)
                    print(f"    Saved: {path}")
                else:
                    print(f"    Prompt {i} was blocked by Safety Filters (Returned None).")
            else:
                print(f"    No image generated for prompt {i}")
        except Exception as e:
            print(f"    Error on prompt {i}: {e}")
            
    return generated_paths