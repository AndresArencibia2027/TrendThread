import json
import os
from datetime import datetime, timedelta, timezone
from urllib.parse import urlencode

import requests

GDELT_DOC_ENDPOINT = "https://api.gdeltproject.org/api/v2/doc/doc"

def fmt_gdelt_dt(dt: datetime) -> str:
    # GDELT expects UTC timestamps like YYYYMMDDHHMMSS
    return dt.strftime("%Y%m%d%H%M%S")

def fetch_gdelt_articles(query: str, minutes_back: int = 60, maxrecords: int = 50) -> dict:
    now = datetime.now(timezone.utc)
    start = now - timedelta(minutes=minutes_back)

    params = {
        "query": query,
        "mode": "ArtList",
        "format": "json",
        "maxrecords": str(maxrecords),
        "startdatetime": fmt_gdelt_dt(start),
        "enddatetime": fmt_gdelt_dt(now),
        "sort": "HybridRel",
    }

    url = f"{GDELT_DOC_ENDPOINT}?{urlencode(params)}"
    response = requests.get(url, timeout=30)
    response.raise_for_status()
    return response.json()

def save_snapshot(payload: dict, out_dir: str = "data/raw") -> str:
    os.makedirs(out_dir, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    path = os.path.join(out_dir, f"gdelt_doc_{ts}.json")

    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)

    return path

if __name__ == "__main__":
    query = '(tiktok OR meme OR viral OR breaking OR sports)'
    data = fetch_gdelt_articles(query=query, minutes_back=60, maxrecords=50)

    out_path = save_snapshot(data)
    articles = data.get("articles", [])

    print("Saved file:", out_path)
    print("Number of articles:", len(articles))

    # Print the first title so you can see it worked
    if len(articles) > 0:
        print("Example title:", articles[0].get("title"))
