import os
from google import genai
from google.genai import types

def generate_five_images(project_id, location, prompts, out_dir="output", model_name="imagen-4.0-generate-001"):
    """Generates 5 PNG images. Uses 'generate_image' (singular) for 2026 SDK stability."""
    os.makedirs(out_dir, exist_ok=True)
    
    # Re-use the Vertex client
    client = genai.Client(vertexai=True, project=project_id, location=location)
    negative_prompt = "t-shirt, hoodie, mockup, mannequin, model, hanger, fabric, folds, 3d render, photo, person wearing, realistic shirt"

    generated_paths = []
    for i, prompt in enumerate(prompts[:5], 1):
        path = os.path.join(out_dir, f"trend_image_{i}.png")
        try:
            print(f" Generating Visual {i}/5...")
            # Note: Method is singular 'generate_image' in most GenAI SDK builds
            response = client.models.generate_image(
                model=model_name,
                prompt=prompt,
                config=types.GenerateImageConfig(
                    number_of_images=1,
                    aspect_ratio="1:1",
                    person_generation="ALLOW_ADULT",
                    safety_filter_level="BLOCK_MEDIUM_AND_ABOVE",
                    negative_prompt=negative_prompt
                )
            )
            
            # The singular call returns a list of generated_images
            if response.generated_images:
                img_data = response.generated_images[0].image.image_bytes
                
                # THE FIX: Check if we actually got bytes
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