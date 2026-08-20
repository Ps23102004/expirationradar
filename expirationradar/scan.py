"""Orchestrates the core scan loop (§3).

barcode → Open Food Facts → OCR + dates → (vision fallback) → openFDA recalls
→ ScanResult. Deterministic reads always win; the vision tier only fills holes.

`run_scan` is the single entry point: `POST /api/scan` and the `scan` CLI
command both call it and nothing else here.

# ponytail: v1 does no bbox-geometry matching — a multi-item shelf photo yields
# N loosely-associated candidates and the confirm-edit UI does final
# association. Upgrade path: match barcode/date boxes by pixel proximity.
"""

from __future__ import annotations

from datetime import datetime

from llm_ladder.engine import run_cascade
from llm_ladder.ollama_client import OllamaConnectionError

from expirationradar import barcode, dates, ocr, openfda, openfoodfacts, vision
from expirationradar.models import Field, ScanCandidate, ScanResult
from expirationradar.vision import has_value, load_chain

_OFF_CONFIDENCE = 0.9  # Open Food Facts resolved it from an exact UPC
_NAME_GUESS_CONFIDENCE = 0.4  # a text model tidying OCR noise into a name
_OCR_TEXT_LIMIT = 1500  # a label's worth of text; keeps the local prompt small


def run_scan(image_bytes: bytes) -> ScanResult:
    """The whole §3 pipeline. Never raises for a missing tier — degradations
    land in ScanResult.warnings and N/A fields."""
    warnings: list[str] = []

    candidates = _barcode_candidates(image_bytes, warnings)
    text = _ocr_text(image_bytes, warnings)
    _attach_dates(candidates, dates.parse_dates(text) if text else [], warnings)
    _guess_name(candidates, text)

    vision_available = _apply_vision(candidates, image_bytes, warnings)
    candidates = [c for c in candidates if has_value(c)]
    _attach_recalls(candidates, warnings)

    return ScanResult(
        candidates=candidates,
        scanned_at=datetime.now().isoformat(timespec="seconds"),
        vision_available=vision_available,
        warnings=warnings,
    )


def _fill(existing: Field, incoming: Field) -> Field:
    """Vision only ever fills a hole.

    CORRECTNESS-CRITICAL (docs/API.md "Precedence, enforced server-side"): a
    deterministic BARCODE/OCR read is never overwritten by the model tier. Every
    vision merge goes through this one function.
    """
    return existing if existing.value is not None else incoming


def _barcode_candidates(image_bytes: bytes, warnings: list[str]) -> list[ScanCandidate]:
    """Step 1-2: every valid UPC in the photo, named via Open Food Facts."""
    try:
        upcs = barcode.decode_barcodes(image_bytes)
    except Exception as exc:  # noqa: BLE001 - unreadable frame degrades, never raises
        warnings.append(f"barcode scan failed: {exc}")
        return []

    found = []
    for upc in upcs:
        candidate = ScanCandidate(upc=Field(upc, "BARCODE", 1.0))
        product = openfoodfacts.lookup(upc)  # never raises; None = unresolved
        if product:
            if product.get("product_name"):
                candidate.product_name = Field(
                    product["product_name"], "BARCODE", _OFF_CONFIDENCE
                )
            if product.get("brand"):
                candidate.brand = Field(product["brand"], "BARCODE", _OFF_CONFIDENCE)
        else:
            warnings.append(f"UPC {upc} not found in Open Food Facts")
        found.append(candidate)

    if not found:
        # An un-barcoded or stamped-date-only item still gets one candidate for
        # OCR and the vision tier to fill; it's dropped later if it stays empty.
        found.append(ScanCandidate())
    return found


def _ocr_text(image_bytes: bytes, warnings: list[str]) -> str:
    """Step 3: tesseract always runs — dates aren't in barcodes."""
    try:
        return ocr.extract_text(image_bytes)
    except ocr.OCRError as exc:
        warnings.append(f"text extraction unavailable: {exc}")
        return ""


def _attach_dates(
    candidates: list[ScanCandidate], date_fields: list[Field], warnings: list[str]
) -> None:
    """Best date to the item when there's one item; otherwise pair by order."""
    if not date_fields:
        return
    if len(candidates) == 1:
        candidates[0].expiry_date = date_fields[0]
        return
    for candidate, found in zip(candidates, date_fields):
        candidate.expiry_date = found
    warnings.append(
        f"{len(candidates)} items in one photo: dates were matched to items by "
        "reading order, not position — check them before saving"
    )


def _guess_name(candidates: list[ScanCandidate], text: str) -> None:
    """Step 3's tail: clean an OCR blob into a product name.

    Only when exactly one candidate is nameless — the OCR text is one
    undivided blob, so with several nameless items it would hand them all the
    same name. That case is the vision tier's job.
    """
    nameless = [c for c in candidates if c.product_name.value is None]
    if len(nameless) != 1 or not text.strip():
        return
    guess = _normalize_product(text)
    if guess:
        nameless[0].product_name = Field(guess, "OCR", _NAME_GUESS_CONFIDENCE)


def _normalize_product(text: str) -> str | None:
    """`normalize_product` cascade over OCR text. None if Ollama is off."""
    chain = load_chain("normalize_product")
    if not chain or not chain.tiers:
        return None

    prompt = (
        "Below is raw OCR text from a photo of one grocery product's packaging.\n"
        "Reply with ONLY the product name as printed on the package — no "
        "explanation, no quotes, no extra words. If you cannot tell, reply NONE.\n\n"
        f"OCR TEXT:\n{text[:_OCR_TEXT_LIMIT]}"
    )
    try:
        result = run_cascade(prompt, "normalize_product", chain)
    except (OllamaConnectionError, ValueError, RuntimeError):
        return None

    lines = [line.strip().strip('"') for line in result.answer.splitlines()]
    lines = [line for line in lines if line]
    if not lines or lines[0].upper().startswith("NONE"):
        return None
    return lines[0][:80]


def _needs_vision(candidates: list[ScanCandidate]) -> bool:
    """Step 4's trigger: some candidate still lacks a name or a date."""
    return any(
        c.product_name.value is None or c.expiry_date.value is None for c in candidates
    )


def _apply_vision(
    candidates: list[ScanCandidate], image_bytes: bytes, warnings: list[str]
) -> bool:
    if not _needs_vision(candidates):
        # Nothing to fill. Report the tier's state honestly anyway — /api/tags
        # is a loopback GET, not a model load.
        return vision.is_available()

    found = vision.extract(image_bytes)
    if found is None:
        warnings.append(
            f"vision fallback: {vision.UNAVAILABLE_NOTE} — Ollama is off or the "
            "vision model isn't pulled; barcode, OCR and recalls still ran"
        )
        return False

    _merge_vision(candidates, found)
    return True


def _merge_vision(candidates: list[ScanCandidate], found: list[ScanCandidate]) -> None:
    """Fill holes from vision: by UPC where possible, else by order, and append
    whatever's left over (the multi-item shelf case)."""
    by_upc = {c.upc.value: c for c in candidates if c.upc.value}
    leftovers: list[ScanCandidate] = []
    for item in found:
        target = by_upc.get(item.upc.value) if item.upc.value else None
        if target is None:
            leftovers.append(item)
        else:
            _merge_into(target, item)

    holes = [c for c in candidates if _needs_vision([c])]
    for candidate, item in zip(holes, leftovers):
        _merge_into(candidate, item)
    candidates.extend(leftovers[len(holes) :])


def _merge_into(candidate: ScanCandidate, item: ScanCandidate) -> None:
    candidate.product_name = _fill(candidate.product_name, item.product_name)
    candidate.brand = _fill(candidate.brand, item.brand)
    candidate.upc = _fill(candidate.upc, item.upc)
    candidate.expiry_date = _fill(candidate.expiry_date, item.expiry_date)


def _attach_recalls(candidates: list[ScanCandidate], warnings: list[str]) -> None:
    """Step 5: exact UPC match first, fuzzy product/brand terms as fallback.

    openFDA's HTTP 404 already means "no recall" inside openfda.py — only a
    genuine transport/API failure lands here, and it degrades to `recalls: []`
    plus one warning rather than N identical ones.
    """
    for candidate in candidates:
        try:
            matches = (
                openfda.search_by_upc(candidate.upc.value) if candidate.upc.value else []
            )
            if not matches and (candidate.product_name.value or candidate.brand.value):
                matches = openfda.search_by_terms(
                    candidate.product_name.value or "", candidate.brand.value or ""
                )
        except openfda.OpenFDAError as exc:
            warnings.append(f"recall check unavailable: {exc}")
            return
        candidate.recalls = matches


def _demo() -> None:
    """Offline self-check of the one rule that must never break."""
    determined = ScanCandidate(
        product_name=Field("Crunchy Oat Granola", "BARCODE", 0.9),
        expiry_date=Field(None),
    )
    guessed = ScanCandidate(
        product_name=Field("Granola Box", "VISION", 0.4),
        expiry_date=Field("2027-03-15", "VISION", 0.4),
    )
    _merge_into(determined, guessed)
    assert determined.product_name.value == "Crunchy Oat Granola"
    assert determined.product_name.source == "BARCODE"  # vision did NOT override
    assert determined.expiry_date.value == "2027-03-15"
    assert determined.expiry_date.source == "VISION"  # but it did fill the hole
    print("scan ok")


if __name__ == "__main__":
    _demo()
