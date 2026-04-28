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
                            {
                                "id": upload_id,
                                "x": 0.5,
                                "y": 0.5,
                                "scale": 1,
                                "angle": 0,
                            }
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
        variant["id"] for variant in template_product.get("variants", []) if variant.get("is_enabled")
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
