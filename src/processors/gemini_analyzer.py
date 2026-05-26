"""
gemini_analyzer.py
==================
Trend filtering and theme distillation.

  distill_search_terms — pick 5 wearable motifs from raw trend data
                         (Google Trends + curated seeds)
  distill_theme_terms  — generate 5 unique motifs for a user theme

Each motif is { term, topic_type } — that's all downstream needs.
The wearability filter now reads `context` (related queries from
Google Trends) to distinguish news from culture.
"""

import os
import re
from typing import Optional

from google import genai
from google.genai import types


TOPIC_TYPES = {
    "FICTIONAL_CHARACTER",
    "VIRAL_PHRASE",
    "REAL_PERSON",
    "SPORTING_EVENT",
    "ABSTRACT_MEME",
    "GENERAL",
}


def get_client() -> genai.Client:
    return genai.Client(
        vertexai=True,
        project=os.getenv("VERTEX_PROJECT_ID"),
        location=os.getenv("VERTEX_LOCATION", "us-central1"),
    )


# ---------------------------------------------------------------------------
# Stage 1a — Trend marketability filter
# ---------------------------------------------------------------------------
def distill_search_terms(
    client,
    bq_context: list[dict],
    extra_sources: Optional[list[dict]] = None,
) -> list[dict]:
    """
    Picks up to 5 wearable motifs from trend data.
    Returns [{ term, topic_type }] for downstream processing.
    """
    if not bq_context:
        print("  [Stage1] No trend data.")
        return []

    lines = []
    for item in bq_context:
        term = item["term"]
        score = item.get("momentum", 0)
        context = item.get("context", "")
        source = item.get("source", "")
        if context:
            lines.append(f'- "{term}" — {context[:120]} [{source}, {score:.0f}]')
        else:
            lines.append(f'- "{term}" [{source}, {score:.0f}]')
    trend_lines = "\n".join(lines)

    system_instruction = """
You are a creative director for a trend-driven print-on-demand shop.

You will be given trending search terms with related queries shown after the —.
The related queries reveal what the trend is actually about. Use them to
distinguish CULTURE (wearable) from NEWS (not wearable).

WEARABILITY TEST — ALL must be true:
1. ONE specific, recognizable thing to draw (character, icon, symbol, phrase)
2. Wearing it signals "I'm in on this" — cultural membership or humor
3. Has clear visual identity — Google Images would return real shirt designs
4. Original design possible (not requiring exact licensed reproduction)

HARD REJECTS — these are NOT wearable and must be discarded:

NEWS & CURRENT EVENTS:
- Deaths, illnesses, medical events ("X colonoscopy", "X passes away")
- Crimes, arrests, lawsuits, trials, court cases
- Closures, layoffs, bankruptcies ("X closure", "X store closing")
- Disasters, accidents, weather events
- Political news, elections, controversies
- Documentaries about real events (especially crime documentaries)
- Anything where related queries include "what happened", "explained",
  "update", "news", "death", "closure", "arrested"

PROMOTIONAL EVENTS:
- Brand giveaways and free promotions ("X free coffee day")
- Store openings, store events, sales, deals
- "[brand] [date]" patterns are usually promotions, not culture
- Product launches without a clear viral cultural moment

GENERIC/UTILITY:
- Status check queries ("is X down", "X not working")
- Service outages ("reddit down", "twitter outage")
- Weather, sports scores, stock prices

CELEBRITY LIFE EVENTS (not their work):
- Divorces, scandals, illness, surgery, legal issues
- Personal life drama disconnected from their art/work

WEARABLE EXAMPLES (these would pass):
- A character from a current viral show or game
- A meme with a specific recognizable image
- A catchphrase from current pop culture
- A specific iconic moment from a recently released movie/show
- A fashion drop or sneaker collab with visual identity

If fewer than 5 pass the test, output FEWER than 5. Do NOT pad weak entries.
It's far better to return 2 strong motifs than 5 mixed-quality ones.

TOPIC_TYPE — exactly one of these tokens:
FICTIONAL_CHARACTER | VIRAL_PHRASE | REAL_PERSON | SPORTING_EVENT | ABSTRACT_MEME | GENERAL

OUTPUT FORMAT — strict. One line per passing term, no quotes, no extra text:
TERM: the trend term here | TOPIC_TYPE: ONE_OF_THE_TOKENS_ABOVE

Example correct output:
TERM: Mandalorian and Grogu | TOPIC_TYPE: FICTIONAL_CHARACTER
TERM: Punch the Monkey | TOPIC_TYPE: ABSTRACT_MEME

Do NOT add subject descriptions. Do NOT put terms in quotes. Do NOT use markdown.
"""

    try:
        response = client.models.generate_content(
            model="gemini-2.0-flash",
            config=types.GenerateContentConfig(system_instruction=system_instruction),
            contents=f"TRENDING SEARCHES:\n{trend_lines}\n\nSelect up to 5 wearable motifs (or fewer if fewer pass the test).",
        )

        text = response.text or ""
        print(f"  [Stage1 DEBUG] Response:\n{text[:500]}")

        results = []
        seen = set()

        # Primary regex: strict expected format
        # TERM: <text> | TOPIC_TYPE: <TOKEN>
        for match in re.finditer(
            r"TERM:\s*['\"]?(.+?)['\"]?\s*\|\s*TOPIC_TYPE:\s*['\"]?([A-Z_]+)['\"]?\s*(?:\n|$)",
            text,
        ):
            term = match.group(1).strip().strip('"\'')
            topic_type = match.group(2).strip().upper()
            term = re.sub(r'[\\/*?:"<>|]', "", term).strip()
            if not term or term.lower() in seen:
                continue
            if topic_type not in TOPIC_TYPES:
                topic_type = "GENERAL"
            seen.add(term.lower())
            results.append({"term": term, "topic_type": topic_type})

        # Fallback: if Gemini put the topic type before a colon
        # like '"term" | VIRAL_PHRASE: "subject"', extract just the topic type
        if not results:
            print("  [Stage1] Primary regex matched 0 — trying fallback parser")
            for match in re.finditer(
                r'["\']?([^"\'\n|]{3,60})["\']?\s*\|\s*([A-Z_]+)(?:\s*:|\s*\|)',
                text,
            ):
                term = match.group(1).strip().strip('"\'-')
                topic_type = match.group(2).strip().upper()
                term = re.sub(r'[\\/*?:"<>|]', "", term).strip()
                # Skip if the captured "term" is actually a section header
                if not term or term.lower() in seen or term.upper() in TOPIC_TYPES:
                    continue
                if topic_type not in TOPIC_TYPES:
                    continue
                seen.add(term.lower())
                results.append({"term": term, "topic_type": topic_type})

        print(f"  [Stage1] {len(results)} motifs selected")
        for r in results[:5]:
            print(f"    - {r['term']} [{r['topic_type']}]")
        return results[:5]

    except Exception as e:
        print(f"  [Stage1] Error: {e}")
        return []


# ---------------------------------------------------------------------------
# Stage 1b — Theme-locked curation
# ---------------------------------------------------------------------------
def distill_theme_terms(client, theme: str) -> list[dict]:
    """
    Generates 5 unique motifs for a user-specified theme.
    First motif is always the exact theme; remaining 4 are sub-elements.
    """
    system_instruction = f"""
You are generating shirt design motifs for the theme: "{theme}"

CRITICAL: Each motif MUST have a UNIQUE TERM. Do not repeat "{theme}" as
the TERM for multiple motifs. Motif #1 uses the exact theme. Motifs #2-5
must each use a DIFFERENT specific term — a character name, scene, quote,
or iconic object within "{theme}".

EXAMPLE for "Jujutsu Kaisen":
TERM: Jujutsu Kaisen | TOPIC_TYPE: GENERAL
TERM: Gojo Satoru | TOPIC_TYPE: FICTIONAL_CHARACTER
TERM: Sukuna | TOPIC_TYPE: FICTIONAL_CHARACTER
TERM: Domain Expansion | TOPIC_TYPE: GENERAL
TERM: Yuji Itadori | TOPIC_TYPE: FICTIONAL_CHARACTER

TOPIC_TYPE:
- FICTIONAL_CHARACTER: specific named character
- VIRAL_PHRASE: a famous quote/phrase
- REAL_PERSON: real figure
- SPORTING_EVENT: sports moment
- ABSTRACT_MEME: meme with clear visual
- GENERAL: iconic visual of the theme itself

OUTPUT — exactly 5 lines, each with a DIFFERENT TERM:
TERM: [unique term] | TOPIC_TYPE: [type]
"""
    try:
        response = client.models.generate_content(
            model="gemini-2.0-flash",
            config=types.GenerateContentConfig(system_instruction=system_instruction),
            contents=(
                f'Generate 5 motifs for "{theme}" with 5 DIFFERENT terms. '
                f'Motif #1 uses "{theme}" as the term, motifs #2-5 use different specific terms.'
            ),
        )

        print(f"  [Theme DEBUG] Response:\n{response.text[:400]}")

        raw = re.findall(
            r"TERM:\s*(.*?)\s*\|\s*(?:TOPIC_TYPE:\s*)?(\w+)\s*$",
            response.text,
            re.MULTILINE,
        )

        results = []
        seen = set()
        for r in raw:
            term = re.sub(r'[\\/*?:"<>|\'"]', "", r[0].strip()).strip()
            if not term or term.lower() in seen:
                continue
            seen.add(term.lower())
            topic_type = r[1].strip().upper()
            if topic_type not in TOPIC_TYPES:
                topic_type = "GENERAL"
            results.append({"term": term, "topic_type": topic_type})

        if not results or results[0]["term"].lower() != theme.lower():
            results.insert(0, {"term": theme, "topic_type": "GENERAL"})

        print(f"  [Theme] {len(results[:5])} unique motifs for '{theme}'")
        for r in results[:5]:
            print(f"    - {r['term']} [{r['topic_type']}]")
        return results[:5]

    except Exception as e:
        print(f"  [Theme] Error: {e}")
        return [{"term": theme, "topic_type": "GENERAL"}]