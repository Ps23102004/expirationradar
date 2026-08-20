"""Tests for scan.py — every downstream tier is mocked.

No camera, no tesseract, no network, no Ollama. The load-bearing case is
`test_vision_never_overrides_a_deterministic_field` and friends: docs/API.md
promises USER > BARCODE > OCR > VISION is enforced server-side, and a vision
tier that quietly overwrote a barcode read would put a wrong expiry date in
front of someone deciding whether to eat something.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

import pytest
from llm_ladder.ollama_client import OllamaConnectionError

from expirationradar import scan
from expirationradar.models import Field, RecallMatch, ScanCandidate

IMAGE = b"pretend-jpeg-bytes"

# Grabbed before the autouse fixture stubs it, so the two tests that exercise
# the text cascade for real can still reach it.
REAL_NORMALIZE = scan._normalize_product

GRANOLA_UPC = "0038000138416"
GRANOLA_OFF = {
    "product_name": "Crunchy Oat Granola",
    "brand": "Northvale Foods",
    "upc": GRANOLA_UPC,
}
RECALL = RecallMatch(
    recall_number="F-0455-2026",
    status="Ongoing",
    classification="Class I",
    reason="Undeclared milk allergen.",
    product_description="Crunchy Oat Granola, 12 oz box",
    recalling_firm="Northvale Foods, Inc.",
    report_date="20260805",
    matched_on="upc",
)


@pytest.fixture(autouse=True)
def tiers():
    """Every external tier off by default; each test turns on what it needs.

    Defaults are the fully-degraded machine: no barcode, no text, no recalls,
    Ollama gone. A test that forgets to mock something gets the offline answer,
    never a live call.
    """
    with (
        patch.object(scan.barcode, "decode_barcodes", return_value=[]) as barcodes,
        patch.object(scan.openfoodfacts, "lookup", return_value=None) as off,
        patch.object(scan.ocr, "extract_text", return_value="") as ocr_text,
        patch.object(scan.openfda, "search_by_upc", return_value=[]) as by_upc,
        patch.object(scan.openfda, "search_by_terms", return_value=[]) as by_terms,
        patch.object(scan.vision, "extract", return_value=None) as vision_extract,
        patch.object(scan.vision, "is_available", return_value=False) as vision_up,
        patch.object(scan, "_normalize_product", return_value=None) as normalize,
    ):
        yield {
            "barcodes": barcodes,
            "off": off,
            "ocr": ocr_text,
            "by_upc": by_upc,
            "by_terms": by_terms,
            "vision": vision_extract,
            "vision_up": vision_up,
            "normalize": normalize,
        }


def _vision_candidate(name=None, brand=None, upc=None, expiry=None) -> ScanCandidate:
    f = lambda v: Field(v, "VISION", 0.4) if v else Field()  # noqa: E731
    return ScanCandidate(
        product_name=f(name), brand=f(brand), upc=f(upc), expiry_date=f(expiry)
    )


# --- the barcode-only path: everything resolves, vision is never called ------


def test_barcode_path_needs_no_vision(tiers):
    tiers["barcodes"].return_value = [GRANOLA_UPC]
    tiers["off"].return_value = GRANOLA_OFF
    tiers["ocr"].return_value = "Crunchy Oat Granola BEST BY 03/15/2027"
    tiers["vision_up"].return_value = True

    result = scan.run_scan(IMAGE)

    assert len(result.candidates) == 1
    c = result.candidates[0]
    assert (c.upc.value, c.upc.source, c.upc.confidence) == (GRANOLA_UPC, "BARCODE", 1.0)
    assert (c.product_name.value, c.product_name.source) == (
        "Crunchy Oat Granola",
        "BARCODE",
    )
    assert (c.brand.value, c.brand.source) == ("Northvale Foods", "BARCODE")
    assert (c.expiry_date.value, c.expiry_date.source) == ("2027-03-15", "OCR")

    tiers["vision"].assert_not_called()  # nothing was missing
    assert result.vision_available is True  # ...but the tier is up, and we say so
    assert result.warnings == []
    assert result.scanned_at


def test_multiple_barcodes_yield_multiple_candidates(tiers):
    other = "5000112637922"
    tiers["barcodes"].return_value = [GRANOLA_UPC, other]
    tiers["off"].side_effect = lambda upc: GRANOLA_OFF if upc == GRANOLA_UPC else None
    tiers["ocr"].return_value = "BEST BY 03/15/2027 and USE BY 08/24/2026"

    result = scan.run_scan(IMAGE)

    assert [c.upc.value for c in result.candidates] == [GRANOLA_UPC, other]
    assert any("not found in Open Food Facts" in w for w in result.warnings)
    assert any("reading order, not position" in w for w in result.warnings)


def test_recalls_are_attached_by_upc_first(tiers):
    tiers["barcodes"].return_value = [GRANOLA_UPC]
    tiers["off"].return_value = GRANOLA_OFF
    tiers["by_upc"].return_value = [RECALL]

    result = scan.run_scan(IMAGE)

    assert result.candidates[0].recalls == [RECALL]
    tiers["by_terms"].assert_not_called()  # the exact match already answered


def test_term_search_is_the_fallback_when_the_upc_finds_nothing(tiers):
    tiers["barcodes"].return_value = [GRANOLA_UPC]
    tiers["off"].return_value = GRANOLA_OFF

    scan.run_scan(IMAGE)

    tiers["by_upc"].assert_called_once_with(GRANOLA_UPC)
    tiers["by_terms"].assert_called_once_with("Crunchy Oat Granola", "Northvale Foods")


def test_openfda_failure_degrades_to_no_recalls_plus_one_warning(tiers):
    tiers["barcodes"].return_value = [GRANOLA_UPC, "5000112637922"]
    tiers["by_upc"].side_effect = scan.openfda.OpenFDAError("could not reach openFDA")

    result = scan.run_scan(IMAGE)

    assert all(c.recalls == [] for c in result.candidates)
    assert sum("recall check unavailable" in w for w in result.warnings) == 1


# --- the OCR + vision-fallback path -----------------------------------------


def test_ocr_only_item_falls_back_to_vision_for_the_name(tiers):
    tiers["ocr"].return_value = "BEST BY 03/15/2027"
    tiers["vision"].return_value = [_vision_candidate(name="Whole Milk, 1 gal")]

    result = scan.run_scan(IMAGE)

    c = result.candidates[0]
    assert (c.expiry_date.value, c.expiry_date.source) == ("2027-03-15", "OCR")
    assert (c.product_name.value, c.product_name.source) == ("Whole Milk, 1 gal", "VISION")
    assert result.vision_available is True
    tiers["vision"].assert_called_once_with(IMAGE)


def test_vision_fills_a_missing_date_on_a_barcoded_item(tiers):
    tiers["barcodes"].return_value = [GRANOLA_UPC]
    tiers["off"].return_value = GRANOLA_OFF
    tiers["ocr"].return_value = "Crunchy Oat Granola Net Wt 12oz"  # no date printed
    tiers["vision"].return_value = [_vision_candidate(expiry="2027-03-15")]

    c = scan.run_scan(IMAGE).candidates[0]

    assert (c.expiry_date.value, c.expiry_date.source) == ("2027-03-15", "VISION")
    assert c.product_name.source == "BARCODE"


def test_vision_extras_become_their_own_candidates_on_a_shelf_photo(tiers):
    tiers["ocr"].return_value = "a blur of shelf text"
    tiers["vision"].return_value = [
        _vision_candidate(name="Crunchy Oat Granola", expiry="2027-03-15"),
        _vision_candidate(name="Whole Milk 1 gal", expiry="2026-08-24"),
    ]

    result = scan.run_scan(IMAGE)

    assert [c.product_name.value for c in result.candidates] == [
        "Crunchy Oat Granola",
        "Whole Milk 1 gal",
    ]


def test_vision_matches_by_upc_not_by_order_when_it_can(tiers):
    other = "5000112637922"
    tiers["barcodes"].return_value = [GRANOLA_UPC, other]
    tiers["ocr"].return_value = "some text"
    # Vision reports them in the opposite order — the UPC must decide, not the index.
    tiers["vision"].return_value = [
        _vision_candidate(name="Sparkling Water", upc=other),
        _vision_candidate(name="Crunchy Oat Granola", upc=GRANOLA_UPC),
    ]

    result = scan.run_scan(IMAGE)

    by_upc = {c.upc.value: c.product_name.value for c in result.candidates}
    assert by_upc == {GRANOLA_UPC: "Crunchy Oat Granola", other: "Sparkling Water"}


def test_ocr_name_guess_is_labeled_ocr_not_vision(tiers):
    tiers["ocr"].return_value = "CRUNCHY 0AT GRAN0LA  net wt 12 oz"
    tiers["normalize"].return_value = "Crunchy Oat Granola"

    c = scan.run_scan(IMAGE).candidates[0]

    assert (c.product_name.value, c.product_name.source) == ("Crunchy Oat Granola", "OCR")
    assert c.product_name.confidence < 0.9  # a guess, not an exact read


# --- the safety property: VISION never overwrites a deterministic value ------


def test_fill_keeps_the_deterministic_value():
    determined = Field("0038000138416", "BARCODE", 1.0)
    guessed = Field("0038000138999", "VISION", 0.4)
    assert scan._fill(determined, guessed) is determined
    assert scan._fill(Field(), guessed) is guessed


@pytest.mark.parametrize(
    "field_name, deterministic",
    [
        ("product_name", Field("Crunchy Oat Granola", "BARCODE", 0.9)),
        ("brand", Field("Northvale Foods", "BARCODE", 0.9)),
        ("upc", Field(GRANOLA_UPC, "BARCODE", 1.0)),
        ("expiry_date", Field("2027-03-15", "OCR", 0.85)),
    ],
)
def test_vision_never_overrides_a_deterministic_field(tiers, field_name, deterministic):
    """The correctness-critical rule from docs/API.md, checked field by field.

    The candidate arrives with one deterministic field already set; vision
    disagrees about every single field. The deterministic value must survive
    untouched — value, source and confidence.
    """
    tiers["ocr"].return_value = "some text so the pipeline runs"
    tiers["vision"].return_value = [
        _vision_candidate(
            name="WRONG NAME",
            brand="WRONG BRAND",
            upc="5000112637922",
            expiry="2029-01-01",
        )
    ]

    candidate = ScanCandidate()
    setattr(candidate, field_name, deterministic)
    with patch.object(scan, "_barcode_candidates", return_value=[candidate]):
        result = scan.run_scan(IMAGE)

    survivor = getattr(result.candidates[0], field_name)
    assert survivor.source != "VISION"
    assert (survivor.value, survivor.source, survivor.confidence) == (
        deterministic.value,
        deterministic.source,
        deterministic.confidence,
    )


def test_a_hole_next_to_a_deterministic_field_still_gets_filled(tiers):
    tiers["barcodes"].return_value = [GRANOLA_UPC]
    tiers["off"].return_value = GRANOLA_OFF
    tiers["ocr"].return_value = "text"
    tiers["vision"].return_value = [
        _vision_candidate(name="WRONG NAME", brand="WRONG BRAND", expiry="2027-03-15")
    ]

    c = scan.run_scan(IMAGE).candidates[0]

    assert c.product_name.value == "Crunchy Oat Granola"  # kept
    assert c.brand.value == "Northvale Foods"  # kept
    assert c.expiry_date.value == "2027-03-15"  # filled
    assert c.expiry_date.source == "VISION"


# --- degradation: Ollama completely off -------------------------------------


def test_ollama_off_still_returns_a_full_scan_result(tiers):
    """The whole deterministic pipeline must survive a dead vision tier."""
    tiers["barcodes"].return_value = [GRANOLA_UPC]
    tiers["ocr"].return_value = "BEST BY 03/15/2027"  # date reads, name doesn't
    tiers["by_upc"].return_value = [RECALL]
    tiers["vision"].return_value = None  # the "vision unavailable" sentinel

    result = scan.run_scan(IMAGE)

    c = result.candidates[0]
    assert (c.upc.value, c.upc.source) == (GRANOLA_UPC, "BARCODE")
    assert (c.expiry_date.value, c.expiry_date.source) == ("2027-03-15", "OCR")
    assert c.recalls == [RECALL]  # recalls still cross-referenced
    assert c.product_name.value is None  # the hole vision would have filled
    assert result.vision_available is False
    assert any(scan.vision.UNAVAILABLE_NOTE in w for w in result.warnings)


def test_ollama_off_leaves_unfillable_fields_null_without_raising(tiers):
    tiers["ocr"].return_value = "BEST BY 03/15/2027"
    tiers["vision"].return_value = None

    c = scan.run_scan(IMAGE).candidates[0]

    assert c.expiry_date.value == "2027-03-15"
    assert c.product_name.value is None  # renders as N/A, not an error
    assert c.brand.value is None


def test_nothing_recognized_is_an_empty_candidate_list_not_an_error(tiers):
    result = scan.run_scan(IMAGE)

    assert result.candidates == []
    assert result.scanned_at
    assert result.vision_available is False


def test_unreadable_image_degrades_instead_of_raising(tiers):
    tiers["barcodes"].side_effect = OSError("cannot identify image file")
    tiers["ocr"].side_effect = scan.ocr.OCRError("could not read image")

    result = scan.run_scan(IMAGE)

    assert result.candidates == []
    assert any("barcode scan failed" in w for w in result.warnings)
    assert any("text extraction unavailable" in w for w in result.warnings)


def test_normalize_product_returns_none_when_ollama_is_off():
    """The text cascade degrades the same way the vision one does.

    Calls the real function (the `tiers` fixture stubs it for every other test).
    """
    with patch.object(scan, "run_cascade", side_effect=OllamaConnectionError("off")):
        assert REAL_NORMALIZE("CRUNCHY 0AT GRAN0LA") is None


def test_normalize_product_takes_only_the_first_line_of_a_chatty_reply():
    reply = SimpleNamespace(answer='  "Crunchy Oat Granola"\nHope that helps!')
    with patch.object(scan, "run_cascade", return_value=reply):
        assert REAL_NORMALIZE("CRUNCHY 0AT GRAN0LA") == "Crunchy Oat Granola"

    with patch.object(scan, "run_cascade", return_value=SimpleNamespace(answer="NONE")):
        assert REAL_NORMALIZE("illegible") is None
