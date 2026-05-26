"""
market_research.py
==================
Single job: fetch reference shirt design images for a trend term.

Search Google Images for "{term} shirt", download the actual images,
return raw bytes. No analysis, no text extraction, no style descriptions.

If fewer than MIN_VALID_REFERENCES images can be downloaded, returns
an empty list — caller should skip the trend.
"""

import os
from typing import Optional

import requests

_SERPAPI_ENDPOINT = "https://serpapi.com/search"
_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "image/webp,image/apng,image/*,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}

_MIN_IMAGE_SIZE = 10_000
MIN_VALID_REFERENCES = 2

_BLOCKED_SOURCES = {
    "twitter.com", "x.com", "instagram.com", "facebook.com",
    "tiktok.com", "youtube.com", "wikipedia.org", "imdb.com",
    "nytimes.com", "bbc.com", "cnn.com", "theguardian.com",
    "reddit.com", "vogue.com", "hypebeast.com", "elle.com",
    "cosmopolitan.com", "people.com", "eonline.com",
}


def _build_query(term: str) -> str:
    """
    Builds a broad search query for the term's shirt category.

    "Scooby Doo Creepy Run" → "Scooby Doo shirt"
    "Jujutsu Kaisen"        → "Jujutsu Kaisen shirt"
    """
    filler = {
        "the", "a", "an", "of", "in", "on", "at", "to", "for", "and",
        "meme", "trend", "viral", "moment", "remix", "saga",
        "creepy", "realistic", "funny", "classic", "original", "new",
        "challenge", "check", "era",
    }
    cleaned = term.replace("(", " ").replace(")", " ").replace("-", " ")
    words = [w for w in cleaned.lower().split() if w not in filler and len(w) > 1]
    core = " ".join(words[:3]) if words else term.lower()[:30]
    return f"{core} shirt"


def _fetch_image_urls(query: str, api_key: str, limit: int = 10) -> list[dict]:
    try:
        resp = requests.get(
            _SERPAPI_ENDPOINT,
            params={
                "engine": "google_images",
                "q": query,
                "api_key": api_key,
                "num": limit + 5,
                "safe": "active",
                "tbs": "isz:m",
            },
            timeout=15,
        )
        resp.raise_for_status()
        data = resp.json()
        if "error" in data:
            print(f"  [refs] SerpAPI error: {data['error']}")
            return []

        results = []
        for r in data.get("images_results", []):
            url = r.get("original") or r.get("thumbnail")
            if not url:
                continue
            source = (r.get("source") or "").lower()
            if any(b in source for b in _BLOCKED_SOURCES):
                continue
            results.append({"url": url, "title": r.get("title", ""), "source": source})
        return results[:limit]
    except Exception as e:
        print(f"  [refs] Fetch error: {e}")
        return []


def _download(url: str) -> Optional[bytes]:
    try:
        resp = requests.get(url, headers=_HEADERS, timeout=10)
        resp.raise_for_status()
        if len(resp.content) < _MIN_IMAGE_SIZE:
            return None
        if not (
            resp.content[:8] == b'\x89PNG\r\n\x1a\n'
            or resp.content[:3] == b'\xff\xd8\xff'
            or resp.content[:4] == b'RIFF'
            or resp.content[:4] == b'GIF8'
        ):
            return None
        return resp.content
    except Exception:
        return None


def fetch_reference_images(
    term: str,
    serpapi_key: Optional[str] = None,
    max_images: int = 5,
) -> list[bytes]:
    """
    Fetches reference shirt design images for a trend term.
    Returns raw image bytes, or empty list if insufficient references found.
    """
    api_key = serpapi_key or os.getenv("SERPAPI_KEY", "").strip()
    if not api_key:
        print("  [refs] No SERPAPI_KEY")
        return []

    query = _build_query(term)
    print(f"  [refs] Searching: '{query}'")

    candidates = _fetch_image_urls(query, api_key, limit=max_images + 5)
    if not candidates:
        print("  [refs] No image results")
        return []

    images: list[bytes] = []
    sources_used: list[str] = []
    for candidate in candidates:
        if len(images) >= max_images:
            break
        img = _download(candidate["url"])
        if img:
            images.append(img)
            sources_used.append(candidate["source"])

    if len(images) < MIN_VALID_REFERENCES:
        print(f"  [refs] Only {len(images)} valid images (need {MIN_VALID_REFERENCES}) — skipping trend")
        return []

    print(f"  [refs] Got {len(images)} references from: {', '.join(sources_used[:3])}")
    return images