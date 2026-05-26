import json
import os
from datetime import datetime, timedelta, timezone
from urllib.parse import urlencode
import requests

GDELT_DOC_ENDPOINT = "https://api.gdeltproject.org/api/v2/doc/doc"

def _fmt_gdelt_dt(dt: datetime) -> str:
    """Internal helper: GDELT expects UTC timestamps like YYYYMMDDHHMMSS"""
    return dt.strftime("%Y%m%d%H%M%S")

def fetch_gdelt_articles(query: str, minutes_back: int = 60, maxrecords: int = 50):
    """
    Fetches articles from the GDELT Doc API based on a query.
    
    Returns:
        list: A list of article dictionaries, or an empty list if none found.
    """
    now = datetime.now(timezone.utc)
    start = now - timedelta(minutes=minutes_back)

    params = {
        "query": query,
        "mode": "ArtList",
        "format": "json",
        "maxrecords": str(maxrecords),
        "startdatetime": _fmt_gdelt_dt(start),
        "enddatetime": _fmt_gdelt_dt(now),
        "sort": "HybridRel",
    }

    try:
        url = f"{GDELT_DOC_ENDPOINT}?{urlencode(params)}"
        response = requests.get(url, timeout=30)
        response.raise_for_status()
        
        data = response.json()
        articles = data.get("articles", [])
        return articles

    except Exception as e:
        print(f"GDELT Error: {e}")
        return []

def save_gdelt_snapshot(articles: list, out_dir: str = "data/raw"):
    """
    Saves the fetched articles to a timestamped JSON file.
    
    Returns:
        str: The path to the saved file.
    """
    if not articles:
        return None

    os.makedirs(out_dir, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    path = os.path.join(out_dir, f"gdelt_snapshot_{ts}.json")

    payload = {
        "timestamp_utc": ts,
        "article_count": len(articles),
        "articles": articles
    }

    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)

    return path