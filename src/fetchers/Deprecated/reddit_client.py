"""
reddit_client.py
================
Fetches trending cultural moments from Reddit as the PRIMARY trend source.

Why Reddit is better than Gemini hallucinations:
  r/OutOfTheLoop new posts titled "What is X?" are verified mainstream
  crossing moments — real people asking about real things they're seeing
  everywhere. High upvote score = lots of people had the same question.

  r/memes and r/dankmemes hot posts show what's actually being made/shared.
  r/popculturechat shows what's being discussed in mainstream culture.

Returns trend candidates with scores for ranking, ready to feed directly
into distill_search_terms as the primary input.
"""

import re
import time
from typing import Optional

import requests

_HEADERS = {
    "User-Agent": "TrendThread/1.0 (trend research bot)"
}
_TIMEOUT = 15

# Subreddits and their weights — higher weight = more reliable trend signal
_SOURCES = [
    # (subreddit, sort, weight, description)
    ("OutOfTheLoop", "new",  3.0, "mainstream crossover — 'What is X?'"),
    ("OutOfTheLoop", "hot",  2.5, "mainstream crossover — hot"),
    ("memes",        "hot",  2.0, "active meme formats"),
    ("dankmemes",    "hot",  1.5, "meme culture"),
    ("popculturechat", "hot", 1.5, "pop culture discourse"),
]

# Strip question framing from OutOfTheLoop titles
_QUESTION_CLEANUP = re.compile(
    r"^\s*(?:what(?:'?s| is| are)|who(?:'?s| is| are)|why is|why are|"
    r"can someone explain|eli5|help me understand)\s+",
    re.IGNORECASE,
)
_PUNCTUATION_CLEANUP = re.compile(r'[?!]+$')

# Hard-block terms that are never shirt-worthy
_BLOCKLIST = {
    "politics", "election", "trump", "biden", "congress", "senate",
    "shooting", "death", "died", "killed", "accident", "arrested",
    "lawsuit", "court", "trial", "crime", "disease", "virus",
    "stock", "crypto", "bitcoin", "economy", "inflation",
    "weekly thread", "megathread", "mod post", "announcement",
    "discord", "subreddit", "reddit",
}


def _is_blocked(title: str) -> bool:
    t = title.lower()
    return any(b in t for b in _BLOCKLIST)


def _clean_title(title: str, is_outoftheloop: bool = False) -> str:
    """Cleans a Reddit post title into a usable trend term."""
    cleaned = title.strip()
    if is_outoftheloop:
        cleaned = _QUESTION_CLEANUP.sub("", cleaned)
    cleaned = _PUNCTUATION_CLEANUP.sub("", cleaned)
    # Remove "the" prefix
    cleaned = re.sub(r'^the\s+', '', cleaned, flags=re.IGNORECASE)
    cleaned = cleaned.strip(" ,.-")
    return cleaned


def _fetch_subreddit_posts(
    subreddit: str,
    sort: str = "hot",
    limit: int = 25,
) -> list[dict]:
    """
    Fetches posts from a subreddit. Returns list of
    {"title": str, "score": int, "num_comments": int, "url": str}
    """
    url = f"https://www.reddit.com/r/{subreddit}/{sort}.json?limit={limit}"
    try:
        resp = requests.get(url, headers=_HEADERS, timeout=_TIMEOUT)
        resp.raise_for_status()
        posts = resp.json().get("data", {}).get("children", [])

        results = []
        for post in posts:
            d = post.get("data", {})
            if d.get("stickied") or d.get("score", 0) < 20:
                continue
            title = d.get("title", "").strip()
            if not title or len(title) < 5:
                continue
            results.append({
                "title": title,
                "score": d.get("score", 0),
                "num_comments": d.get("num_comments", 0),
                "subreddit": subreddit,
                "url": f"https://reddit.com{d.get('permalink', '')}",
            })
        return results
    except Exception as e:
        print(f"  [reddit] Error fetching r/{subreddit}/{sort}: {e}")
        return []


def get_trending_terms(limit: int = 30) -> list[dict]:
    """
    PRIMARY trend source. Fetches Reddit posts across key subreddits
    and returns them as scored trend candidates.

    Returns:
        List of dicts sorted by composite score (descending):
        [{
            "term": str,          # cleaned title
            "momentum": float,    # composite score
            "velocity": float,    # always 1.0 (Reddit is real-time)
            "source": str,        # subreddit name
            "raw_score": int,     # upvote score
            "context": str,       # original title for context
        }]
    """
    all_posts = []

    for subreddit, sort, weight, desc in _SOURCES:
        posts = _fetch_subreddit_posts(subreddit, sort=sort, limit=30)
        for post in posts:
            all_posts.append({
                **post,
                "weight": weight,
                "sort": sort,
            })
        time.sleep(0.3)  # be polite

    # Deduplicate by cleaned title, keeping highest composite score
    seen: dict[str, dict] = {}
    for post in all_posts:
        is_ootl = post["subreddit"] == "OutOfTheLoop"
        cleaned = _clean_title(post["title"], is_outoftheloop=is_ootl)

        if len(cleaned) < 4 or _is_blocked(cleaned):
            continue

        # Composite score: upvotes × weight + comments bonus
        composite = (post["score"] * post["weight"]) + (post["num_comments"] * 0.5)
        key = cleaned.lower()

        if key not in seen or composite > seen[key]["momentum"]:
            seen[key] = {
                "term": cleaned,
                "momentum": composite,
                "velocity": 1.0,
                "source": f"reddit/r/{post['subreddit']}",
                "raw_score": post["score"],
                "context": post["title"],
            }

    results = sorted(seen.values(), key=lambda x: x["momentum"], reverse=True)
    print(f"  [reddit] {len(results)} trend candidates from Reddit")
    return results[:limit]


def get_reddit_signals(max_per_subreddit: int = 15) -> list[dict]:
    """
    Legacy interface — returns Reddit data as extra_sources format
    for supplemental use in distill_search_terms.
    """
    sources = []
    for subreddit in ["OutOfTheLoop", "popculturechat"]:
        posts = _fetch_subreddit_posts(subreddit, sort="hot", limit=max_per_subreddit + 5)
        titles = [
            _clean_title(p["title"], is_outoftheloop=(subreddit == "OutOfTheLoop"))
            for p in posts
            if not _is_blocked(p["title"])
        ][:max_per_subreddit]
        if titles:
            sources.append({"source_name": f"Reddit r/{subreddit}", "items": titles})
        time.sleep(0.3)

    total = sum(len(s["items"]) for s in sources)
    print(f"  [reddit] {total} supplemental signals")
    return sources