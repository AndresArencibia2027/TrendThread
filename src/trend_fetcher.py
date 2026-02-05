import os
from pathlib import Path
from google.cloud import bigquery

# This finds the directory where THIS script is located
# Then goes up one level and into the 'credentials' folder
BASE_DIR = Path(__file__).resolve().parent.parent
KEY_PATH = BASE_DIR / "credentials" / "google-key.json"

# Set the environment variable using the calculated path
os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = str(KEY_PATH)

def get_rising_trends():
    # Initialize the BigQuery Client
    client = bigquery.Client()

    # Write a simple SQL query (Standard SQL)
    # This pulls from the 'top_rising_terms' public table
    # Update your query string to this:
    query = """
        SELECT term, MAX(score) as max_score
        FROM `bigquery-public-data.google_trends.top_rising_terms`
        WHERE refresh_date = (SELECT MAX(refresh_date) FROM `bigquery-public-data.google_trends.top_rising_terms`)
        GROUP BY term
        ORDER BY max_score DESC
        LIMIT 20
    """

    # Run the query and print results
    print("Fetching the latest rising trends...")
    query_job = client.query(query) 
    results = query_job.result()

    for row in results:
        print(f"Trend: {row.term} | Momentum: {row.max_score}")

if __name__ == "__main__":
    get_rising_trends()