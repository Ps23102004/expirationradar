"""Shared records passed between ExpirationRadar modules and out over the API.

This is the Phase 0 contract: every other module builds against these shapes,
and `docs/API.md` + `tests/fixtures/*.json` are literally `asdict()` of them.
Serialization is stdlib `dataclasses.asdict` — no custom encoders.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

# Where a field's value came from. Deterministic sources outrank the LLM one:
# BARCODE > OCR > VISION, and USER (a confirm-edit correction) outranks all.
PROVENANCE = ("BARCODE", "OCR", "VISION", "USER")

# openFDA enforcement.json statuses we care about; "Ongoing" is the loud one.
RECALL_STATUSES = ("Ongoing", "Completed", "Terminated")


@dataclass
class Field:
    """One extracted value plus where it came from and how sure we are.

    `value` is None when nothing was extracted (e.g. no date found, or the
    vision tier was unavailable) — the UI renders that as "N/A", not an error.
    """

    value: str | None = None
    source: str = "OCR"  # one of PROVENANCE
    confidence: float = 0.0  # 0.0-1.0


@dataclass
class RecallMatch:
    """One openFDA food-enforcement record matched to a scanned/pantry item."""

    recall_number: str
    status: str  # one of RECALL_STATUSES
    classification: str  # "Class I" | "Class II" | "Class III"
    reason: str
    product_description: str
    recalling_firm: str
    report_date: str  # openFDA "YYYYMMDD"
    matched_on: str  # "upc" | "product_terms"
    url: str = ""


@dataclass
class ScanCandidate:
    """One item found in a photo. A multi-barcode photo yields several."""

    product_name: Field = field(default_factory=Field)
    brand: Field = field(default_factory=Field)
    upc: Field = field(default_factory=Field)
    expiry_date: Field = field(default_factory=Field)  # ISO "YYYY-MM-DD"
    recalls: list[RecallMatch] = field(default_factory=list)


@dataclass
class ScanResult:
    """What `POST /api/scan` and `expirationradar scan` return."""

    candidates: list[ScanCandidate] = field(default_factory=list)
    scanned_at: str = ""  # ISO 8601
    vision_available: bool = False
    warnings: list[str] = field(default_factory=list)


@dataclass
class PantryItem:
    """A row of the `pantry_items` table. `id` is None until inserted."""

    product_name: str
    brand: str = ""
    upc: str = ""
    expiry_date: str = ""  # ISO "YYYY-MM-DD"; "" = undated
    quantity: int = 1
    source: str = "USER"  # provenance of the item as a whole
    added_at: str = ""  # ISO 8601
    consumed_at: str | None = None  # None = still active
    notes: str = ""
    id: int | None = None


@dataclass
class WatcherRun:
    """One `expirationradar watch` pass (§4). Idempotent per day."""

    ran_at: str = ""
    items_checked: int = 0
    new_recalls: int = 0
    expiring_soon: int = 0
    expired: int = 0
    error: str = ""  # non-empty = the pass degraded (e.g. network down)
    id: int | None = None


@dataclass
class RecipeSuggestion:
    """One use-it-up idea from the `recipes` cascade."""

    title: str
    uses: list[str] = field(default_factory=list)  # product names it consumes
    steps: str = ""


@dataclass
class Digest:
    """`~/.expirationradar/last_digest.json`, `GET /api/digest`, `digest` CLI —
    all three read this one shape."""

    generated_at: str = ""
    days: int = 5  # the expiring-soon threshold used
    expiring_soon: list[PantryItem] = field(default_factory=list)
    expired: list[PantryItem] = field(default_factory=list)
    new_recalls: list[RecallMatch] = field(default_factory=list)


def to_json_dict(obj: Any) -> Any:
    """Dataclass (or list of them) → plain JSON-ready dict. Stdlib does it."""
    if isinstance(obj, list):
        return [to_json_dict(o) for o in obj]
    return asdict(obj)


def _demo() -> None:
    r = ScanResult(
        candidates=[ScanCandidate(upc=Field("0123456789012", "BARCODE", 1.0))],
        scanned_at="2026-08-20T10:00:00",
    )
    d = to_json_dict(r)
    assert d["candidates"][0]["upc"] == {
        "value": "0123456789012",
        "source": "BARCODE",
        "confidence": 1.0,
    }, d
    assert d["candidates"][0]["expiry_date"]["value"] is None
    assert to_json_dict(PantryItem("Milk"))["consumed_at"] is None
    print("models ok")


if __name__ == "__main__":
    _demo()
