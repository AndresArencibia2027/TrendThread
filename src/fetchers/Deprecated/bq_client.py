"""
bq_client.py
============
Fetches rising trend velocity from the Google Trends BigQuery public dataset.

Returns a velocity-ranked, blocklist-filtered list of terms ready for the
marketability filter in gemini_analyzer. Designed to be the sole trend source
for now, with a clean interface for adding new sources later.
"""

import os
from google.cloud import bigquery


# ---------------------------------------------------------------------------
# Terms that are high-volume but structurally never wearable.
# Extend this list as you observe bad outputs.
# ---------------------------------------------------------------------------
_BLOCKLIST = {
    # Weather / natural events
    "eclipse", "blood moon", "hurricane", "earthquake", "tornado", "wildfire",
    # Finance / economy
    "mortgage", "tax", "interest rate", "inflation", "stock market", "crypto",
    "bitcoin", "earnings", "gdp", "unemployment", "recession",
    # Health / medical
    "flu", "covid", "vaccine", "symptom", "hospital", "surgery",
    # Generic breaking news
    "shooting", "accident", "crash", "obituary", "missing", "arrest",
    # Utility tech
    "update", "download", "install", "error", "fix", "how to",
    "tutorial", "price", "sale", "coupon", "promo code",
    # Sports scores (event data, not identity)
    "score", "standings", "schedule", "trade deadline",
}


def _is_blocked(term: str) -> bool:
    t = term.lower()
    return any(b in t for b in _BLOCKLIST)


def get_rising_trends(limit: int = 40) -> list[dict]:
    """
    Fetches Google Trends rising terms ranked by velocity.

    Velocity = (avg score last 2 days) vs (avg score prior 5 days).
    Terms with no prior baseline are ranked last (velocity = -1).

    Returns:
        List of dicts sorted by velocity desc:
        [{"term": str, "momentum": float, "velocity": float}]
        Returns [] on any error — callers should handle gracefully.
    """
    if not os.getenv("GOOGLE_APPLICATION_CREDENTIALS"):
        raise EnvironmentError(
            "GOOGLE_APPLICATION_CREDENTIALS not set. "
            "Ensure your .env file points to your service account JSON."
        )

    try:
        client = bigquery.Client()

        query = f"""
            WITH recent AS (
                SELECT term, AVG(score) AS recent_avg
                FROM `bigquery-public-data.google_trends.top_rising_terms`
                WHERE refresh_date >= DATE_SUB(CURRENT_DATE(), INTERVAL 2 DAY)
                GROUP BY term
            ),
            baseline AS (
                SELECT term, AVG(score) AS baseline_avg
                FROM `bigquery-public-data.google_trends.top_rising_terms`
                WHERE refresh_date >= DATE_SUB(CURRENT_DATE(), INTERVAL 7 DAY)
                  AND refresh_date <  DATE_SUB(CURRENT_DATE(), INTERVAL 2 DAY)
                GROUP BY term
            )
            SELECT
                r.term,
                r.recent_avg                                              AS momentum,
                COALESCE(
                    SAFE_DIVIDE(r.recent_avg - b.baseline_avg, b.baseline_avg),
                    -1
                )                                                         AS velocity
            FROM recent r
            LEFT JOIN baseline b USING (term)
            ORDER BY velocity DESC
            LIMIT {limit * 3}
        """

        rows = list(client.query(query).result())
        seen: set[str] = set()
        trends: list[dict] = []

        for row in rows:
            term = (row.term or "").strip()
            if not term or term in seen or _is_blocked(term):
                continue
            seen.add(term)
            trends.append({
                "term": term,
                "momentum": float(row.momentum),
                "velocity": float(row.velocity) if row.velocity is not None else -1.0,
            })

        trends.sort(key=lambda x: x["velocity"], reverse=True)
        return trends[:limit]

    except Exception as e:
        print(f"  [BQ] Error: {e}")
        return []