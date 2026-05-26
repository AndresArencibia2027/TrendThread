import base64
import os
from pathlib import Path
from typing import Any, Dict, List, Optional

import requests


class PrintifyClient:
    BASE_URL = "https://api.printify.com/v1"

    def __init__(self, api_token: str):
        if not api_token:
            raise ValueError("PRINTIFY_API_TOKEN is required")
        self.api_token = api_token
        self.session = requests.Session()
        self.session.headers.update(
            {
                "Authorization": f"Bearer {self.api_token}",
                "Content-Type": "application/json",
                "User-Agent": "trendthread-automation/1.0",
            }
        )

    def _request(self, method: str, path: str, **kwargs) -> Any:
        url = f"{self.BASE_URL}{path}"
        response = self.session.request(method, url, timeout=60, **kwargs)
        response.raise_for_status()
        if not response.text.strip():
            return {}
        return response.json()

    def get_shops(self) -> List[Dict[str, Any]]:
        return self._request("GET", "/shops.json")

    def upload_image(self, image_path: str) -> str:
        file_path = Path(image_path)
        if not file_path.exists():
            raise FileNotFoundError(f"Image not found: {image_path}")
        with open(file_path, "rb") as image_file:
            encoded = base64.b64encode(image_file.read()).decode("utf-8")
        payload = {"file_name": file_path.name, "contents": encoded}
        result = self._request("POST", "/uploads/images.json", json=payload)
        upload_id = result.get("id")
        if not upload_id:
            raise RuntimeError(f"Unexpected upload response: {result}")
        return upload_id

    def create_product(self, shop_id: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        return self._request("POST", f"/shops/{shop_id}/products.json", json=payload)

    def get_products(self, shop_id: str, page: int = 1, limit: int = 10) -> Dict[str, Any]:
        return self._request("GET", f"/shops/{shop_id}/products.json?page={page}&limit={limit}")

    def get_product(self, shop_id: str, product_id: str) -> Dict[str, Any]:
        return self._request("GET", f"/shops/{shop_id}/products/{product_id}.json")

    def get_product_mockups(self, shop_id: str, product_id: str) -> List[Dict[str, Any]]:
        """
        Returns the list of mockup image objects for a product.
        Each object contains 'src' (URL) and 'variant_ids'.
        Preferred mockup titles: 'Front 2', 'Hanging 1', 'Person 1'–'Person 6'.
        """
        product = self.get_product(shop_id=shop_id, product_id=product_id)
        return product.get("images", [])

    def set_mockup_preferences(
        self,
        shop_id: str,
        product_id: str,
        preferred_titles: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        """
        Requests specific mockup camera/view titles for a product.
        Printify's mockup generation API endpoint — not always available on all plans.
        preferred_titles: e.g. ["Front 2", "Hanging 1", "Person 1"]
        """
        titles = preferred_titles or ["Front 2", "Hanging 1", "Person 1", "Person 2"]
        payload = {"variant_ids": [], "camera_ids": titles}
        try:
            return self._request(
                "POST",
                f"/shops/{shop_id}/products/{product_id}/mockups.json",
                json=payload,
            )
        except Exception as e:
            print(f"  [Mockups] Could not set mockup preferences: {e}")
            return {}

    def publish_product(
        self,
        shop_id: str,
        product_id: str,
        publish_details: Optional[Dict[str, bool]] = None,
    ) -> Dict[str, Any]:
        settings = publish_details or {
            "title": True,
            "description": True,
            "images": True,
            "variants": True,
            "tags": True,
            "keyFeatures": True,
            "shipping_template": True,
        }
        return self._request(
            "POST",
            f"/shops/{shop_id}/products/{product_id}/publish.json",
            json=settings,
        )


# ---------------------------------------------------------------------------
# Listing copy generation (Gemini-powered)
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
    """
    Uses Gemini to generate a sales-optimized title and description.
    Falls back to a rule-based title/description if Gemini fails.

    Returns: {"title": str, "description": str, "tags": List[str]}
    """
    import json
    import re
    from google.genai import types

    system_instruction = """
    You are a conversion-focused Etsy/Shopify copywriter for a trend-driven graphic tee shop.

    Write listing copy that:
    - Has a punchy, specific title that would make someone stop scrolling
    - References the cultural moment without being generic
    - Speaks directly to identity ("this is SO me", "perfect for fans of X")
    - Is concise and scannable — no filler phrases like "high quality" or "great gift"
    - The title format that works well: "[Punchy phrase] | [Cultural anchor] [Product type]"
      Example: "This Couldve Been an Email Shirt | JJK Higuruma Meme"
    - Description: 3–4 short punchy sentences max, then stop. No bullet points.
      Focus on WHO this is for and WHAT it signals about them.

    Output ONLY a JSON object, no markdown, no explanation:
    {
        "title": "...",
        "description": "...",
        "tags": ["tag1", "tag2", "tag3", "tag4", "tag5", "tag6", "tag7", "tag8", "tag9", "tag10", "tag11", "tag12", "tag13"]
    }

    Tags: 13 max, mix of specific (character/moment name) and broad (graphic tee, meme shirt, streetwear).
    Title: 140 characters max.
    Description: 3–4 sentences only — the compliance footer will be appended automatically.
    """

    prompt = (
        f"Term: {term}\n"
        f"Subject: {subject}\n"
        f"Cultural context: {context}\n"
        f"Topic type: {topic_type}\n"
        "Write the listing copy."
    )

    try:
        response = gemini_client.models.generate_content(
            model="gemini-2.0-flash",
            config=types.GenerateContentConfig(system_instruction=system_instruction),
            contents=prompt,
        )
        raw = response.text.strip()
        raw = re.sub(r"^```[a-z]*\n?", "", raw)
        raw = re.sub(r"\n?```$", "", raw)
        result = json.loads(raw)

        title = result.get("title", "")[:140]
        description_body = result.get("description", "").strip()
        tags = result.get("tags", [])[:13]

        # Validate we got something useful
        if not title or not description_body:
            raise ValueError("Empty title or description from Gemini")

        full_description = f"{description_body}\n\n{COMPLIANCE_FOOTER}"
        return {"title": title, "description": full_description, "tags": tags}

    except Exception as e:
        print(f"  [Listing] Gemini copy generation failed: {e}. Using fallback.")
        return _fallback_listing_copy(term, subject)


def _fallback_listing_copy(term: str, subject: str) -> Dict[str, str]:
    """Rule-based fallback if Gemini copy generation fails."""
    clean_term = term.replace("_", " ").title()
    clean_subject = subject.replace("_", " ").title()
    title = f"{clean_term} Graphic Tee | {clean_subject} Fan Shirt"[:140]
    description = (
        f"A design for fans of {clean_subject}. "
        "Clean graphic, comfortable fit, made to order.\n\n"
        + COMPLIANCE_FOOTER
    )
    tags = [
        term.lower(), subject.lower(), "graphic tee", "meme shirt",
        "streetwear", "fan gift", "trending", "unisex tee",
        "aesthetic shirt", "pop culture", "gift idea", "casual wear", "art tee"
    ]
    return {"title": title, "description": description, "tags": tags[:13]}


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
) -> Dict[str, Any]:
    variants = [{"id": variant_id, "price": 2499, "is_enabled": True} for variant_id in variant_ids]
    return {
        "title": title[:140],
        "description": description[:3000],
        "blueprint_id": blueprint_id,
        "print_provider_id": print_provider_id,
        "tags": tags[:13],
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


def build_payload_from_template(
    title: str,
    description: str,
    tags: List[str],
    template_product: Dict[str, Any],
    upload_id: str,
) -> Dict[str, Any]:
    blueprint_id = template_product["blueprint_id"]
    print_provider_id = template_product["print_provider_id"]
    enabled_variant_ids = [
        variant["id"] for variant in template_product.get("variants", [])
        if variant.get("is_enabled")
    ]
    if not enabled_variant_ids:
        enabled_variant_ids = [variant["id"] for variant in template_product.get("variants", [])]
    if not enabled_variant_ids:
        raise RuntimeError("Template product has no variants.")

    front_position = "front"
    placeholders = template_product.get("print_areas", [{}])[0].get("placeholders", [])
    if placeholders and not any(p.get("position") == "front" for p in placeholders):
        front_position = placeholders[0].get("position", "front")

    variants = [{"id": variant_id, "price": 2499, "is_enabled": True} for variant_id in enabled_variant_ids]
    return {
        "title": title[:140],
        "description": description[:3000],
        "blueprint_id": blueprint_id,
        "print_provider_id": print_provider_id,
        "tags": tags[:13],
        "variants": variants,
        "print_areas": [
            {
                "variant_ids": enabled_variant_ids,
                "placeholders": [
                    {
                        "position": front_position,
                        "images": [
                            {"id": upload_id, "x": 0.5, "y": 0.5, "scale": 1, "angle": 0},
                        ],
                    }
                ],
            }
        ],
    }


def build_client_from_env() -> PrintifyClient:
    return PrintifyClient(api_token=os.getenv("PRINTIFY_API_TOKEN", ""))