# TrendThread Store Manager 

TrendThread Store Manager is a private seller tool designed to autonomously identify, curate, and manufacture high-marketability assets for a trend-focused Etsy shop. By synthesizing search intent from BigQuery with cultural context from KnowYourMeme, the application generates professional-grade visual motifs ready for print-on-demand products.

This project is intended for internal use only and is not distributed as a public service.

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