"""
trends_client.py
================
Primary trend discovery via Google Trends Trending Now (SerpAPI), with
a curated categories fallback when Google Trends returns weak results.

Why this beats Reddit:
  Reddit (especially r/OutOfTheLoop) skews heavily toward news and current
  events — controversies, crime, politics, deaths. These don't make
  marketable shirt designs.

  Google Trends "Trending Now" surfaces what people are actively SEARCHING
  for. People search for things they want to buy, watch, look up, or learn
  about. Filtered by category (entertainment, games, fashion, hobbies),
  this gives us actual marketable trends: new shows, viral characters,
  game releases, fashion drops, memes that crossed mainstream.

Two functions:
  get_trending_terms(limit)  — primary: Google Trends, US, last 24h,
                                filtered to wearable categories
  get_curated_seeds(n)       — fallback: Gemini-grounded discovery within
                                a curated list of evergreen-but-fresh
                                trend universes (anime, indie games,
                                cult media, viral phrases, fashion)
"""

import os
import time
from typing import Optional

import requests

_SERPAPI_ENDPOINT = "https://serpapi.com/search"
_TIMEOUT = 20

# Google Trends Trending Now category IDs.
# Source: https://serpapi.com/google-trends-trending-now-categories
# We target categories that produce marketable shirt content,
# skipping news-adjacent ones (Sports, Top stories, Politics, Finance).
WEARABLE_CATEGORIES = [
    (3,  "Entertainment"),       # Movies, TV, music, celebrities (viral moments)
    (4,  "Beauty and Fashion"),  # Fashion drops, style trends
    (6,  "Games"),               # Video games, esports moments, characters
    (18, "Hobbies and Leisure"), # Hobby communities — anime, collectibles
    (20, "Pets and Animals"),    # Viral animal moments, cute aesthetic
]

# Sources to never include — keywords that signal news/promotions/utility
# even when they leak through category filters.
_HARD_BLOCKLIST = {
    # Death / illness / medical
    "death", "died", "killed", "shooting", "shot", "murder", "obituary",
    "passed away", "rip ", "funeral", "cancer", "diagnosis", "hospital",
    "colonoscopy", "surgery", "rehab", "overdose",
    # Crime / legal
    "arrested", "indicted", "lawsuit", "court", "trial", "guilty",
    "verdict", "charged with", "sentenced", "convicted",
    "documentary",  # crime documentaries are most "documentary" queries
    # Politics
    "trump", "biden", "harris", "election", "congress", "senate", "vote",
    "republican", "democrat", "primary",
    # War / disasters
    "war", "ukraine", "russia", "israel", "gaza", "iran",
    "storm", "hurricane", "earthquake", "tornado", "wildfire", "flood",
    # Finance / markets
    "stock", "crypto", "bitcoin", "market crash", "recession", "layoff",
    "bankruptcy", "earnings",
    # Business closures / promotions
    "closure", "closing", "shuts down", "going out of business",
    "free coffee", "free food", "giveaway", "promotion",
    "sale ends", "black friday", "cyber monday",
    # Service outages / utility
    "is down", "not working", "outage", "is broken",
    # Weather/sports filler
    "weather", "forecast", "scores", "results",
    # Health misc
    "covid", "virus", "outbreak", "recall",
}


def _is_blocked(query: str) -> bool:
    q = query.lower()
    return any(b in q for b in _HARD_BLOCKLIST)


# ---------------------------------------------------------------------------
# Primary source: Google Trends Trending Now
# ---------------------------------------------------------------------------
def _fetch_trending_for_category(
    api_key: str,
    category_id: int,
    geo: str = "US",
    hours: int = 24,
) -> list[dict]:
    """Fetches trending searches for one category."""
    try:
        resp = requests.get(
            _SERPAPI_ENDPOINT,
            params={
                "engine": "google_trends_trending_now",
                "geo": geo,
                "hours": hours,
                "category_id": category_id,
                "api_key": api_key,
            },
            timeout=_TIMEOUT,
        )
        resp.raise_for_status()
        data = resp.json()
        if "error" in data:
            print(f"  [trends] SerpAPI error (cat {category_id}): {data['error']}")
            return []
        return data.get("trending_searches", []) or []
    except Exception as e:
        print(f"  [trends] Fetch error (cat {category_id}): {e}")
        return []


def get_trending_terms(
    limit: int = 40,
    serpapi_key: Optional[str] = None,
    geo: str = "US",
    hours: int = 24,
) -> list[dict]:
    """
    Primary trend source. Fetches Google Trends Trending Now across
    wearable categories and returns deduplicated trend candidates
    sorted by search volume.

    Each item: { term, momentum, velocity, source, raw_score, context }

    `momentum` is search_volume (absolute popularity).
    `velocity` is increase_percentage (how fast it's growing).
    `context` includes related queries (trend_breakdown) — useful for
    the wearability filter to understand the specific angle.
    """
    api_key = serpapi_key or os.getenv("SERPAPI_KEY", "").strip()
    if not api_key:
        print("  [trends] No SERPAPI_KEY")
        return []

    print(f"  [trends] Fetching Google Trends across {len(WEARABLE_CATEGORIES)} categories…")
    all_trends: dict[str, dict] = {}

    for cat_id, cat_name in WEARABLE_CATEGORIES:
        searches = _fetch_trending_for_category(api_key, cat_id, geo, hours)
        for s in searches:
            query = (s.get("query") or "").strip()
            if not query or _is_blocked(query):
                continue

            volume = int(s.get("search_volume") or 0)
            increase = int(s.get("increase_percentage") or 0)

            # Related queries help the wearability filter understand
            # what the trend is actually about
            breakdown = s.get("trend_breakdown") or []
            context = " | ".join(breakdown[:5]) if breakdown else ""

            # Also block based on related queries — many news trends
            # have an innocent-looking term but their breakdown reveals
            # the news angle ("Mackenzie Shirilla" + "murder case" etc.)
            if context and _is_blocked(context):
                continue

            key = query.lower()
            if key in all_trends and all_trends[key]["momentum"] >= volume:
                continue

            all_trends[key] = {
                "term": query,
                "momentum": float(volume),
                "velocity": float(increase) / 100.0,
                "source": f"google_trends/{cat_name.lower().replace(' ', '_')}",
                "raw_score": volume,
                "context": context,
            }
        time.sleep(0.3)

    results = sorted(all_trends.values(), key=lambda x: x["momentum"], reverse=True)
    print(f"  [trends] {len(results)} unique trends from Google Trends")

    if results:
        print(f"  [trends] Top 5:")
        for r in results[:5]:
            print(f"    - {r['term']} ({r['raw_score']:,} searches, +{r['velocity']*100:.0f}%)")

    return results[:limit]


# ---------------------------------------------------------------------------
# Fallback source: curated trend universes + Gemini grounding
# ---------------------------------------------------------------------------

# Curated list of "trend universes" — categories of content that consistently
# produce wearable, marketable trends. When Google Trends returns weak results,
# Gemini searches within these universes for what's currently hot.
TREND_UNIVERSES = [
    "currently airing or recently released anime with viral moments",
    "newly released or trending video game characters",
    "viral memes spreading on TikTok and Twitter this week",
    "iconic moments from recently released movies and TV shows",
    "trending sneaker drops and streetwear collaborations",
    "viral catchphrases from current pop culture",
    "currently popular kpop and music artist moments",
]

_GROUNDING_SYSTEM = """
You are a trend scout for a print-on-demand merchandise shop.

You will be given a TREND UNIVERSE to search within. Use Google Search to find
what is currently viral within that universe — specifically, things people
would put on a shirt.

For each trend you find, output one line in this format:
TERM: [2-6 words, the specific viral thing] | CONTEXT: [one sentence — what it is and why it's hot]

Output 3-5 specific trends per universe. Hard rules:
- Must be currently viral (last 2-4 weeks)
- Must have a recognizable visual identity (character, scene, phrase, item)
- Must be wearable — no news events, no tragedies, no politics
- No vague concepts ("nostalgia is back") — only specific recognizable things

OUTPUT — only the lines, no preamble:
TERM: [...] | CONTEXT: [...]
"""


def get_curated_seeds(
    gemini_client,
    n_universes: int = 3,
) -> list[dict]:
    """
    Fallback trend discovery using Gemini web grounding within curated
    trend universes. Called when Google Trends returns insufficient
    wearable trends.

    Picks N random universes from TREND_UNIVERSES, queries Gemini with
    grounding enabled, and parses the results into the same format as
    get_trending_terms().
    """
    if gemini_client is None:
        print("  [seeds] No Gemini client — cannot run fallback")
        return []

    import random
    import re
    from google.genai import types

    universes = random.sample(TREND_UNIVERSES, min(n_universes, len(TREND_UNIVERSES)))
    print(f"  [seeds] Querying {len(universes)} curated trend universes via Gemini grounding…")

    results: list[dict] = []
    for universe in universes:
        try:
            response = gemini_client.models.generate_content(
                model="gemini-2.0-flash",
                config=types.GenerateContentConfig(
                    tools=[types.Tool(google_search=types.GoogleSearch())],
                    response_modalities=["TEXT"],
                    system_instruction=_GROUNDING_SYSTEM,
                ),
                contents=(
                    f"Search the web for current viral trends in this universe: "
                    f"{universe}. What are the 3-5 most specific viral moments "
                    f"right now that would work as shirt designs?"
                ),
            )
            text = response.text or ""
            matches = re.findall(
                r"TERM:\s*(.*?)\s*\|\s*CONTEXT:\s*(.*?)(?:\n|$)",
                text,
            )
            for term, context in matches:
                term = term.strip().strip('"\'')
                if not term or _is_blocked(term):
                    continue
                results.append({
                    "term": term,
                    "momentum": 100.0,   # placeholder; no real volume data
                    "velocity": 1.0,
                    "source": f"curated/{universe[:30]}",
                    "raw_score": 0,
                    "context": context.strip(),
                })
        except Exception as e:
            print(f"  [seeds] Error for '{universe[:40]}': {e}")

    # Deduplicate by term
    seen = set()
    unique = []
    for r in results:
        key = r["term"].lower()
        if key in seen:
            continue
        seen.add(key)
        unique.append(r)

    print(f"  [seeds] {len(unique)} curated trends discovered")
    if unique:
        print(f"  [seeds] Sample:")
        for r in unique[:5]:
            print(f"    - {r['term']}")
    return unique


# ---------------------------------------------------------------------------
# Public entry — combines both sources
# ---------------------------------------------------------------------------
def discover_trends(
    gemini_client=None,
    limit: int = 40,
    min_google_trends: int = 15,
) -> list[dict]:
    """
    Returns trend candidates from Google Trends (primary). If Google Trends
    returns fewer than min_google_trends, supplements with curated seeds.

    Same return format as the old get_trending_terms() — drop-in replacement.
    """
    google_results = get_trending_terms(limit=limit)

    if len(google_results) >= min_google_trends:
        return google_results

    print(f"  [discover] Only {len(google_results)} Google Trends results — adding curated seeds")
    curated = get_curated_seeds(gemini_client, n_universes=3)

    # Combine with Google Trends first (real signal) then curated (fallback)
    seen = {r["term"].lower() for r in google_results}
    combined = list(google_results)
    for r in curated:
        if r["term"].lower() not in seen:
            combined.append(r)
            seen.add(r["term"].lower())

    return combined[:limit]