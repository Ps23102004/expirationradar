"""api.fda.gov/food/enforcement.json — recall cross-reference (free, no key).

Quirk that must be honored (§3 step 5): openFDA returns HTTP 404 for a query
with zero results. That is "no recall", NOT an error — do not raise on it.
"""

from __future__ import annotations

import os

import requests

from expirationradar.models import RecallMatch

_BASE_URL = "https://api.fda.gov/food/enforcement.json"
_TIMEOUT = 10
_LIMIT = 10


class OpenFDAError(Exception):
    pass


def _params(search_clause: str) -> dict[str, str]:
    params = {"search": search_clause, "limit": str(_LIMIT)}
    api_key = os.environ.get("OPENFDA_API_KEY")
    if api_key:
        params["api_key"] = api_key
    return params


def _fetch(search_clause: str) -> tuple[list[dict], str]:
    """Run the request; a 404 (zero matches) returns ([], url), not an error."""
    try:
        resp = requests.get(_BASE_URL, params=_params(search_clause), timeout=_TIMEOUT)
    except requests.RequestException as exc:
        raise OpenFDAError(f"could not reach openFDA: {exc}") from exc

    if resp.status_code == 404:
        # openFDA's documented quirk: zero results = 404 + an error-JSON body.
        # Any 404 here means "no recall found" — never raise on it.
        return [], resp.url
    if resp.status_code != 200:
        raise OpenFDAError(f"openFDA returned {resp.status_code}: {resp.text[:500]}")

    try:
        body = resp.json()
    except ValueError as exc:
        raise OpenFDAError(f"openFDA returned malformed JSON: {exc}") from exc
    return body.get("results", []), resp.url


def _to_match(raw: dict, matched_on: str, url: str) -> RecallMatch:
    return RecallMatch(
        recall_number=raw.get("recall_number", ""),
        status=raw.get("status", ""),
        classification=raw.get("classification", ""),
        reason=raw.get("reason_for_recall", ""),
        product_description=raw.get("product_description", ""),
        recalling_firm=raw.get("recalling_firm", ""),
        report_date=raw.get("report_date", ""),
        matched_on=matched_on,
        url=url,
    )


def _ongoing_first(matches: list[RecallMatch]) -> list[RecallMatch]:
    """Stable sort: `Ongoing` records surface first (the loud status)."""
    return sorted(matches, key=lambda m: m.status != "Ongoing")


def search_by_upc(upc: str) -> list[RecallMatch]:
    """Match against the enforcement record's `code_info` field."""
    results, url = _fetch(f'code_info:"{upc}"')
    return _ongoing_first([_to_match(r, "upc", url) for r in results])


def search_by_terms(product_name: str, brand: str = "") -> list[RecallMatch]:
    """Fuzzier fallback across product_description / recalling_firm."""
    terms = [t for t in (product_name.strip(), brand.strip()) if t]
    if not terms:
        return []
    clause = " OR ".join(
        f'product_description:"{t}" OR recalling_firm:"{t}"' for t in terms
    )
    results, url = _fetch(clause)
    return _ongoing_first([_to_match(r, "product_terms", url) for r in results])
