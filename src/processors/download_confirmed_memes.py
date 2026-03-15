#!/usr/bin/env python3
"""
Fetch confirmed+newest meme pages from KnowYourMeme and download their images
into the centralized output directory.
"""

import os
from pathlib import Path
from urllib.parse import urlparse
import requests

from src.fetchers.confirmed_memes_pages import fetch_entries

# Updated to store in the centralized output folder
OUTPUT_DIR = Path("output/references/downloaded_confirmed_memes")

def sanitize_filename(name: str) -> str:
    """Convert meme title into a safe filename fragment."""
    safe = "".join(c for c in name if c.isalnum() or c in (" ", "-", "_")).strip()
    if not safe:
        safe = "meme"
    return safe.replace(" ", "_")

def choose_extension(img_url: str) -> str:
    """Infer file extension from the image URL path."""
    path = urlparse(img_url).path
    _, ext = os.path.splitext(path)
    if ext:
        return ext
    return ".jpg"

def download_image(img_url: str, title: str, index: int, out_dir: Path) -> Path | None:
    """Download a single image and return its path, or None on failure."""
    out_dir.mkdir(parents=True, exist_ok=True)

    ext = choose_extension(img_url)
    fname = f"{index:02d}_{sanitize_filename(title)}{ext}"
    out_path = out_dir / fname

    print(f"    ⬇  Downloading image -> {fname}")
    try:
        # Added a User-Agent to prevent KYM from blocking the request
        headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'}
        resp = requests.get(img_url, timeout=20, headers=headers)
        if resp.status_code == 200:
            out_path.write_bytes(resp.content)
            return out_path
        else:
            print(f"    HTTP {resp.status_code} for {img_url}")
            return None
    except Exception as ex:
        print(f"    Error downloading {img_url}: {ex}")
        return None

def main(start_page: int = 1, end_page: int = 2) -> None:
    print("=" * 60)
    print(" Downloading confirmed + newest KYM memes (images + titles)")
    print("=" * 60)
    print(f"Pages: {start_page}–{end_page}")
    print()

    entries = fetch_entries(start_page, end_page)
    if not entries:
        print(" No entries found.")
        return

    print(f"Found {len(entries)} meme entries.")
    print(f"Images will be saved into: {OUTPUT_DIR.resolve()}")
    print()

    success = 0
    for i, e in enumerate(entries, 1):
        title = e["title"]
        meme_url = e["meme_url"]
        img_url = e["image_url"]

        print(f"[{i}/{len(entries)}] {title}")
        print(f"    Meme URL: {meme_url}")

        if not img_url:
            print("     No image URL found, skipping.")
            continue

        print(f"     Image URL: {img_url}")
        out_path = download_image(img_url, title, i, OUTPUT_DIR)
        if out_path:
            success += 1

    print()
    print("=" * 60)
    print(f" Done. Downloaded {success}/{len(entries)} images into {OUTPUT_DIR}")
    print("=" * 60)

if __name__ == "__main__":
    # Adjust page range here (e.g., 1, 1 for a quick test)
    main(1, 2)