# Meme T‑Shirt Listing Uploader (Etsy only)

This mini tool creates a **draft T‑shirt listing on Etsy** from any image file, using the same settings as an existing template listing in your shop.

## Files

- `upload_tshirt.py` – entrypoint; takes an image path + title and calls the Etsy uploader.
- `etsy_upload_tshirt.py` – Etsy logic: OAuth, shop/ID discovery, create listing, upload image, copy attributes from a template listing.
- `requirements.txt` – main project dependencies (already include `requests`, `python-dotenv`, `Pillow`).
- `.env.etsy.example` – example Etsy config; copy to `.env` or merge into your existing `.env` and fill in.

## Setup

```bash
git clone https://github.com/AndresArencibia2027/TrendThread.git
cd TrendThread

auth python3 -m venv venv
source venv/bin/activate

pip install -r requirements.txt
cp .env.etsy.example .env   # or merge into your own .env
```

Edit `.env` and set your Etsy values:

```env
ETSY_API_KEY=5ukwdjiqpz1ls92wtzxfuchp
ETSY_CLIENT_SECRET=wg3zw51c7t
ETSY_SHOP_NAME=TrendThread338
ETSY_SHOP_ID=64371133
ETSY_SHIPPING_PROFILE_ID=300984612030
ETSY_READINESS_STATE_ID=1472493433693
ETSY_REDIRECT_URI=https://oauth.pstmn.io/v1/callback
```

> `ETSY_TEMPLATE_LISTING_ID` is hardcoded in `etsy_upload_tshirt.py` (default `4469757441`) and controls the taxonomy/tags/materials copied to new listings.

## Usage

With the venv active:

```bash
cd TrendThread
source venv/bin/activate

python upload_tshirt.py /absolute/path/to/image.jpg "Listing Title"
```

On first run:

1. The script prints an Etsy OAuth URL.
2. Open it, log in to the Etsy seller account for `TrendThread338`, and approve access.
3. Paste the full redirect URL from your browser back into the terminal.

Each run will then:

- Resolve your shop and IDs from `.env`.
- Copy attributes from the template listing.
- Create a **draft T‑shirt listing** priced at **$19.99**.
- Upload the given image as the main photo.

Publish and tweak the draft from Etsy Shop Manager as needed.
