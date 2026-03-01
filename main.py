import os
import sys
from dotenv import load_dotenv

# Path fixing for local imports
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from src.fetchers.bq_client import get_rising_trends
from src.fetchers.gdelt_client import fetch_gdelt_articles
from src.fetchers.x_scraper import run_full_x_scraper
from src.fetchers.image_fetcher import fetch_and_save_visuals
from src.processors.gemini_analyzer import (
    get_client, 
    distill_search_terms,
    analyze_visual_strategy
)
from src.utils.asset_engine import process_final_assets

load_dotenv()

def main():
    print("\n" + "="*60)
    print(" --- STARTING SUBJECT-CENTRIC TREND PIPELINE ---")
    print("="*60)
    client = get_client()
    
    try:
        # [1/5] RAW DATA GATHERING
        print("\n[1/5]  Gathering raw data from BQ, GDELT, and X...")
        bq_raw = get_rising_trends(limit=20)
        gdelt_raw = fetch_gdelt_articles(query='(viral OR trending OR popular)', maxrecords=15)
        x_raw_path = run_full_x_scraper(max_tweets=20)

        # --- NICE PRINTING FOR RAW DATA ---
        if bq_raw:
            print(f"\n{' BQ RISING TERMS':<35} | {'MOMENTUM'}")
            print("-" * 50)
            for trend in bq_raw:
                print(f"{trend.get('term', 'Unknown')[:35]:<35} | {trend.get('momentum', 'N/A')}")

        if gdelt_raw:
            print(f"\n{' GDELT HEADLINES':<50} | {'SOURCE'}")
            print("-" * 75)
            for art in gdelt_raw:
                title = art.get('title', 'No Title')
                display_title = (title[:48] + '..') if len(title) > 48 else title
                print(f"{display_title:<50} | {art.get('source', 'Unknown')}")
        
        # [2/5] GEMINI CURATION (Distilling Subjects & Narratives)
        print("\n" + "-"*60)
        print("[2/5]  Distilling curated trends, subjects, and narratives...")
        # Now returns list of dicts: [{'term': '...', 'subject': '...', 'context': '...'}]
        trend_data = distill_search_terms(client, bq_raw, gdelt_raw, x_raw_path)
        
        if not trend_data:
            print("  No marketable trends distilled. Exiting.")
            return

        # Display the Subject and Narrative so you understand the "Visual Motif"
        for item in trend_data:
            print(f" TREND: {item['term']}")
            print(f" SUBJECT: {item['subject']}") # Essential for character isolation
            print(f" NARRATIVE: {item['context']}")
            print("-" * 20)

        # [3/5] VISUAL DISCOVERY
        print("\n[3/5]  Executing Visual Discovery (Consensus Pass)...")
        trend_visuals_map = {}
        for item in trend_data:
            t = item['term'].strip()
            # Fetching 3 images allows Gemini to verify the specific subject's appearance
            local_images = fetch_and_save_visuals(t, num_results=5) 
            if local_images:
                trend_visuals_map[t] = local_images

        # [4/5] MULTI-MODAL ANALYSIS (Passing Subject + Narrative + Visuals)
        print("\n[4/5]  Launching Subject-Focused Visual Analysis...")
        # Now passing the full trend_data (Terms, Subjects, and Narratives)
        visual_report = analyze_visual_strategy(client, trend_visuals_map, trend_data)
        
        print("-" * 50)
        print(f" CREATIVE REPORT:\n{visual_report}")
        print("-" * 50)

        # [5/5] MANUFACTURING
        print("\n[5/5]  Manufacturing Final Assets...")
        process_final_assets(
            visual_report=visual_report,
            project_id=os.getenv("VERTEX_PROJECT_ID"),
            location=os.getenv("VERTEX_LOCATION", "us-central1")
        )

    except Exception as e:
        print(f"\n  Critical Pipeline Failure: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)

    print("\n Pipeline complete. Final assets with removed backgrounds are in 'output/final_assets/'.")

if __name__ == "__main__":
    main()