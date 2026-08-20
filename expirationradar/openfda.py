"""api.fda.gov/food/enforcement.json — recall cross-reference (free, no key).

Quirk that must be honored (§3 step 5): openFDA returns HTTP 404 for a query
with zero results. That is "no recall", NOT an error — do not raise on it.
"""

from __future__ import annotations

from expirationradar.models import RecallMatch


def search_by_upc(upc: str) -> list[RecallMatch]:
    """Match against the enforcement record's `code_info` field."""
    raise NotImplementedError("Phase 1 — see plan §3")


def search_by_terms(product_name: str, brand: str = "") -> list[RecallMatch]:
    """Fuzzier fallback across product_description / recalling_firm."""
    raise NotImplementedError("Phase 1 — see plan §3")
