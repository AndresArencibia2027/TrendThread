"""
bq_client.py
============
Fetches rising trend velocity from the Google Trends BigQuery public dataset.

Key improvement over v1:
- Replaces MAX(score) ranking with a true velocity metric:
  recent 2-day average vs. prior 5-day baseline.
  This surfaces terms that are *accelerating* rather than just *big*.
- Returns 'velocity' (float, proportion change) alongside 'term' and 'momentum'
  (raw recent_avg) for backward compatibility with downstream consumers.
- Falls back gracefully to momentum-only if baseline data is unavailable
  (e.g. for brand-new terms with no prior 5-day history).
"""

import os
from google.cloud import bigquery


def get_rising_trends(limit: int = 20) -> list[dict]:
    """
    Connects to BigQuery and fetches top rising terms ranked by velocity.

    Velocity = (recent_avg - baseline_avg) / baseline_avg
    where:
        recent_avg   = average score over the last 2 days
        baseline_avg = average score over the 5 days before that window

    Terms with no baseline (brand new this week) are included but ranked
    below terms with a measurable acceleration signal.

    Returns:
        List of dicts: [{"term": str, "momentum": float, "velocity": float}]
        Empty list on any error.
    """
    if not os.getenv("GOOGLE_APPLICATION_CREDENTIALS"):
        raise EnvironmentError(
            "GOOGLE_APPLICATION_CREDENTIALS not set. "
            "Ensure your .env file points to your service account JSON."
        )

    try:
        client = bigquery.Client()

        # ------------------------------------------------------------------
        # Velocity query: 2-day recent window vs. 5-day prior baseline.
        #
        # LEFT JOIN means terms with no baseline history are still returned
        # with velocity = NULL, so we can sort them below genuine spikes.
        #
        # SAFE_DIVIDE avoids division-by-zero for baseline_avg = 0.
        # COALESCE(-1) puts brand-new terms (no baseline) at the bottom
        # of the velocity ranking but still surfaces them.
        # ------------------------------------------------------------------
        query = f"""
            WITH recent AS (
                SELECT
                    term,
                    AVG(score) AS recent_avg
                FROM `bigquery-public-data.google_trends.top_rising_terms`
                WHERE refresh_date >= DATE_SUB(CURRENT_DATE(), INTERVAL 2 DAY)
                GROUP BY term
            ),
            baseline AS (
                SELECT
                    term,
                    AVG(score) AS baseline_avg
                FROM `bigquery-public-data.google_trends.top_rising_terms`
                WHERE
                    refresh_date >= DATE_SUB(CURRENT_DATE(), INTERVAL 7 DAY)
                    AND refresh_date < DATE_SUB(CURRENT_DATE(), INTERVAL 2 DAY)
                GROUP BY term
            )
            SELECT
                r.term,
                r.recent_avg,
                b.baseline_avg,
                COALESCE(
                    SAFE_DIVIDE(r.recent_avg - b.baseline_avg, b.baseline_avg),
                    -1
                ) AS velocity
            FROM recent r
            LEFT JOIN baseline b USING (term)
            ORDER BY velocity DESC
            LIMIT {limit}
        """

        query_job = client.query(query)
        results = query_job.result()

        trends = []
        for row in results:
            velocity = float(row.velocity) if row.velocity is not None else -1.0
            trends.append({
                "term": row.term,
                "momentum": float(row.recent_avg),   # kept for backward compat
                "velocity": velocity,
            })

        return trends

    except Exception as e:
        print(f"  BigQuery Error: {e}")
        return []