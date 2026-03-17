#!/usr/bin/env python3
"""
Automate creating a draft T-shirt listing on Etsy and uploading one image.

All config from env (use a .env file or export). One-time OAuth: open URL, sign in, paste redirect URL.
Then script finds your shop, creates draft listing, uploads image.

Setup:
  1. Etsy Developer: https://www.etsy.com/developers/your-apps
     - Add Redirect URI: https://oauth.pstmn.io/v1/callback
  2. Set in .env: ETSY_API_KEY, ETSY_CLIENT_SECRET, ETSY_SHOP_NAME
     Optional: ETSY_SHOP_ID, ETSY_SHIPPING_PROFILE_ID, ETSY_READINESS_STATE_ID
  3. Run: python etsy_upload_tshirt.py path/to/image.jpg [title]
"""

import base64
import hashlib
import json
import os
import re
import secrets
import sys
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import requests

# Load .env from project root so ETSY_* (and PRINTIFY_*) come from one file
try:
    from dotenv import load_dotenv
    _root = Path(__file__).resolve().parent
    load_dotenv(_root / ".env")
    load_dotenv(_root / ".env.etsy")
    load_dotenv(_root / ".env.local")
except ImportError:
    pass

# --- Config (env only; no hardcoded secrets) ---
ETSY_API_KEY = os.environ.get("ETSY_API_KEY")
ETSY_CLIENT_SECRET = os.environ.get("ETSY_CLIENT_SECRET")
ETSY_SHOP_NAME = os.environ.get("ETSY_SHOP_NAME")
REDIRECT_URI = os.environ.get("ETSY_REDIRECT_URI", "https://oauth.pstmn.io/v1/callback")

# Use an existing good T-shirt listing as a template for attributes
TEMPLATE_LISTING_ID = int(os.environ.get("ETSY_TEMPLATE_LISTING_ID", "4469757441"))

API_BASE = "https://api.etsy.com/v3"
TOKEN_FILE = Path(__file__).resolve().parent / ".etsy_token.json"
SCOPES = "listings_w listings_r shops_r"  # create/read listings, read shop/shipping


def _x_api_key():
    if not ETSY_API_KEY or not ETSY_CLIENT_SECRET:
        raise SystemExit(
            "Set ETSY_API_KEY and ETSY_CLIENT_SECRET in .env or environment."
        )
    return f"{ETSY_API_KEY}:{ETSY_CLIENT_SECRET}"


def _pkce():
    verifier = base64.urlsafe_b64encode(secrets.token_bytes(32)).rstrip(b"=").decode("ascii")
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode("ascii")
    return verifier, challenge


def _auth_headers(token: str):
    return {
        "x-api-key": _x_api_key(),
        "Authorization": f"Bearer {token}",
    }


def fetch_template_listing(token: str) -> dict:
    """Fetch the template listing we want to mirror attributes from."""
    r = requests.get(
        f"{API_BASE}/application/listings/{TEMPLATE_LISTING_ID}",
        headers=_auth_headers(token),
    )
    if not r.ok:
        print(f"Warning: failed to fetch template listing {TEMPLATE_LISTING_ID}: {r.status_code} {r.text}")
        return {}
    return r.json()


def get_token_via_browser():
    """Run OAuth with PKCE; user opens URL and pastes redirect URL or code."""
    verifier, challenge = _pkce()
    state = secrets.token_urlsafe(16)
    params = {
        "response_type": "code",
        "client_id": ETSY_API_KEY,
        "redirect_uri": REDIRECT_URI,
        "scope": SCOPES.replace(" ", "%20"),
        "state": state,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
    }
    q = "&".join(f"{k}={v}" for k, v in params.items())
    auth_url = f"https://www.etsy.com/oauth/connect?{q}"

    print("1. Add this Redirect URI in your Etsy app if you haven't:", REDIRECT_URI)
    print("2. Open this URL in your browser, sign in, and allow the app:\n")
    print(auth_url)
    print("\n3. After Etsy redirects you, copy the FULL address bar URL (or just the 'code=...' part).")
    raw = input("Paste it here: ").strip()

    code = None
    if "code=" in raw:
        if raw.startswith("http"):
            parsed = urlparse(raw)
            qs = parse_qs(parsed.query)
            code = (qs.get("code") or [None])[0]
        else:
            m = re.search(r"code=([^&\s]+)", raw)
            code = m.group(1) if m else None
    else:
        code = raw

    if not code:
        raise SystemExit("Could not find 'code' in what you pasted. Try pasting the full redirect URL.")

    # Token exchange
    r = requests.post(
        f"{API_BASE}/public/oauth/token",
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        data={
            "grant_type": "authorization_code",
            "client_id": ETSY_API_KEY,
            "redirect_uri": REDIRECT_URI,
            "code": code,
            "code_verifier": verifier,
        },
    )
    if not r.ok:
        raise SystemExit(f"Token exchange failed: {r.status_code} {r.text}")
    data = r.json()
    out = {
        "access_token": data["access_token"],
        "refresh_token": data.get("refresh_token"),
        "expires_in": data.get("expires_in", 3600),
    }
    with open(TOKEN_FILE, "w") as f:
        json.dump(out, f, indent=2)
    print("Token saved to", TOKEN_FILE)
    return out["access_token"]


def load_or_refresh_token():
    """Load token from file; refresh if expired."""
    if not TOKEN_FILE.exists():
        return get_token_via_browser()

    with open(TOKEN_FILE) as f:
        data = json.load(f)
    access = data.get("access_token")
    refresh = data.get("refresh_token")

    # Optional: check expires_at if you store it; here we just try refresh when needed
    if refresh:
        r = requests.post(
            f"{API_BASE}/public/oauth/token",
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            data={
                "grant_type": "refresh_token",
                "client_id": ETSY_API_KEY,
                "refresh_token": refresh,
            },
        )
        if r.ok:
            new = r.json()
            data["access_token"] = new["access_token"]
            data["refresh_token"] = new.get("refresh_token", refresh)
            data["expires_in"] = new.get("expires_in", 3600)
            with open(TOKEN_FILE, "w") as f:
                json.dump(data, f, indent=2)
            return data["access_token"]

    return access or get_token_via_browser()


def get_shop_id(token: str) -> int:
    """Resolve to numeric shop_id. Prefer getMe (returns shop_id for token), then env, then user shops."""
    # 1) Fast path: manual override
    env_shop_id = os.environ.get("ETSY_SHOP_ID")
    if env_shop_id:
        try:
            return int(env_shop_id)
        except ValueError:
            raise SystemExit(f"ETSY_SHOP_ID must be an integer, got: {env_shop_id!r}")

    # 2) getMe returns shop_id for the authenticated user (recommended)
    r = requests.get(
        f"{API_BASE}/application/users/me",
        headers=_auth_headers(token),
    )
    if r.ok:
        data = r.json()
        shop_id = data.get("shop_id")
        if shop_id is None and data.get("results"):
            res = data["results"]
            shop_id = res.get("shop_id") if isinstance(res, dict) else (res[0].get("shop_id") if res else None)
        if shop_id is not None:
            return int(shop_id)

    # 3) Fallback: list shops by user_id from token
    user_prefix = (token or "").split(".", 1)[0]
    if not user_prefix.isdigit():
        raise SystemExit(
            f"Could not parse user_id from token. Set ETSY_SHOP_ID in .env."
        )
    user_id = int(user_prefix)
    r = requests.get(
        f"{API_BASE}/application/users/{user_id}/shops",
        headers=_auth_headers(token),
    )
    if not r.ok:
        raise SystemExit(f"Failed to get shops: {r.status_code} {r.text}")
    shops = r.json().get("results", []) or r.json().get("shops", [])

    if not ETSY_SHOP_NAME:
        raise SystemExit("Set ETSY_SHOP_NAME in .env or environment.")
    name_lower = ETSY_SHOP_NAME.strip().lower()
    for s in shops:
        if (s.get("shop_name") or "").strip().lower() == name_lower:
            return s["shop_id"]
    raise SystemExit(
        f"Shop '{ETSY_SHOP_NAME}' not found. Set ETSY_SHOP_ID in .env (numeric ID from Etsy). Your shops: {[s.get('shop_name') for s in shops]}"
    )


def get_shop_defaults(token: str, shop_id: int):
    """Fetch first shipping profile and first processing profile for the shop (or use env)."""
    out = {
        "shipping_profile_id": os.environ.get("ETSY_SHIPPING_PROFILE_ID"),
        "readiness_state_id": os.environ.get("ETSY_READINESS_STATE_ID"),
    }
    try:
        if out["shipping_profile_id"]:
            out["shipping_profile_id"] = int(out["shipping_profile_id"])
    except (TypeError, ValueError):
        out["shipping_profile_id"] = None
    try:
        if out["readiness_state_id"]:
            out["readiness_state_id"] = int(out["readiness_state_id"])
    except (TypeError, ValueError):
        out["readiness_state_id"] = None

    if out["shipping_profile_id"] and out["readiness_state_id"]:
        return out

    # Try API: shipping profiles
    for path in (f"/application/shops/{shop_id}/shipping-profiles", f"/application/shops/{shop_id}/listings/shipping-profiles"):
        r = requests.get(API_BASE + path, headers=_auth_headers(token))
        if r.ok:
            results = r.json().get("results", [])
            if results and not out["shipping_profile_id"]:
                out["shipping_profile_id"] = results[0].get("shipping_profile_id")
            break

    # Processing profiles (readiness states)
    for path in (f"/application/shops/{shop_id}/processing-profiles", f"/application/shops/{shop_id}/listings/processing-profiles"):
        r = requests.get(API_BASE + path, headers=_auth_headers(token))
        if r.ok:
            results = r.json().get("results", [])
            if results and not out["readiness_state_id"]:
                out["readiness_state_id"] = results[0].get("processing_profile_id")
            break

    # Fallback: get from first existing listing (draft or active)
    if not out["shipping_profile_id"] or not out["readiness_state_id"]:
        r = requests.get(
            f"{API_BASE}/application/shops/{shop_id}/listings",
            headers=_auth_headers(token),
            params={"state": "draft", "limit": 1},
        )
        if not r.ok:
            r = requests.get(
                f"{API_BASE}/application/shops/{shop_id}/listings",
                headers=_auth_headers(token),
                params={"limit": 1},
            )
        if r.ok:
            results = r.json().get("results", [])
            if results:
                L = results[0]
                if not out["shipping_profile_id"]:
                    out["shipping_profile_id"] = L.get("shipping_profile_id")
                if not out["readiness_state_id"]:
                    out["readiness_state_id"] = L.get("processing_profile_id")

    return out


def create_draft_listing(token: str, shop_id: int, title: str, image_path: Path):
    """Create draft listing then upload image. Returns listing_id."""
    defaults = get_shop_defaults(token, shop_id)
    shipping_id = defaults["shipping_profile_id"]
    readiness_id = defaults["readiness_state_id"]

    if not shipping_id or not readiness_id:
        print("Missing shipping_profile_id or readiness_state_id.")
        print("Set ETSY_SHIPPING_PROFILE_ID and ETSY_READINESS_STATE_ID (from Etsy shop dashboard or API).")
        print("Or create one listing manually in Etsy, then inspect its shipping/processing IDs and set them here.")
        raise SystemExit(1)

    # Pull attributes (taxonomy, tags, materials, etc.) from a known-good template listing
    template = fetch_template_listing(token)
    taxonomy_id = template.get("taxonomy_id") or 1152  # fallback to a generic tee taxonomy
    materials = template.get("materials") or []
    tags = template.get("tags") or []

    listing_title = (title or image_path.stem)[:140]
    body = {
        "quantity": 1,
        "title": listing_title,
        "description": f"T-shirt design: {listing_title}",
        "price": 19.99,  # cents (19.99 USD)
        "who_made": "i_did",
        # Valid enum values per Etsy docs; use 2020_2026 for new items
        "when_made": "2020_2026",
        "taxonomy_id": taxonomy_id,
        "shipping_profile_id": shipping_id,
        "readiness_state_id": readiness_id,
        "materials": materials,
        "tags": tags,
    }

    r = requests.post(
        f"{API_BASE}/application/shops/{shop_id}/listings",
        headers={**_auth_headers(token), "Content-Type": "application/x-www-form-urlencoded"},
        data=body,
    )
    if not r.ok:
        raise SystemExit(f"Create listing failed: {r.status_code} {r.text}")
    listing = r.json()
    listing_id = listing.get("listing_id")
    if not listing_id:
        raise SystemExit("Create succeeded but no listing_id in response.")
    print("Created draft listing:", listing_id)

    # Upload the original meme image as the main photo
    # (you can swap this to a manual mockup file if you prefer)
    with open(image_path, "rb") as f:
        img_data = f.read()
    filename = image_path.name
    files = {"image": (filename, img_data)}
    # multipart/form-data; do not set Content-Type so requests sets boundary
    up = requests.post(
        f"{API_BASE}/application/shops/{shop_id}/listings/{listing_id}/images",
        headers=_auth_headers(token),
        files=files,
        data={"name": filename},
    )
    if not up.ok:
        print("Image upload failed:", up.status_code, up.text)
        print("Draft listing was created; add image manually in Etsy.")
    else:
        print("Image uploaded to listing.")

    return listing_id


def main():
    if len(sys.argv) < 2:
        print("Usage: python etsy_upload_tshirt.py <image.jpg> [listing_title]")
        sys.exit(1)
    image_path = Path(sys.argv[1])
    title = sys.argv[2] if len(sys.argv) > 2 else None
    if not image_path.is_file():
        raise SystemExit(f"Not a file: {image_path}")

    token = load_or_refresh_token()
    shop_id = get_shop_id(token)
    print("Shop ID:", shop_id)
    create_draft_listing(token, shop_id, title, image_path)
    print("Done. Check your Etsy shop dashboard for the draft.")


if __name__ == "__main__":
    main()
