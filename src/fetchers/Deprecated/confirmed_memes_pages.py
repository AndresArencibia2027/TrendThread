#!/usr/bin/env python3
"""
Helpers for importing KnowYourMeme *confirmed* meme pages (sorted by newest).

Based on:
- https://knowyourmeme.com/memes?kind=confirmed&sort=newest
- https://knowyourmeme.com/memes/page/2?kind=confirmed&sort=newest
"""

from pathlib import Path
import urllib3

try:
    from bs4 import BeautifulSoup
except ImportError:
    BeautifulSoup = None

BASE_URL = "https://knowyourmeme.com/memes"
QUERY = "kind=confirmed&sort=newest"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    )
}


def get_confirmed_page_url(page_index: int) -> str:
    """
    Build the URL for a confirmed/newest meme page.

    Page 1:
      https://knowyourmeme.com/memes?kind=confirmed&sort=newest
    Page 2+:
      https://knowyourmeme.com/memes/page/{page}?kind=confirmed&sort=newest
    """
    if page_index <= 1:
        return f"{BASE_URL}?{QUERY}"
    return f"{BASE_URL}/page/{page_index}?{QUERY}"


def fetch_page(page_index: int):
    """
    Fetch a single confirmed/newest memes page.
    Returns (page_index, url, status_code, html_text or error_string).
    """
    url = get_confirmed_page_url(page_index)
    http = urllib3.PoolManager()
    try:
        resp = http.request("GET", url, headers=HEADERS, timeout=20)
        html = resp.data.decode("utf-8", errors="replace")
        return page_index, url, resp.status, html
    except Exception as e:
        return page_index, url, None, str(e)


def extract_entries_from_html(html: str):
    """
    Extract meme entries (title, meme_url, image_url) from a confirmed/newest page's HTML.

    Returns list of dicts:
      {"title": ..., "meme_url": ..., "image_url": ... or None}
    """
    if not BeautifulSoup:
        raise ImportError("BeautifulSoup (bs4) required. Install with: pip install beautifulsoup4")

    soup = BeautifulSoup(html, "html.parser")
    entries = []

    # Strategy:
    # - Find all <a> with /memes/ in href (but not navigation like /memes/page/)
    # - For each, use its text as title and nearest <img> as thumbnail.
    all_links = soup.find_all("a", href=True)
    excluded_paths = ["/memes/page/", "/memes/new", "/memes/popular", "/memes/submissions"]

    seen_urls = set()

    for link in all_links:
        href = link.get("href", "")
        if "/memes/" not in href:
            continue
        # Skip obvious navigation / category links
        if any(ex in href for ex in excluded_paths):
            continue
        # Require exactly one /memes/ so we don't grab category links etc.
        if href.count("/memes/") != 1:
            continue

        if href.startswith("http"):
            meme_url = href
        elif href.startswith("/"):
            meme_url = "https://knowyourmeme.com" + href
        else:
            meme_url = "https://knowyourmeme.com/" + href

        if meme_url in seen_urls:
            continue
        seen_urls.add(meme_url)

        # Title: link text, cleaned up
        title = link.get_text(strip=True) or meme_url.rsplit("/", 1)[-1].replace("-", " ").title()

        # Try to find a related image: inside the link, or in a close parent
        img_tag = link.find("img")
        if not img_tag:
            parent = link.parent
            for _ in range(3):  # climb up a few levels
                if not parent:
                    break
                img_tag = parent.find("img")
                if img_tag:
                    break
                parent = parent.parent if hasattr(parent, "parent") else None

        img_url = None
        if img_tag:
            src = img_tag.get("src") or img_tag.get("data-src")
            if src:
                if src.startswith("http"):
                    img_url = src
                else:
                    # KYM images are usually on i.kym-cdn.com
                    if src.startswith("//"):
                        img_url = "https:" + src
                    else:
                        img_url = "https://knowyourmeme.com" + src

        entries.append(
            {
                "title": title,
                "meme_url": meme_url,
                "image_url": img_url,
            }
        )

    return entries


def fetch_pages(start_page: int = 1, end_page: int = 2):
    """
    Fetch a range of confirmed/newest meme pages (inclusive).

    Defaults to pages 1–2 (the URLs you provided).
    Returns dict: page_index -> {"url", "status", "html"}.
    """
    results = {}
    for i in range(start_page, end_page + 1):
        page_idx, url, status, html = fetch_page(i)
        results[page_idx] = {"url": url, "status": status, "html": html}
    return results


def fetch_entries(start_page: int = 1, end_page: int = 2):
    """
    Convenience helper: fetch pages and extract entries (title + image URL).

    Returns list of dicts:
      {"page": page_index, "title": ..., "meme_url": ..., "image_url": ...}
    """
    results = []
    for i in range(start_page, end_page + 1):
        page_idx, url, status, html = fetch_page(i)
        if status == 200 and html:
            for e in extract_entries_from_html(html):
                e["page"] = page_idx
                results.append(e)
    return results


def fetch_pages_as_soup(start_page: int = 1, end_page: int = 2):
    """
    Fetch a range of pages and parse them with BeautifulSoup.
    Returns dict: page_index -> soup (or None if fetch/parse failed).
    """
    if not BeautifulSoup:
        raise ImportError("BeautifulSoup (bs4) required. Install with: pip install beautifulsoup4")

    soups = {}
    for i in range(start_page, end_page + 1):
        page_idx, url, status, html = fetch_page(i)
        if status == 200 and html:
            soups[page_idx] = BeautifulSoup(html, "html.parser")
        else:
            soups[page_idx] = None
    return soups


def list_sample_urls(max_pages: int = 5):
    """Print sample confirmed/newest meme page URLs up to max_pages."""
    print("KnowYourMeme confirmed/newest meme pages:")
    for i in range(1, max_pages + 1):
        print(f"  page {i}: {get_confirmed_page_url(i)}")


if __name__ == "__main__":
    # Show URLs and fetch the first two pages by default
    list_sample_urls(max_pages=2)
    print()
    print("Fetching pages 1–2...")
    pages = fetch_pages(1, 2)
    for idx in sorted(pages):
        data = pages[idx]
        status = data["status"]
        size = len(data.get("html") or "")
        print(f"  page {idx}: status={status}, size={size} chars, url={data['url']}")

    print()
    print("Extracting entries (titles + image URLs) from pages 1–2...")
    entries = fetch_entries(1, 2)
    print(f"  Found {len(entries)} entries")
    for e in entries[:10]:
        print(f"  [page {e['page']}] {e['title']} -> {e['image_url']}")

