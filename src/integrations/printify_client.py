"""
printify_client.py
==================
Printify v1 API client for the TrendThread pipeline.

Mockup selection: Printify returns mockups inline with the create-product
response. Each image has:
  - mockup_id: numeric camera/view identifier (e.g. 102044=Front 2, 102048=Hanging 1)
  - position: human-readable label (e.g. "Front 2, White")
  - src: full mockup URL
  - variant_ids: which variants this mockup applies to
  - is_default: whether it's marked as the default mockup
  - is_selected_for_publishing: whether it goes to the sales channel on publish
  - order: display order

To pick which mockups appear on Etsy:
  1. Filter the create-response images array by mockup_id
  2. Set is_selected_for_publishing=True on matches, False on the rest
  3. PUT the updated images array back to the product
  4. Publish
"""

import base64
import json
import os
from pathlib import Path
from typing import Any, Dict, List, Optional

import requests


BLUEPRINT_ID_BELLA_CANVAS_3001 = 12
PROVIDER_ID_PRINTIFY_CHOICE = 99

DEFAULT_COLORS = [
    "Black", "White", "Navy", "Red", "Brown",
    "Athletic Heather", "Heather Forest", "True Royal",
    "Maroon", "Soft Cream", "Dark Grey Heather", "Heather Mauve",
]

DEFAULT_SIZES = ["S", "M", "L", "XL"]

# mockup_id is a STRING in format: "{product_id}_{variant_id}_{camera_id}_{slug}"
# Example: "6a22133e93fc829ff5052fa2_18078_102044_front-2"
# We match on the trailing slug since it's the most stable identifier.
PREFERRED_MOCKUP_SLUGS = ["front-2", "hanging-1"]

# Printify caps mockups per product at 20 when publishing to Etsy.
MAX_MOCKUPS_PER_PRODUCT = 20

DEFAULT_PRICE_CENTS = 1999


class PrintifyClient:
    BASE_URL = "https://api.printify.com/v1"

    def __init__(self, api_token: str):
        if not api_token:
            raise ValueError("PRINTIFY_API_TOKEN is required")
        self.api_token = api_token
        self.session = requests.Session()
        self.session.headers.update({
            "Authorization": f"Bearer {self.api_token}",
            "Content-Type": "application/json",
            "User-Agent": "trendthread-automation/1.0",
        })

    def _request(self, method: str, path: str, **kwargs) -> Any:
        url = f"{self.BASE_URL}{path}"
        response = self.session.request(method, url, timeout=60, **kwargs)
        if not response.ok:
            try:
                err_body = response.json()
            except Exception:
                err_body = response.text[:500]
            raise RuntimeError(
                f"Printify API {response.status_code} on {method} {path}: {err_body}"
            )
        if not response.text.strip():
            return {}
        return response.json()

    def get_shops(self) -> List[Dict[str, Any]]:
        return self._request("GET", "/shops.json")

    def get_blueprint_variants(self, blueprint_id: int, print_provider_id: int) -> Dict[str, Any]:
        return self._request(
            "GET",
            f"/catalog/blueprints/{blueprint_id}/print_providers/{print_provider_id}/variants.json",
        )

    def list_available_colors(
        self,
        blueprint_id: int = BLUEPRINT_ID_BELLA_CANVAS_3001,
        print_provider_id: int = PROVIDER_ID_PRINTIFY_CHOICE,
    ) -> List[str]:
        catalog = self.get_blueprint_variants(blueprint_id, print_provider_id)
        colors = set()
        for v in catalog.get("variants", []):
            color = v.get("options", {}).get("color")
            if color:
                colors.add(str(color))
        return sorted(colors)

    def lookup_variant_ids(
        self,
        blueprint_id: int = BLUEPRINT_ID_BELLA_CANVAS_3001,
        print_provider_id: int = PROVIDER_ID_PRINTIFY_CHOICE,
        colors: Optional[List[str]] = None,
        sizes: Optional[List[str]] = None,
    ) -> List[int]:
        wanted_colors = [c.lower() for c in (colors or DEFAULT_COLORS)]
        wanted_sizes = [s.lower() for s in (sizes or DEFAULT_SIZES)]

        catalog = self.get_blueprint_variants(blueprint_id, print_provider_id)
        variants = catalog.get("variants", [])

        matched: List[int] = []
        found_combos = set()
        for v in variants:
            opts = v.get("options", {})
            color = str(opts.get("color", "")).lower()
            size = str(opts.get("size", "")).lower()
            if color in wanted_colors and size in wanted_sizes:
                matched.append(int(v["id"]))
                found_combos.add((color, size))

        expected = {(c, s) for c in wanted_colors for s in wanted_sizes}
        missing = expected - found_combos
        if missing:
            missing_colors = sorted({c for c, _ in missing})
            print(f"  [Printify] Colors not in catalog: {missing_colors}")

        if not matched:
            raise RuntimeError(f"No matching variants found for colors={colors} sizes={sizes}")

        print(f"  [Printify] Matched {len(matched)} variants")
        return matched

    def upload_image(self, image_path: str) -> str:
        file_path = Path(image_path)
        if not file_path.exists():
            raise FileNotFoundError(f"Image not found: {image_path}")
        with open(file_path, "rb") as f:
            encoded = base64.b64encode(f.read()).decode("utf-8")
        payload = {"file_name": file_path.name, "contents": encoded}
        result = self._request("POST", "/uploads/images.json", json=payload)
        upload_id = result.get("id")
        if not upload_id:
            raise RuntimeError(f"Unexpected upload response: {result}")
        return upload_id

    def create_product(self, shop_id: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        return self._request("POST", f"/shops/{shop_id}/products.json", json=payload)

    def update_product(self, shop_id: str, product_id: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        return self._request("PUT", f"/shops/{shop_id}/products/{product_id}.json", json=payload)

    def get_product(self, shop_id: str, product_id: str) -> Dict[str, Any]:
        return self._request("GET", f"/shops/{shop_id}/products/{product_id}.json")

    def select_mockups_for_publishing(
        self,
        shop_id: str,
        product_id: str,
        product_data: Optional[Dict[str, Any]] = None,
        mockup_slugs: Optional[List[str]] = None,
        max_mockups: int = MAX_MOCKUPS_PER_PRODUCT,
    ) -> Dict[str, Any]:
        """
        Determines which mockups SHOULD be selected, for logging purposes only.

        IMPORTANT: Printify's v1 API does not support updating image
        selection via PUT /products/{id}.json. Empirical testing showed
        that PUT'ing the images array back actually WIPES it (verify GET
        returns 0 images). So we do not send the PUT at all.

        This function still exists to log what we WOULD have selected,
        which is useful when prompting the user to manually pick the same
        mockups in the Printify UI.
        """
        priority_slugs = list(mockup_slugs or PREFERRED_MOCKUP_SLUGS)

        product = product_data or self.get_product(shop_id=shop_id, product_id=product_id)
        images = product.get("images", []) or []

        if not images:
            print("  [Mockups] No images on product")
            return {}

        def _extract_slug(mockup_id_str: Any) -> str:
            if not isinstance(mockup_id_str, str):
                return ""
            parts = mockup_id_str.rsplit("_", 1)
            return parts[-1] if len(parts) > 1 else ""

        buckets: Dict[str, List[Dict[str, Any]]] = {s: [] for s in priority_slugs}
        for img in images:
            slug = _extract_slug(img.get("mockup_id"))
            if slug in buckets:
                buckets[slug].append(img)

        for slug in priority_slugs:
            buckets[slug].sort(key=lambda im: (im.get("variant_ids") or [0])[0])

        selected = 0
        breakdown = {}
        for slug in priority_slugs:
            for _ in buckets[slug]:
                if selected >= max_mockups:
                    break
                selected += 1
                breakdown[slug] = breakdown.get(slug, 0) + 1
            if selected >= max_mockups:
                break

        if selected:
            summary = ", ".join(f"{slug}×{cnt}" for slug, cnt in breakdown.items())
            print(f"  [Mockups] Would select {selected}/{max_mockups} ({summary}) — manual selection required in Printify UI")
        else:
            print(f"  [Mockups] No mockups matched slugs {priority_slugs}")

        return {}

    def publish_product(
        self,
        shop_id: str,
        product_id: str,
        publish_details: Optional[Dict[str, bool]] = None,
    ) -> Dict[str, Any]:
        settings = publish_details or {
            "title": True, "description": True, "images": True,
            "variants": True, "tags": True, "keyFeatures": True,
            "shipping_template": True,
        }
        return self._request(
            "POST",
            f"/shops/{shop_id}/products/{product_id}/publish.json",
            json=settings,
        )


# ---------------------------------------------------------------------------
# Listing copy generation
# ---------------------------------------------------------------------------

COMPLIANCE_FOOTER = """EU representative: HONSON VENTURES LIMITED, gpsr@honsonventures.com, 3, Gnaftis House flat 102, Limassol, Mesa Geitonia, 4003, CY
Product information: Bella+Canvas 3001, 2 year warranty in EU and Northern Ireland as per Directive 1999/44/EC
Warnings, Hazard: For adults, Blank product sourced from Honduras
Care instructions: Machine wash: cold (max 30C or 90F), Non-chlorine: bleach as needed, Tumble dry: low heat, Iron, steam or dry: medium heat, Do not dryclean"""


def generate_listing_copy(
    gemini_client,
    term: str,
    subject: str,
    context: str,
    topic_type: str = "GENERAL",
) -> Dict[str, str]:
    import re
    from google.genai import types

    system_instruction = """
You are an Etsy/Shopify copywriter for a trend-driven graphic tee shop.

TITLE (max 140 chars):
- Punchy, specific, includes the trending term/character/phrase
- Format: "[Specific Reference] [Product Type] | [Identity/Vibe Hook] | [Adjective] [Audience]"
- Example: "Gojo Satoru Shirt | Six Eyes Anime Tee | Aesthetic JJK Fan Gift"
- No filler: "Premium", "High Quality", "Best", "Amazing"

DESCRIPTION — three short paragraphs, no bullets, no emojis:
1. Hook (1-2 sentences): What it is, who it's for, why it slaps.
2. Detail (2-3 sentences): Bella+Canvas 3001 unisex tee, soft cotton,
   S-XL, multiple colors, DTG printing for vivid color.
3. Closing (1-2 sentences): Use case, vibe, sign-off.

TAGS (exactly 10):
- Mix specific (character, series, viral phrase) and broad (graphic tee, streetwear)
- Lowercase, max 20 chars each

Output ONLY a JSON object:
{
    "title": "...",
    "description": "...",
    "tags": ["tag1", ..., "tag10"]
}
"""

    prompt = (
        f"Term: {term}\n"
        f"Subject: {subject}\n"
        f"Cultural context: {context}\n"
        f"Topic type: {topic_type}\n\n"
        "Write the listing copy. Make it human, not AI marketing copy."
    )

    try:
        response = gemini_client.models.generate_content(
            model="gemini-2.5-flash",
            config=types.GenerateContentConfig(system_instruction=system_instruction),
            contents=prompt,
        )
        raw = response.text.strip()
        raw = re.sub(r"^```[a-z]*\n?", "", raw)
        raw = re.sub(r"\n?```$", "", raw)
        result = json.loads(raw)

        title = result.get("title", "")[:140]
        description_body = result.get("description", "").strip()
        tags = result.get("tags", [])[:10]
        tags = [t.lower().strip()[:20] for t in tags if t.strip()]

        if not title or not description_body:
            raise ValueError("Empty title or description")

        full_description = f"{description_body}\n\n{COMPLIANCE_FOOTER}"
        return {"title": title, "description": full_description, "tags": tags}

    except Exception as e:
        print(f"  [Listing] Gemini copy failed: {e}. Using fallback.")
        return _fallback_listing_copy(term, subject)


def _fallback_listing_copy(term: str, subject: str) -> Dict[str, str]:
    clean_term = term.replace("_", " ").title()
    title = f"{clean_term} Graphic Tee | Trending Unisex Fan Shirt"[:140]
    description = (
        f"A bold graphic tee for fans of {clean_term}. "
        "Designed to feel like the shirt you reach for first.\n\n"
        "Printed on Bella+Canvas 3001 — soft ringspun cotton, unisex fit, "
        "S-XL across multiple colors. Direct-to-garment printing keeps every detail crisp.\n\n"
        f"Perfect for showing your love of {clean_term} anywhere.\n\n"
        + COMPLIANCE_FOOTER
    )
    tags = [
        term.lower()[:20], "graphic tee", "meme shirt", "streetwear",
        "fan gift", "trending tee", "unisex tee", "aesthetic shirt",
        "pop culture", "gift idea",
    ]
    return {"title": title, "description": description, "tags": tags[:10]}


# ---------------------------------------------------------------------------
# Payload builders
# ---------------------------------------------------------------------------

def build_tshirt_payload(
    title: str,
    description: str,
    tags: List[str],
    blueprint_id: int,
    print_provider_id: int,
    variant_ids: List[int],
    upload_id: str,
    price_cents: int = DEFAULT_PRICE_CENTS,
) -> Dict[str, Any]:
    variants = [
        {"id": variant_id, "price": price_cents, "is_enabled": True}
        for variant_id in variant_ids
    ]
    return {
        "title": title[:140],
        "description": description[:3000],
        "blueprint_id": blueprint_id,
        "print_provider_id": print_provider_id,
        "tags": tags[:10],
        "variants": variants,
        "print_areas": [
            {
                "variant_ids": variant_ids,
                "placeholders": [
                    {
                        "position": "front",
                        "images": [
                            {"id": upload_id, "x": 0.5, "y": 0.5, "scale": 1, "angle": 0}
                        ],
                    }
                ],
            }
        ],
    }


def build_client_from_env() -> PrintifyClient:
    return PrintifyClient(api_token=os.getenv("PRINTIFY_API_TOKEN", ""))


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------

def create_and_publish_product(
    client: PrintifyClient,
    shop_id: str,
    image_path: str,
    title: str,
    description: str,
    tags: List[str],
    colors: Optional[List[str]] = None,
    sizes: Optional[List[str]] = None,
    price_cents: int = DEFAULT_PRICE_CENTS,
    mockup_slugs: Optional[List[str]] = None,
    auto_publish: bool = False,
) -> Dict[str, Any]:
    """
    End-to-end: upload → lookup variants → create → log mockup recommendation.

    Printify v1 doesn't support API-driven mockup selection, so this function
    creates the product as a draft and surfaces the Printify URL. The user
    opens the URL, manually selects mockups, and clicks Publish in the UI.

    The returned product dict includes:
      - _printify_url: direct link to the product editor in Printify
      - _needs_manual_mockup_selection: always True for Printify v1

    Set auto_publish=True to publish without mockup selection (creates a
    broken Etsy listing — not recommended).
    """
    upload_id = client.upload_image(image_path)

    variant_ids = client.lookup_variant_ids(
        colors=colors or DEFAULT_COLORS,
        sizes=sizes or DEFAULT_SIZES,
    )

    payload = build_tshirt_payload(
        title=title,
        description=description,
        tags=tags,
        blueprint_id=BLUEPRINT_ID_BELLA_CANVAS_3001,
        print_provider_id=PROVIDER_ID_PRINTIFY_CHOICE,
        variant_ids=variant_ids,
        upload_id=upload_id,
        price_cents=price_cents,
    )
    product = client.create_product(shop_id=shop_id, payload=payload)
    product_id = product["id"]
    print(f"  [Printify] Created product {product_id}")

    # Log which mockups SHOULD be selected — does not actually do it,
    # since Printify v1 PUT silently wipes the images array.
    client.select_mockups_for_publishing(
        shop_id=shop_id,
        product_id=product_id,
        product_data=product,
        mockup_slugs=mockup_slugs or PREFERRED_MOCKUP_SLUGS,
    )

    printify_url = f"https://printify.com/app/editor/{product_id}"
    product["_printify_url"] = printify_url
    product["_needs_manual_mockup_selection"] = True

    print(f"  [Printify] Open this URL, select mockups, then publish:")
    print(f"  [Printify] → {printify_url}")

    if auto_publish:
        client.publish_product(shop_id=shop_id, product_id=product_id)
        print(f"  [Printify] Published {product_id} (likely broken — no mockups)")

    return product