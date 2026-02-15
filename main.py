import os
import sys
from dotenv import load_dotenv

from src.fetchers.bq_client import get_rising_trends
from src.fetchers.gdelt_client import fetch_gdelt_articles
from src.fetchers.x_scraper import run_full_x_scraper
from src.processors.gemini_analyzer import get_client, analyze_trends_and_generate_prompts
from src.processors.image_generator import generate_five_images

def main():
    load_dotenv()
    print("\n --- STARTING UNIFIED TREND PIPELINE ---")
    
    try:
        # Gather Context
        print("\n[1/3]  Fetching BigQuery Trends...")
        bq_trends = get_rising_trends(limit=20)
        if bq_trends:
            print(f"\n{'TERM':<25} | {'MOMENTUM':<10}")
            print("-" * 40)
            for trend in bq_trends:
                print(f"{trend['term']:<25} | {trend['momentum']:<10}")
        else:
            print(" No BigQuery trends found.")

        print("\n[2/3]  Fetching GDELT News Articles...")
        articles = fetch_gdelt_articles(query='(breaking OR viral OR popular OR meme OR tiktok)', maxrecords=20)
        if articles:
            print(f"\n{'SOURCE':<20} | {'HEADLINE'}")
            print("-" * 60)
            for art in articles:
                # Truncate headline if too long for the console
                headline = art.get('title', 'No Title')
                display_headline = (headline[:75] + '..') if len(headline) > 75 else headline
                print(f"{art.get('source', 'Unknown'):<20} | {display_headline}")
        else:
            print(" No GDELT articles found.")

        print("\n[3/3]  Launching X.com Scraper...")
        x_file_path = run_full_x_scraper(max_tweets=20)

        # Analyze & Synthesize
        client = get_client()
        summary, prompts = analyze_trends_and_generate_prompts(client, bq_trends, articles, x_file_path)
        print(f"\n Trend Report:\n{summary}\n")

        # Create Asset
        project_id = os.getenv("VERTEX_PROJECT_ID")
        if project_id and prompts:
            generate_five_images(
                project_id=project_id,
                location=os.getenv("VERTEX_LOCATION", "us-central1"),
                prompts=prompts
            )

    except Exception as e:
        print(f"\n Critical Failure: {e}")
        sys.exit(1)

    print("\n Pipeline complete.")

if __name__ == "__main__":
    main()