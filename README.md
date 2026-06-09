# TrendThread Store Manager 

TrendThread Store Manager is a private seller tool designed to autonomously identify, curate, and manufacture high-marketability assets for a trend-focused Etsy shop. The default trend engine is now **Gemini / Vertex AI**, which produces ranked, IP-safe merch opportunities and a production calendar — replacing the older multi-source scraping pipeline.

This project is intended for internal use only and is not distributed as a public service.

---

## Gemini Trend Engine (default)

The trend engine is server-side only and feature-flagged:

```
TREND_ENGINE_PROVIDER=gemini      # selects the Gemini engine (default)
ENABLE_LEGACY_SCRAPING=false      # keeps the old scrapers OFF
```

It reuses the existing Vertex AI auth (same credentials as image generation) and
returns structured JSON: top merch opportunities, upcoming predictable events,
viral trends to monitor, evergreen ideas, high-risk trends to avoid, and an
immediate action plan. Each opportunity includes a safe (IP-clean) design angle,
unsafe terms to avoid, design concepts, Etsy SEO keywords, legal-risk and
saturation ratings, a trend score, and a recommended action. High / Very High
legal-risk items are flagged for manual review and never auto-approved.

### Setup

1. Install dependencies: `pip install -r requirements.txt`
2. Copy env: `cp .env.example .env` and fill in the Google Cloud values
   (`GOOGLE_CLOUD_PROJECT`, `GOOGLE_CLOUD_LOCATION`, `GOOGLE_APPLICATION_CREDENTIALS`,
   `GEMINI_TREND_MODEL`, `CRON_SECRET`). The legacy `VERTEX_PROJECT_ID` /
   `VERTEX_LOCATION` names are still honored as a fallback.
3. Initialize the database (auto-runs, or do it manually):
   `python -m src.trends.database`

### Dashboard + manual scan

```bash
python app.py            # http://localhost:5000
```

Click **Run Trend Scan** (manual / daily / weekly) to call the backend and view
ranked opportunities and the saved production calendar. You can also run a scan
from the CLI:

```bash
python main.py                       # manual scan (Gemini engine)
python scripts/run_scan.py manual --context "summer pet vibes"
```

### API routes

| Route | Method | Auth | Purpose |
| --- | --- | --- | --- |
| `/api/trends/scan` | POST | none | manual scan (dashboard button) |
| `/api/trends/daily` | POST | `CRON_SECRET` | current / viral trends |
| `/api/trends/weekly` | POST | `CRON_SECRET` | event calendar + planning |
| `/api/trends/events` | GET | none | load saved events |

Cron routes require `Authorization: Bearer <CRON_SECRET>` (or `X-Cron-Secret`).

### Scheduling (system cron)

This is a Python/Flask app (not Vercel), so daily/weekly scans run via system
cron using `scripts/run_scan.py` (calls the service directly, no HTTP):

```cron
0 8 * * *  cd /path/to/TrendThread && /path/to/venv/bin/python scripts/run_scan.py daily  >> data/cron.log 2>&1
0 7 * * 1  cd /path/to/TrendThread && /path/to/venv/bin/python scripts/run_scan.py weekly >> data/cron.log 2>&1
```

Or trigger the HTTP routes with curl: `curl -X POST -H "Authorization: Bearer $CRON_SECRET" http://host/api/trends/daily`.

### Legacy scraping pipeline

The old X/Twitter, GDELT, BigQuery, and KnowYourMeme scrapers in `src/fetchers/`
are **not deleted** — they are disabled behind the feature flags above. To run
the legacy pipeline, set `ENABLE_LEGACY_SCRAPING=true` (and/or
`TREND_ENGINE_PROVIDER` to something other than `gemini`) and run `python main.py`.

---

## Features

- **Multi-Source Trend Extraction:** Aggregates data from Google BigQuery (Search Intent), GDELT (News), X (Social Discourse), and KnowYourMeme (Confirmed Cultural DNA).
- **Strategic AI Curation:** Uses Gemini 2.0 Flash to filter raw data through, prioritizing high-momentum search terms over unmarketable internet noise.
- **Multimodal Creative Direction:** Analyzes visual clusters to determine the optimal manufacturing path: raw isolation, narrative-driven memes, or full AI regeneration.
- **Visual DNA Preservation:** Ensures AI-regenerated motifs maintain specific cultural anchors (franchise names, signature symbols) so products remain recognizable to fans.
- **Autonomous Asset Manufacturing:**
    - **Background Removal:** Uses Vertex AI Image Segmentation for surgical subject isolation.
    - **Motif Generation:** Leverages Imagen 3.0 to create crisp, flat-vector stickers from low-quality source references.
    - **Meme Processing:** Applies non-pixelated, high-contrast text overlays using alpha-channel composition.

---

## Use Case

This tool is used by the shop owner to:

- **Identify Emerging Motifs:** Bypass short-lived "trash" trends to find subjects with high identity-signaling potential.
- **Automate Asset Creation:** Convert grainy social media screengrabs into professional, "trustworthy" product graphics.
- **Scale Product Research:** Use independent signal weighting to verify if a meme has enough search volume to justify a listing.
- **Maintain Quality Standards:** Ensure all shop assets follow a consistent, high-end "die-cut" aesthetic.

---

## Technology Stack

- **Language:** Python 3.10+
- **AI Models:** Google Gemini 2.0 Flash (Vision/Text), Google Imagen 3.0 (Vertex AI)
- **Data Infrastructure:** Google BigQuery, GDELT Project, SerpApi (Visual Discovery)
- **Image Processing:** Pillow (PIL) for advanced composition and Alpha-channel blending
- **Data Management:** Dotenv for environment-based credential security

---

## Setup

### Prerequisites

- **Google Cloud Project:** Vertex AI and BigQuery APIs enabled.
- **API Credentials:** Vertex Project ID, SerpApi Key, and Etsy API Key.
- **Python 3.10+:** Required for `google-genai` SDK compatibility.

### Installation

1. Clone the repository to your local root.
2. Install dependencies:
   `pip install -r requirements.txt`
3. Configure your `.env` file with project-specific IDs and keys.

### Execution

To run the full trend-to-asset pipeline:
`python main.py`

To update the KnowYourMeme reference database independently:
`python -m src.processors.download_confirmed_memes`

---

## Swipe Review + Printify Publish

This repository now includes a mobile-friendly review app that lets you approve or reject generated designs.

### What it does

- Loads images from `output/final_assets/`
- Lets you "Dislike" (reject), "Like + Publish", or "Regenerate" images.
- On approval, uploads the design to Printify and creates + publishes a t-shirt product
- Because Printify is already connected to Etsy, the published product flows to Etsy

### Run it

1. Install dependencies:
   `pip install -r requirements.txt`
2. Ensure `.env` has valid Printify values:
   - `PRINTIFY_API_TOKEN`
   - `PRINTIFY_SHOP_ID` (optional: auto-selects first shop if empty)
   - `PRINTIFY_BLUEPRINT_ID`
   - `PRINTIFY_PRINT_PROVIDER_ID`
   - `PRINTIFY_VARIANT_IDS`
3. Start app:
   `python -m src.apps.review_app`
4. Open:
   `http://localhost:8080`

### Controls

- `ArrowLeft` -> reject
- `ArrowRight` -> approve and publish