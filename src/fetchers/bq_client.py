import os
from google.cloud import bigquery

def get_rising_trends(limit=20):
    """
    Connects to BigQuery and fetches the latest top rising terms 
    from the Google Trends public dataset.
    
    Returns:
        list: A list of dictionaries containing 'term' and 'momentum'.
    """
    
    # Ensure credentials path is set before initializing the client
    # This assumes the env var is set in main.py or via your .env file
    if not os.getenv("GOOGLE_APPLICATION_CREDENTIALS"):
        raise EnvironmentError(
            "GOOGLE_APPLICATION_CREDENTIALS not set. "
            "Ensure your .env file points to your service account JSON."
        )

    try:
        client = bigquery.Client()

        # Query pulls the most recent trends based on the latest refresh_date
        query = f"""
            SELECT term, MAX(score) as max_score
            FROM `bigquery-public-data.google_trends.top_rising_terms`
            WHERE refresh_date = (
                SELECT MAX(refresh_date) 
                FROM `bigquery-public-data.google_trends.top_rising_terms`
            )
            GROUP BY term
            ORDER BY max_score DESC
            LIMIT {limit}
        """

        query_job = client.query(query)
        results = query_job.result()

        # Standardizing output to list of dicts for pipeline compatibility
        return [
            {"term": row.term, "momentum": row.max_score} 
            for row in results
        ]

    except Exception as e:
        print(f"❌ BigQuery Error: {e}")
        return []