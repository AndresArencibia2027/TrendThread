import os
import sys
from pathlib import Path
from dotenv import load_dotenv

# Path fixing for local imports
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from src.fetchers.Deprecated.bq_client import get_rising_trends
from src.fetchers.Deprecated.gdelt_client import fetch_gdelt_articles
from src.fetchers.Deprecated.x_scraper import run_full_x_scraper
from src.fetchers.image_fetcher import fetch_and_save_visuals
from src.processors.download_confirmed_memes import main as run_kym_scraper
from src.processors.gemini_analyzer import (
    get_client, 
    distill_search_terms,
    analyze_visual_strategy
)
from src.utils.asset_engine import process_final_assets

from src.trends import config as trend_config

load_dotenv()

# Centralized reference for the KYM data
KYM_DIR = Path("output/references/downloaded_confirmed_memes")

def get_local_kym_context():
    """Reads the local output folder to provide Gemini with 'Confirmed Meme' context."""
    kym_entries = []
    if KYM_DIR.exists():
        # Scans the folder for images; files are named '01_meme_title.jpg'
        for file in KYM_DIR.glob("*"):
            if file.suffix.lower() in [".jpg", ".png", ".webp"]:
                # Extracting the title from the filename logic we built
                clean_title = file.stem.split("_", 1)[-1].replace("_", " ")
                kym_entries.append({"title": clean_title, "path": str(file)})
    return kym_entries

def run_gemini_trend_pipeline():
    """New default pipeline: Gemini/Vertex AI trend engine (no scraping)."""
    from src.trends import service

    print("\n" + "="*60)
    print(" --- STARTING GEMINI TREND ENGINE (manual scan) ---")
    print("="*60)
    result = service.run_and_store(scan_type="manual")

    print(f"\n Run #{result['run_id']} | model: {result['model']} | "
          f"saved {result['saved_events']} events")

    opps = result.get("merch_opportunities", [])
    if opps:
        print(f"\n{' MERCH OPPORTUNITY':<40} | {'RISK':<10} | {'ACTION'}")
        print("-" * 75)
        for o in opps:
            name = (o.get("trend_event_name") or "")[:38]
            print(f"{name:<40} | {str(o.get('legal_risk', '')):<10} | {o.get('recommended_action', '')}")

    plan = result.get("immediate_action_plan", [])
    if plan:
        print("\n IMMEDIATE ACTION PLAN:")
        for i, step in enumerate(plan, 1):
            print(f"  {i}. {step}")

    print("\n Trend scan complete. Open the dashboard (python app.py) to review,")
    print(" then use upload_tshirt.py / the image generator to produce assets.")


def main():
    # Route to the new Gemini engine unless the operator has explicitly opted
    # back into the legacy scraping pipeline via feature flags.
    if trend_config.is_gemini_engine() and not trend_config.legacy_scraping_enabled():
        run_gemini_trend_pipeline()
        return

    print("\n" + "="*60)
    print(" --- STARTING LEGACY SUBJECT-CENTRIC SCRAPING PIPELINE ---")
    print(" (TREND_ENGINE_PROVIDER!=gemini or ENABLE_LEGACY_SCRAPING=true)")
    print("="*60)
    return _run_legacy_scraping_pipeline()


def _run_legacy_scraping_pipeline():
    """DEPRECATED multi-source scraping pipeline.

    Kept for reference / fallback. Disabled by default behind the feature flags
    TREND_ENGINE_PROVIDER=gemini and ENABLE_LEGACY_SCRAPING=false. It depends on
    X/Twitter scraping, GDELT, BigQuery and KnowYourMeme.
    """
    client = get_client()
    
    try:
        # [1/5] RAW DATA GATHERING
        print("\n[1/5]  Gathering raw data from BQ, GDELT, and X...")
        bq_raw = get_rising_trends(limit=40)
        gdelt_raw = fetch_gdelt_articles(query='(viral OR trending OR popular)', maxrecords=15)
        x_raw_path = run_full_x_scraper(max_tweets=20)

        # Pull signals from KnowYourMeme (downloads files to KYM_DIR)
        run_kym_scraper(start_page=1, end_page=1)
        kym_context = get_local_kym_context()

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
        trend_data = distill_search_terms(client, bq_raw, gdelt_raw, x_raw_path, kym_context = kym_context)
        
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

    print("\n Pipeline complete. Final assets are in 'output/final_assets/'.")

if __name__ == "__main__":
    main()