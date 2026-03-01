import os
import requests
from io import BytesIO
from PIL import Image
from dotenv import load_dotenv

load_dotenv()

def fetch_and_save_visuals(trend_term, num_results=3, base_dir="output/references"):
    """
    Fetches raw Google Images via SerpApi.
    Gets the 'Original' high-res links directly from the Google index.
    """
    api_key = os.getenv("SERPAPI_KEY")
    folder_path = os.path.join(base_dir, trend_term.lower().replace(" ", "_"))
    os.makedirs(folder_path, exist_ok=True)

    if not api_key:
        print(" SERPAPI_KEY not found in .env")
        return []

    # 1. Hit the SerpApi Google Images endpoint
    params = {
        "engine": "google_images",
        "q": trend_term,
        "api_key": api_key,
        "num": 10,  # Request 10 candidates to ensure we get 3 valid ones
        "safe": "off"
    }

    print(f"🔍 Querying SerpApi (Google Images) for '{trend_term}'...")

    try:
        response = requests.get("https://serpapi.com/search", params=params, timeout=15)
        response.raise_for_status()
        results = response.json().get("images_results", [])
        
        local_files = []
        headers = {'User-Agent': 'Mozilla/5.0'} # Standard headers for image hosts

        # 2. Iterate through results and verify
        for item in results:
            if len(local_files) >= num_results:
                break
            
            # 'original' is the direct high-res link provided by SerpApi
            img_url = item.get("original")
            if not img_url: continue

            try:
                img_res = requests.get(img_url, timeout=10, headers=headers)
                if img_res.status_code == 200:
                    if _save_if_valid(img_res.content, folder_path, local_files):
                        print(f"    Saved Raw: {img_url[:60]}...")
            except:
                continue

        if not local_files:
            print(f" No valid images found for '{trend_term}'.")
            
        return local_files

    except Exception as e:
        print(f" SerpApi Error: {e}")
        return []

def _save_if_valid(content, folder, file_list):
    """Verifies that the file is a real image and > 300px before saving."""
    try:
        img = Image.open(BytesIO(content))
        if img.width < 300 or img.height < 300:
            return False # Filter out small icons/logos
            
        ext = f".{img.format.lower()}" if img.format else ".jpg"
        path = os.path.join(folder, f"raw_{len(file_list)}{ext}")
        with open(path, "wb") as f:
            f.write(content)
        file_list.append(path)
        return True
    except:
        return False