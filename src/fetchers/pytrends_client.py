"""
pytrends_client.py
==================
Fetches trending cultural moments using Gemini web grounding.

Google Trends blocks all non-browser requests. pytrends is broken against
current Google APIs. SerpAPI discontinued their free trends endpoint.

Instead, we use Gemini 2.0 Flash with Google Search grounding to fetch
what's actually trending right now — the same approach that powers our
trend research step, applied earlier in the pipeline to discover terms.

This requires the Gemini client, which is passed in from review_app.py.
The fallback (if no client provided) returns [] so the pipeline degrades
gracefully to Reddit-only signal.
"""

import json
import os
import re

_TRENDING_SYSTEM = """
You are a trend scout for a print-on-demand merchandise shop.

Search the web for what is genuinely NEW and trending RIGHT NOW this week.

WHAT WE WANT — things that didn't exist or weren't being talked about 3 weeks ago:
- A meme that started in the last 2-3 weeks and is actively spreading
- A pop culture moment from a recent release generating huge reaction
- A viral video/moment from this week people keep referencing
- A celebrity moment from this week generating ironic/funny commentary
- A specific quote or scene from something released recently that went viral

WHAT WE DO NOT WANT — do not include any of these:
- Classic movie quotes ("Run Forrest run", "I'll be back", "May the Force be with you")
- Evergreen memes that have existed for years (distracted boyfriend, etc.)
- Trends from more than 3 weeks ago
- TikTok audio/dance formats
- Social media behavior trends
- News events (deaths, crimes, politics, disasters)
- Vague categories ("retro nostalgia", "dark humor")
- Sports players by name with no specific viral moment

QUALITY TEST for each term:
Would someone who doesn't follow social media closely ask
"wait what is that?" if they saw it on a shirt this week?
If yes — it's current enough. If no (they'd recognize it as old) — skip it.

Return ONLY a clean JSON array of 20 specific terms, newest/most viral first.
2-6 words each. No descriptions, no markdown, no numbering:
["term one", "term two", ...]
"""


def get_realtime_trends(limit: int = 40, gemini_client=None) -> list[dict]:
    """
    Fetches currently trending topics using Gemini web grounding.

    Args:
        limit:          Max number of terms to return
        gemini_client:  Initialized Gemini client. If None, returns [].

    Returns:
        [{"term": str, "momentum": float, "velocity": float, "source": "gemini_trends"}]
    """
    if gemini_client is None:
        print("  [trends] No Gemini client provided — skipping trend fetch")
        return []

    try:
        from google.genai import types

        response = gemini_client.models.generate_content(
            model="gemini-2.0-flash",
            config=types.GenerateContentConfig(
                tools=[types.Tool(google_search=types.GoogleSearch())],
                response_modalities=["TEXT"],
                system_instruction=_TRENDING_SYSTEM,
            ),
            contents="Search Reddit r/OutOfTheLoop new posts, Twitter/X trending topics, and Know Your Meme new entries from THIS WEEK. What NEW memes, viral moments, and pop culture references started trending in the last 7-14 days? Focus on things that are fresh, not classic or evergreen content.",
        )

        raw = response.text.strip()

        # Find the JSON array anywhere in the response — Gemini often adds
        # prose before/after the array despite instructions not to
        array_match = re.search(r'\[.*?\]', raw, re.DOTALL)
        if array_match:
            raw = array_match.group(0)
        else:
            # Strip markdown fences and try the whole thing
            raw = re.sub(r"^```[a-z]*\n?", "", raw)
            raw = re.sub(r"\n?```$", "", raw)

        terms_list = json.loads(raw)
        if not isinstance(terms_list, list):
            raise ValueError("Response was not a list")

        results = []
        for i, term in enumerate(terms_list[:limit]):
            term = str(term).strip().strip('"\'')
            if not term or len(term) < 3:
                continue
            results.append({
                "term": term,
                "momentum": float(max(1, limit - i)),
                "velocity": 1.0,
                "source": "gemini_trends",
            })

        print(f"  [trends] {len(results)} trending topics via Gemini web search")
        return results

    except json.JSONDecodeError:
        # Gemini returned markdown/numbered list instead of JSON
        # Extract just the bolded term name from each line, not the full description
        results = []
        for line in response.text.split("\n"):
            line = line.strip()
            if not line:
                continue
            # Extract **bold text** which is the trend name
            bold_match = re.search(r'\*\*([^*]+)\*\*', line)
            if bold_match:
                term = bold_match.group(1).strip().strip('"\'')
                # Skip if it's a description (too long or contains colons)
                if term and len(term) < 60 and ":" not in term:
                    results.append({
                        "term": term,
                        "momentum": float(max(1, limit - len(results))),
                        "velocity": 1.0,
                        "source": "gemini_trends",
                    })
            if len(results) >= limit:
                break

        if results:
            print(f"  [trends] {len(results)} terms extracted from markdown response")
            return results

        print("  [trends] Could not parse Gemini response")
        return []

    except Exception as e:
        print(f"  [trends] Error: {e}")
        return []