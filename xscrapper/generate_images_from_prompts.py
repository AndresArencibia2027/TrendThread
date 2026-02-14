"""
Generate 5 images from 5 prompts using Vertex AI Imagen only.
No Excel, no Gemini — use this to test the Vertex AI image API without wasting tokens.

Usage:
  python generate_images_from_prompts.py
  python generate_images_from_prompts.py --prompts-file trend_analysis_prompts.txt
  python generate_images_from_prompts.py --vertex-project-id YOUR_PROJECT_ID

Requires:
  - Google Cloud auth: run  gcloud auth application-default login
  - Vertex AI API enabled on your project
  - Project ID in .env as VERTEX_PROJECT_ID or pass --vertex-project-id
"""

import os
import re
import argparse

# Load .env so VERTEX_PROJECT_ID is available
def _load_env():
    for d in (os.path.dirname(os.path.abspath(__file__)), os.getcwd()):
        path = os.path.join(d, ".env")
        if os.path.isfile(path):
            try:
                with open(path, "r") as f:
                    for line in f:
                        line = line.strip()
                        if line and not line.startswith("#") and "=" in line:
                            k, v = line.split("=", 1)
                            k, v = k.strip(), v.strip().strip('"').strip("'")
                            if k and v and k not in os.environ:
                                os.environ[k] = v
            except Exception:
                pass
            break

_load_env()

try:
    import vertexai
    from vertexai.preview.vision_models import ImageGenerationModel
    VERTEX_AVAILABLE = True
except ImportError:
    VERTEX_AVAILABLE = False


def load_prompts_from_file(filepath):
    """Load 5 prompts from trend_analysis_prompts.txt style file. Returns list of 5 strings."""
    with open(filepath, "r", encoding="utf-8") as f:
        text = f.read()
    prompts = []
    for i in range(1, 6):
        pat = rf"---\s*PROMPT\s+{i}\s*---\s*(.*?)(?=---\s*PROMPT\s+\d+\s*---|\Z)"
        m = re.search(pat, text, re.DOTALL | re.IGNORECASE)
        if m:
            prompts.append(m.group(1).strip())
    if len(prompts) < 5:
        # Fallback: split by --- PROMPT
        parts = re.split(r"---\s*PROMPT\s+\d+\s*---", text, flags=re.IGNORECASE)
        prompts = [p.strip() for p in parts if p.strip() and len(p.strip()) > 30][:5]
    return prompts[:5]


def generate_one_image(project_id, location, prompt, output_path, model_name="imagen-3.0-generate-002"):
    """Generate one image with Vertex AI Imagen (matches official sample). Raises with clear message on auth failure."""
    if not VERTEX_AVAILABLE:
        raise ImportError("Install: pip install google-cloud-aiplatform")
    # When using gcloud user credentials (no service account), Vertex AI requires a quota project.
    if not os.environ.get("GOOGLE_APPLICATION_CREDENTIALS"):
        os.environ["GOOGLE_CLOUD_QUOTA_PROJECT"] = project_id
    try:
        vertexai.init(project=project_id, location=location)
    except Exception as e:
        _raise_auth_help(e)
    model = ImageGenerationModel.from_pretrained(model_name)
    try:
        response = model.generate_images(
            prompt=prompt,
            number_of_images=1,
            language="en",
            aspect_ratio="1:1",
            safety_filter_level="block_some",
            person_generation="allow_adult",
        )
        # response is ImageGenerationResponse with .images list (no len(response))
        if response.images:
            response.images[0].save(location=output_path, include_generation_parameters=False)
            try:
                n_bytes = len(response.images[0]._image_bytes)
                print(f"  Created output image using {n_bytes} bytes")
            except Exception:
                pass
            return True
    except Exception as e:
        err = str(e)
        if "429" in err and ("quota" in err.lower() or "exceeded" in err.lower()):
            raise RuntimeError(
                "Imagen rate limit (429) hit. Default quota is low. Options:\n"
                "  - Wait a few minutes and run again\n"
                "  - Request a quota increase: https://cloud.google.com/vertex-ai/docs/generative-ai/quotas-genai\n"
                "  - Try --imagen-model imagen-3.0-generate-002 (different quota)"
            )
        if "billing" in err.lower() and ("enable" in err.lower() or "BILLING_DISABLED" in err):
            raise RuntimeError(
                f"Vertex AI Imagen requires billing to be enabled on your project.\n\n"
                f"Enable billing: https://console.cloud.google.com/billing/enable?project={project_id}\n\n"
                f"(Or use a different GCP project that already has a billing account linked.)"
            )
        if "quota project" in err.lower() or "quota_project" in err.lower():
            raise RuntimeError(
                f"Vertex AI requires a quota project when using gcloud login.\n\n"
                f"Run this once (use your project ID):\n\n"
                f"  gcloud auth application-default set-quota-project {project_id}\n\n"
                f"Then run this script again.\n\nOriginal error: {e}"
            )
        if "authenticate" in err.lower() or "Unable to authenticate" in err:
            _raise_auth_help(e)
        raise
    return False


def _auth_help_text():
    """Instructions for Vertex AI auth (gcloud not installed → use service account)."""
    creds = os.environ.get("GOOGLE_APPLICATION_CREDENTIALS")
    gcloud_ok = False
    try:
        import shutil
        gcloud_ok = bool(shutil.which("gcloud"))
    except Exception:
        pass
    lines = [
        "Vertex AI needs Google Cloud credentials. Use one of these:",
        "",
        "OPTION A — Install gcloud, then log in (good for local use):",
        "  macOS:   brew install --cask google-cloud-sdk",
        "  Other:   https://cloud.google.com/sdk/docs/install",
        "  Then:   gcloud auth application-default login",
        "",
        "OPTION B — Use a service account (no gcloud needed):",
        "  1. Go to: https://console.cloud.google.com/iam-admin/serviceaccounts",
        "  2. Create a key (JSON) for a service account that has Vertex AI access",
        "  3. Run:  export GOOGLE_APPLICATION_CREDENTIALS=\"/path/to/your-key.json\"",
        "  4. Run this script again in the same terminal",
        "",
    ]
    if creds:
        lines.append(f"  (You have GOOGLE_APPLICATION_CREDENTIALS set; if auth still fails, the key may be invalid or lack Vertex AI permissions.)")
    elif not gcloud_ok:
        lines.append("  (gcloud not found in PATH — use Option B or install gcloud first.)")
    return "\n".join(lines)


def _raise_auth_help(e):
    msg = "Vertex AI authentication failed.\n\n" + _auth_help_text() + "\n\nOriginal error: " + str(e)
    raise RuntimeError(msg)


def main():
    parser = argparse.ArgumentParser(description="Generate 5 images from prompts using Vertex AI Imagen (no Gemini, no Excel).")
    parser.add_argument("--prompts-file", default="trend_analysis_prompts.txt", help="File with 5 prompts (--- PROMPT 1 --- ...). Default: trend_analysis_prompts.txt")
    parser.add_argument("--vertex-project-id", default=None, help="Google Cloud project ID. Or set VERTEX_PROJECT_ID in .env")
    parser.add_argument("--vertex-location", default="us-central1", help="Vertex AI region (default: us-central1)")
    parser.add_argument("--out-dir", default=".", help="Where to save trend_image_1.png ... trend_image_5.png")
    parser.add_argument("--imagen-model", default="imagen-3.0-generate-002", help="Imagen model (default: imagen-3.0-generate-002). Use imagen-4.0-generate-001 for Imagen 4)")
    args = parser.parse_args()

    project_id = args.vertex_project_id or os.environ.get("VERTEX_PROJECT_ID")
    if not project_id:
        print("Error: Set VERTEX_PROJECT_ID in .env or pass --vertex-project-id")
        print("  Example: python generate_images_from_prompts.py --vertex-project-id gen-lang-client-0401157070")
        return 1

    # So Vertex AI uses this project for quota when using gcloud user credentials (no service account).
    if not os.environ.get("GOOGLE_APPLICATION_CREDENTIALS"):
        os.environ["GOOGLE_CLOUD_QUOTA_PROJECT"] = project_id

    if not os.path.isfile(args.prompts_file):
        print(f"Error: Prompts file not found: {args.prompts_file}")
        print("  Create it by running analyze_trends_gemini.py once, or pass another file with --- PROMPT 1 --- ... --- PROMPT 5 ---")
        return 1

    prompts = load_prompts_from_file(args.prompts_file)
    if len(prompts) < 5:
        print(f"Warning: Only found {len(prompts)} prompts in {args.prompts_file}. Need 5.")
        if not prompts:
            return 1
    else:
        prompts = prompts[:5]

    print(f"Loaded {len(prompts)} prompts from {args.prompts_file}")
    print(f"Project: {project_id}  Model: {args.imagen_model}")
    if not os.environ.get("GOOGLE_APPLICATION_CREDENTIALS"):
        try:
            import shutil
            if not shutil.which("gcloud"):
                print("Note: gcloud not installed. Use a service account: set GOOGLE_APPLICATION_CREDENTIALS=/path/to/key.json")
        except Exception:
            pass
    print("Generating images...\n")

    os.makedirs(args.out_dir, exist_ok=True)
    for i, prompt in enumerate(prompts, 1):
        path = os.path.join(args.out_dir, f"trend_image_{i}.png")
        try:
            if generate_one_image(project_id, args.vertex_location, prompt, path, args.imagen_model):
                print(f"  ✓ Saved: {path}")
            else:
                print(f"  ✗ No image for prompt {i}")
        except Exception as e:
            err_str = str(e)
            err_lower = err_str.lower()
            if "429" in err_str and ("quota" in err_lower or "exceeded" in err_lower):
                print(f"  ✗ Error prompt {i}: Rate limit (429) exceeded.")
                print("\n  Wait a few minutes or request quota: https://cloud.google.com/vertex-ai/docs/generative-ai/quotas-genai")
                return 1
            if "billing" in err_lower and ("enable" in err_lower or "billing_disabled" in err_lower):
                print(f"  ✗ Error prompt {i}: Billing not enabled on project.")
                print("\n" + "="*60)
                print("  Vertex AI Imagen requires a billing account on your project.")
                print(f"  Enable billing: https://console.cloud.google.com/billing/enable?project={project_id}")
                print("  Then run this script again.")
                print("="*60)
                return 1
            print(f"  ✗ Error prompt {i}: {e}")
            if "quota project" in err_lower:
                print("\n" + "="*60)
                print(f"  Run this once, then try again:")
                print(f"  gcloud auth application-default set-quota-project {project_id}")
                print("="*60)
                return 1
            if "auth" in err_lower or "authenticate" in err_lower:
                print("\n" + "="*60)
                print(_auth_help_text())
                print("="*60)
                return 1
    print("\nDone.")
    return 0


if __name__ == "__main__":
    exit(main())
