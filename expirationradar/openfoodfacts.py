"""UPC → product name/brand via Open Food Facts (free, no key).

No model involved (§3 step 2). Unknown UPC is a normal outcome, not an error.
"""

from __future__ import annotations

import requests

_BASE_URL = "https://world.openfoodfacts.org/api/v2/product"
_TIMEOUT = 10
_HEADERS = {"User-Agent": "ExpirationRadar/0.1 (local pantry app)"}


def lookup(upc: str) -> dict | None:
    """{'product_name': str, 'brand': str, 'upc': str} or None if unresolved.

    "Unresolved" covers both a genuine miss (unknown barcode) and the API
    being unreachable/erroring — this never raises, so scan.py can always
    fall back to OCR-derived naming without a try/except of its own.
    """
    try:
        resp = requests.get(
            f"{_BASE_URL}/{upc}.json",
            params={"fields": "product_name,brands"},
            headers=_HEADERS,
            timeout=_TIMEOUT,
        )
    except requests.RequestException:
        return None

    if resp.status_code != 200:
        return None

    try:
        body = resp.json()
    except ValueError:
        return None

    if not isinstance(body, dict) or body.get("status") == 0:  # not found
        return None

    product = body.get("product")
    if not isinstance(product, dict):
        return None

    brands = (product.get("brands") or "").split(",")[0].strip()
    return {
        "product_name": product.get("product_name") or "",
        "brand": brands,
        "upc": upc,
    }
