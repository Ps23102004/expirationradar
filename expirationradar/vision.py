"""FALLBACK-ONLY tier: Ollama /api/chat + image → strict JSON.

Triggered only when barcode + OFF + OCR left a date or product missing (§3
step 4). Never overrides a deterministic read; everything it produces is
labeled VISION provenance. Ollama down or model not pulled → `extract` returns
None and the caller degrades to "N/A (vision unavailable)" — it never raises
upward.

Model tag lives in chains.yaml under `vision_extract` (§5).
"""

from __future__ import annotations

import base64
import json
import re

import requests
from llm_ladder.config import ChainConfig, load_chains
from llm_ladder.ollama_client import OllamaConnectionError, chat, resolve_host

from expirationradar.barcode import is_valid_upc
from expirationradar.dates import normalize_date
from expirationradar.models import Field, ScanCandidate

# chains.yaml sits next to the package — true for an editable install and for
# the wheel's force-include block (pyproject), same resolution server.py uses
# for web/.
from pathlib import Path

CHAINS_PATH = Path(__file__).resolve().parent.parent / "chains.yaml"

# What the UI shows for a field the vision tier would have filled if it could.
UNAVAILABLE_NOTE = "N/A (vision unavailable)"

# Deliberately below every deterministic confidence in the app (barcode 1.0,
# Open Food Facts 0.9, a labelled OCR date 0.85). A VISION field is always the
# least-trusted thing on the confirm-edit screen.
VISION_CONFIDENCE = 0.4

# Measured on this machine: ~35s warm, ~110s when a text model is also resident
# and the 6.1GB vision model has to be re-loaded. llm-ladder's 120s default cut
# a working cold-start call off mid-flight, so image calls get their own budget.
_TIMEOUT = 300

_ISO = re.compile(r"\d{4}-\d{2}-\d{2}")
_NULLISH = {"null", "none", "n/a", "na", "unknown", "not visible", "not legible"}

_PROMPT = """You are looking at a photo of one or more grocery or pantry products.

Return ONLY a JSON array. No prose, no explanation, no markdown code fences.

One object per distinct product visible in the photo, in this exact shape:
[{"name": "...", "brand": "...", "expiry_date": "YYYY-MM-DD", "upc": "..."}]

Rules:
- "name" is the product as printed on the package, e.g. "Crunchy Oat Granola, 12 oz".
- "brand" is the manufacturer or label name only.
- "expiry_date" is the printed BEST BY / USE BY / SELL BY / EXP date converted to
  YYYY-MM-DD. US labels print MM/DD/YY. If only a month and year are printed,
  use the 1st of that month.
- "upc" is the barcode's digits, only if they are legible.
- Use null for anything you cannot actually read. Never guess a date or a UPC.
- If the photo shows a shelf or a group, return one object per item you can see.
- If no product is visible at all, return [].
"""

# receipt.py's vision fallback: triggered when OCR + the parse_receipt text
# chain can't produce line items (garbled photo, Ollama's text tier off).
RECEIPT_PROMPT = """You are looking at a photo of a grocery store receipt.

Return ONLY a JSON array. No prose, no explanation, no markdown code fences.

One object per purchased product line item, in this exact shape:
[{"name": "...", "category_guess": "..."}]

Rules:
- Ignore prices, quantities, SKUs, subtotals, tax, payment info, and store/
  header boilerplate — only real product line items.
- "name" is the product name as best you can read it.
- "category_guess" is your best single-word-or-short-phrase guess at its food
  category (e.g. "dairy", "produce", "meat", "frozen", "canned", "bakery",
  "snack", "condiment", "grain"). Guess your best category even if unsure.
- If you can't read any line items at all, return [].
"""


def load_chain(name: str) -> ChainConfig | None:
    """Accessor for this repo's chains.yaml (§5). None = chain not configured.

    Shared with scan.py, which pulls the `normalize_product` text chain from
    the same file.
    """
    try:
        return load_chains(str(CHAINS_PATH)).get(name)
    except ValueError:
        return None


def has_value(candidate: ScanCandidate) -> bool:
    """True if anything at all was extracted for this candidate."""
    return any(
        f.value
        for f in (
            candidate.product_name,
            candidate.brand,
            candidate.upc,
            candidate.expiry_date,
        )
    )


def vision_model() -> str | None:
    """The tag configured for the `vision_extract` chain's first tier."""
    chain = load_chain("vision_extract")
    return chain.tiers[0].model if chain and chain.tiers else None


def is_available() -> bool:
    """True if the vision model in chains.yaml is reachable and pulled.

    Asks Ollama's /api/tags rather than running the model: this is called on
    every scan just to set `ScanResult.vision_available`, so it must not cost
    a model load.
    """
    model = vision_model()
    if not model:
        return False
    try:
        resp = requests.get(f"{resolve_host()}/api/tags", timeout=5)
        resp.raise_for_status()
        tags = {m.get("name", "") for m in resp.json().get("models", [])}
    except (requests.RequestException, ValueError, AttributeError):
        return False
    return model in tags or f"{model}:latest" in tags


def _call_vision(prompt: str, image_bytes: bytes) -> list[dict] | None:
    """One image call → the parsed JSON array of dicts, or `None` if the tier
    is unavailable (Ollama off, model not pulled, or no `vision_extract` chain
    configured). Shared by `extract` and `extract_receipt_items`.
    """
    model = vision_model()
    if not model:
        return None
    try:
        resp = chat(
            model,
            prompt,
            images=[base64.b64encode(image_bytes).decode()],
            timeout=_TIMEOUT,
        )
    except OllamaConnectionError:
        # Covers OllamaModelNotFoundError too (it subclasses this one).
        return None

    content = resp.get("message", {}).get("content", "") if isinstance(resp, dict) else ""
    return _parse_items(content)


def extract(image_bytes: bytes) -> list[ScanCandidate] | None:
    """One image call → candidates with VISION-sourced fields.

    `None` means the tier is unavailable — the caller degrades to
    UNAVAILABLE_NOTE. `[]` means vision ran and saw nothing.
    """
    items = _call_vision(_PROMPT, image_bytes)
    if items is None:
        return None
    return [c for c in (_to_candidate(i) for i in items) if has_value(c)]


def extract_receipt_items(image_bytes: bytes) -> list[dict] | None:
    """receipt.py's vision fallback: `[{"name", "category_guess"}]` per line
    item read straight off the photo. Same `None`/`[]` sentinel as `extract`.
    """
    items = _call_vision(RECEIPT_PROMPT, image_bytes)
    if items is None:
        return None
    return [i for i in items if isinstance(i.get("name"), str) and i["name"].strip()]


def _parse_items(raw: str) -> list[dict]:
    """The JSON array out of a model reply, tolerating fences//prose around it."""
    start, end = raw.find("["), raw.rfind("]")
    if start == -1 or end < start:
        return []
    try:
        items = json.loads(raw[start : end + 1])
    except ValueError:
        return []
    if not isinstance(items, list):
        return []
    return [i for i in items if isinstance(i, dict)]


def _clean(value: object) -> str | None:
    """A usable string, or None for anything the model didn't really read."""
    if not isinstance(value, str):
        return None
    value = value.strip().strip('"')
    if not value or value.lower() in _NULLISH:
        return None
    return value


def _field(value: str | None) -> Field:
    return Field(value, "VISION", VISION_CONFIDENCE) if value else Field()


def _to_candidate(item: dict) -> ScanCandidate:
    expiry = _clean(item.get("expiry_date"))
    if expiry and not _ISO.fullmatch(expiry):
        # The model was asked for ISO; if it printed a label date anyway, the
        # deterministic parser already knows those formats.
        expiry = normalize_date(expiry)

    upc = _clean(item.get("upc"))
    return ScanCandidate(
        product_name=_field(_clean(item.get("name"))),
        brand=_field(_clean(item.get("brand"))),
        # A misread digit makes a plausible-looking wrong UPC, which would then
        # be queried against openFDA as if it were exact. Check-digit or drop.
        upc=_field(upc if upc and is_valid_upc(upc) else None),
        expiry_date=_field(expiry),
    )


def _demo() -> None:
    items = _parse_items(
        '```json\n[{"name": "Oat Granola", "brand": null, '
        '"expiry_date": "03/15/2027", "upc": "0038000138416"}]\n```'
    )
    assert len(items) == 1, items
    c = _to_candidate(items[0])
    assert c.product_name.value == "Oat Granola"
    assert c.product_name.source == "VISION"
    assert c.brand.value is None
    assert c.expiry_date.value == "2027-03-15"  # normalized through dates.py
    assert c.upc.value == "0038000138416"
    assert _to_candidate({"upc": "0038000138417"}).upc.value is None  # bad check digit
    assert _parse_items("sorry, I can't help with that") == []
    print("vision ok")


if __name__ == "__main__":
    _demo()
