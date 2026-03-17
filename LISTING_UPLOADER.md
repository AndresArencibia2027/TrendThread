# Meme T‑Shirt Listing Uploader (Etsy only)

This mini tool creates a **draft T‑shirt listing on Etsy** from any image file, using the same settings as an existing template listing in your shop.

## Files

- `upload_tshirt.py` – entrypoint; takes an image path + title and calls the Etsy uploader.
- `etsy_upload_tshirt.py` – Etsy logic: OAuth, shop/ID discovery, create listing, upload image, copy attributes from a template listing.
- `requirements.txt` – main project dependencies (already include `requests`, `python-dotenv`, `Pillow`).
- `.env.etsy.example` – example Etsy config; copy to `.env` or merge into your existing `.env`.

## Setup

```bash
git clone https://github.com/AndresArencibia2027/TrendThread.git
cd TrendThread

python3 -m venv venv
source venv/bin/activate

pip install -r requirements.txt
cp .env.etsy.example .env   # or merge into your own .env
```

Edit `.env` and set your Etsy values (API key, secret, shop name, shop/processing IDs).
For security reasons **do not commit actual credentials** – check your private credential doc for the exact values.

> `ETSY_TEMPLATE_LISTING_ID` is hardcoded in `etsy_upload_tshirt.py` and controls the taxonomy/tags/materials copied to new listings.

## Usage

With the venv active:

```bash
cd TrendThread
source venv/bin/activate

python upload_tshirt.py /absolute/path/to/image.jpg "Listing Title"
```

On first run:

1. The script prints an Etsy OAuth URL.
2. Open it, log in to the Etsy seller account for your shop, and approve access.
3. Paste the full redirect URL from your browser back into the terminal.

Each run will then:

- Resolve your shop and IDs from `.env`.
- Copy attributes from the template listing.
- Create a **draft T‑shirt listing** priced at your configured amount.
- Upload the given image as the main photo.

Publish and tweak the draft from Etsy Shop Manager as needed.
