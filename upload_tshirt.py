#!/usr/bin/env python3
"""
One entrypoint: upload an image as a T-shirt listing.

- Loads .env for credentials.
- Tries Etsy first (your Etsy shop). If Etsy creds are set and work, listing is created there.
- If Etsy is skipped or fails, tries Printify (API store). Use an API store for Printify to work.

Usage:
  python upload_tshirt.py <image.jpg> [title]

Env (in .env):
  Etsy (primary for your Etsy shop):
    ETSY_API_KEY, ETSY_CLIENT_SECRET, ETSY_SHOP_NAME
    Optional: ETSY_SHOP_ID, ETSY_SHIPPING_PROFILE_ID, ETSY_READINESS_STATE_ID
  Printify (fallback; needs an "API" store in Printify):
    PRINTIFY_API_TOKEN

  Set USE_PRINTIFY_ONLY=1 to skip Etsy and use only Printify.
"""

import os
import sys
from pathlib import Path

# Load .env once for all sub-scripts
try:
    from dotenv import load_dotenv
    _root = Path(__file__).resolve().parent
    load_dotenv(_root / ".env")
    load_dotenv(_root / ".env.local")
except ImportError:
    pass


def main():
    if len(sys.argv) < 2:
        print("Usage: python upload_tshirt.py <image.jpg> [title]")
        sys.exit(1)
    image_path = Path(sys.argv[1])
    if not image_path.is_file():
        print(f"Not a file: {image_path}")
        sys.exit(1)
    title = sys.argv[2] if len(sys.argv) > 2 else None

    use_printify_only = os.environ.get("USE_PRINTIFY_ONLY", "").strip().lower() in ("1", "true", "yes")
    etsy_ready = all(
        os.environ.get(k)
        for k in ("ETSY_API_KEY", "ETSY_CLIENT_SECRET", "ETSY_SHOP_NAME")
    )

    # 1) Etsy first (unless USE_PRINTIFY_ONLY)
    if not use_printify_only and etsy_ready:
        try:
            from etsy_upload_tshirt import (
                load_or_refresh_token,
                get_shop_id,
                create_draft_listing,
            )
            print("Using Etsy.")
            token = load_or_refresh_token()
            shop_id = get_shop_id(token)
            print("Shop ID:", shop_id)
            create_draft_listing(token, shop_id, title, image_path)
            print("Done. Check your Etsy shop for the draft.")
            return
        except SystemExit as e:
            if e.code != 0:
                print("Etsy failed:", e)
                print("Falling back to Printify...")
        except Exception as e:
            print("Etsy error:", e)
            print("Falling back to Printify...")

    # 2) Printify (fallback or only)
    if os.environ.get("PRINTIFY_API_TOKEN"):
        try:
            from printify_upload_tshirt import (
                get_shops,
                pick_shop,
                upload_image,
                create_tshirt_product,
            )
            print("Using Printify.")
            shops = get_shops()
            shop_id = pick_shop(shops)
            image_id = upload_image(image_path)
            create_tshirt_product(shop_id, image_id, title or image_path.stem)
            print("Done. Check your Printify API store for the draft.")
            return
        except SystemExit as e:
            if e.code != 0:
                print("Printify failed:", e)
        except Exception as e:
            print("Printify error:", e)

    print(
        "No option succeeded. For Etsy: set ETSY_API_KEY, ETSY_CLIENT_SECRET, ETSY_SHOP_NAME in .env "
        "(and optionally ETSY_SHOP_ID, ETSY_SHIPPING_PROFILE_ID, ETSY_READINESS_STATE_ID). "
        "For Printify: set PRINTIFY_API_TOKEN and use an API store."
    )
    sys.exit(1)


if __name__ == "__main__":
    main()
