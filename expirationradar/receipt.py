"""Receipt photo → N pantry candidates with ESTIMATED expiry dates.

Mirrors scan.run_scan's contract exactly (same ScanResult/ScanCandidate
shape, "never raises for a missing tier" rule) so server.py/cli.py can call
this symmetrically. `parse_receipt` is the single entry point.

OCR (dense PSM 6) → `parse_receipt` text chain (line items) → vision fallback
if the text tier came up empty → shelf_life.py estimate per item. Receipts
have no per-item barcode, so this skips barcode lookup and the openFDA recall
cross-reference entirely (a warning says so instead of guessing).
"""

from __future__ import annotations

import json
import re
from datetime import date, datetime, timedelta

from llm_ladder.engine import run_cascade
from llm_ladder.ollama_client import OllamaConnectionError

from expirationradar import dates, ocr, shelf_life, vision
from expirationradar.models import Field, ScanCandidate, ScanResult
from expirationradar.vision import load_chain

_OCR_TEXT_LIMIT = 4000  # a receipt is denser than a label; give the chain more room
_MAX_NAME_LEN = 80

# A category matched something more specific than the fallback bucket.
_CONFIDENCE_MATCHED = 0.45
# The default "nothing matched" bucket, or a vision-only (no category) read.
_CONFIDENCE_DEFAULT = 0.3

_ESTIMATE_WARNING = (
    "Expiry dates are estimated from typical shelf life, not printed on a "
    "receipt — review before adding."
)
_RECALL_WARNING = (
    "Recall check skipped for receipt items: there's no per-item barcode on a "
    "receipt, and matching noisy line-item names to recalls isn't reliable "
    "enough to trust."
)
_RAW_LINES_WARNING = (
    "Ollama is off: line items were guessed from raw receipt text instead of "
    "cleaned up by a model — names may be rough."
)

# Last-resort fallback (both the text chain and vision are unavailable):
# lines that are clearly receipt boilerplate, not product names.
_BOILERPLATE = re.compile(
    r"\b(total|subtotal|tax|cash|change|visa|mastercard|debit|credit|balance|"
    r"thank you|cashier|register|receipt|store|auth|approval|tender|"
    r"savings|discount|coupon|member|loyalty|order|survey)\b",
    re.IGNORECASE,
)
_PRICE_ONLY = re.compile(r"^[\$\d.,\s\-]+$")
_WORDS = re.compile(r"[A-Za-z]{3,}")

_PROMPT = """Below is raw OCR text from a grocery store receipt.

Extract every purchased product line item. Ignore prices, quantities, SKUs,
subtotals, tax, payment info, and store/header boilerplate.

Return ONLY a JSON array, no prose, no markdown fences, in this exact shape:
[{"name": "...", "category_guess": "..."}]

Rules:
- "name" is the product name as best you can read it, cleaned up (title
  case, drop stray codes), e.g. "Whole Milk 1 Gal".
- "category_guess" is your best single-word-or-short-phrase guess at its food
  category (e.g. "dairy", "produce", "meat", "frozen", "canned", "bakery",
  "snack", "condiment", "grain"). Guess your best category even if unsure.
- Skip lines that aren't food/grocery products.
- If you can't extract anything useful, return [].

OCR TEXT:
"""


def parse_receipt(image_bytes: bytes) -> ScanResult:
    """The whole receipt pipeline. Never raises for a missing tier —
    degradations land in ScanResult.warnings, same rule as scan.run_scan."""
    warnings: list[str] = [_ESTIMATE_WARNING, _RECALL_WARNING]

    text = _ocr_text(image_bytes, warnings)
    items = _parse_line_items(text) if text.strip() else []

    vision_available = vision.is_available()
    if not items:
        items = _vision_line_items(image_bytes, warnings)
    if not items and text.strip():
        # Both model tiers are unavailable (e.g. Ollama off). Degrade to a
        # default-bucket estimate per plausible OCR line rather than
        # returning nothing — see docs/API.md's degradation table.
        items = _raw_text_line_items(text)
        if items:
            warnings.append(_RAW_LINES_WARNING)

    purchase_date = _find_purchase_date(text) or date.today()
    candidates = [_to_candidate(item, purchase_date) for item in items]

    return ScanResult(
        candidates=candidates,
        scanned_at=datetime.now().isoformat(timespec="seconds"),
        vision_available=vision_available,
        warnings=warnings,
    )


def _ocr_text(image_bytes: bytes, warnings: list[str]) -> str:
    """Dense line-oriented PSM — a receipt isn't scattered fragments like a
    pantry label, it's one column of print (ocr.py's `_PSM_RECEIPT`)."""
    try:
        return ocr.extract_text(image_bytes, psm=ocr.PSM_RECEIPT)
    except ocr.OCRError as exc:
        warnings.append(f"text extraction unavailable: {exc}")
        return ""


def _parse_line_items(text: str) -> list[dict]:
    """`parse_receipt` text-chain cascade over OCR text. [] if Ollama is off,
    the chain isn't configured, or the reply didn't parse."""
    chain = load_chain("parse_receipt")
    if not chain or not chain.tiers:
        return []
    prompt = _PROMPT + text[:_OCR_TEXT_LIMIT]
    try:
        result = run_cascade(prompt, "parse_receipt", chain)
    except (OllamaConnectionError, ValueError, RuntimeError):
        return []
    return _parse_json_items(result.answer)


def _parse_json_items(raw: str) -> list[dict]:
    """A JSON array of {name, category_guess} out of a model reply, tolerant
    of fences/prose around it (same approach as vision.py's `_parse_items`)."""
    start, end = raw.find("["), raw.rfind("]")
    if start == -1 or end < start:
        return []
    try:
        items = json.loads(raw[start : end + 1])
    except ValueError:
        return []
    if not isinstance(items, list):
        return []

    cleaned = []
    for item in items:
        if not isinstance(item, dict):
            continue
        name = item.get("name")
        if not isinstance(name, str) or not name.strip():
            continue
        category = item.get("category_guess")
        cleaned.append(
            {
                "name": name.strip()[:_MAX_NAME_LEN],
                "category_guess": category.strip() if isinstance(category, str) else None,
            }
        )
    return cleaned


def _vision_line_items(image_bytes: bytes, warnings: list[str]) -> list[dict]:
    """Fallback when OCR text is too sparse/garbled for the text chain to
    produce anything: read line items straight off the photo instead."""
    items = vision.extract_receipt_items(image_bytes)
    if items is None:
        warnings.append(
            f"vision fallback: {vision.UNAVAILABLE_NOTE} — could not extract any "
            "line items from this receipt"
        )
        return []
    return [
        {
            "name": item["name"].strip()[:_MAX_NAME_LEN],
            "category_guess": item.get("category_guess"),
        }
        for item in items
    ]


def _raw_text_line_items(text: str) -> list[dict]:
    """Plausible-looking OCR lines treated as bare item names (no category
    guess — shelf_life.py's default bucket covers them). The lazy last-resort
    fallback: better than showing nothing when both model tiers are down."""
    items = []
    for line in text.splitlines():
        line = line.strip()
        if (
            len(line) < 3
            or len(line) > _MAX_NAME_LEN
            or not _WORDS.search(line)
            or _PRICE_ONLY.match(line)
            or _BOILERPLATE.search(line)
            or dates.parse_dates(line)
        ):
            continue
        items.append({"name": line, "category_guess": None})
    return items


def _find_purchase_date(text: str) -> date | None:
    """Best date found anywhere in the raw OCR text — most receipts print one
    near the top or bottom. Reuses dates.py's deterministic regex parser."""
    found = dates.parse_dates(text) if text else []
    if not found:
        return None
    return date.fromisoformat(found[0].value)


def _to_candidate(item: dict, purchase_date: date) -> ScanCandidate:
    name = item["name"]
    category_hint = item.get("category_guess")
    days, matched_category = shelf_life.estimate_shelf_life_days(name, category_hint)
    confidence = _CONFIDENCE_MATCHED if matched_category != "unknown" else _CONFIDENCE_DEFAULT
    expiry = (purchase_date + timedelta(days=days)).isoformat()

    return ScanCandidate(
        product_name=Field(name, "OCR", 0.5),
        expiry_date=Field(expiry, "ESTIMATED", confidence),
    )


def _demo() -> None:
    """Offline self-check: an item with a category guess estimates a
    plausible expiry from a given purchase date, ESTIMATED-sourced and low
    confidence per the app's provenance rule."""
    candidate = _to_candidate(
        {"name": "Whole Milk 1 Gal", "category_guess": "dairy"}, date(2026, 8, 1)
    )
    assert candidate.product_name.value == "Whole Milk 1 Gal"
    assert candidate.expiry_date.source == "ESTIMATED"
    assert candidate.expiry_date.value == "2026-08-08"  # milk/cream bucket = 7 days
    assert candidate.expiry_date.confidence < 0.5  # a guess, never trusted like a real read
    print("receipt ok")


if __name__ == "__main__":
    _demo()
