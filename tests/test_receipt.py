"""Tests for receipt.py — every external tier is mocked, mirroring
test_scan.py's approach. No camera, no tesseract, no Ollama.

Load-bearing cases: the estimate is always ESTIMATED-sourced with a low
confidence, no recall check ever runs for a receipt item, and the pipeline
still returns *something* usable with Ollama completely off.
"""

from __future__ import annotations

from datetime import date, timedelta
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from llm_ladder.config import ChainConfig, TierConfig
from llm_ladder.ollama_client import OllamaConnectionError

from expirationradar import receipt
from expirationradar.models import Field

RECEIPT_TEXT = (
    "GROCERY MART\n"
    "123 MAIN ST\n"
    "08/13/2026\n"
    "WHOLE MILK 1GAL      3.99\n"
    "CHEDDAR CHEESE       4.50\n"
    "SUBTOTAL             8.49\n"
    "TAX                  0.70\n"
    "TOTAL                9.19\n"
    "VISA ****1234\n"
    "THANK YOU\n"
)

_CHAIN = ChainConfig(name="parse_receipt", tiers=[TierConfig("gemma4:e4b-mlx", 1, 0.5)])


@pytest.fixture(autouse=True)
def tiers():
    """Everything off by default: no OCR text, no chain, no vision. Each test
    turns on what it needs — a test that forgets to mock something gets the
    fully-degraded machine, never a live call."""
    with (
        patch.object(receipt.ocr, "extract_text", return_value="") as ocr_text,
        patch.object(receipt, "load_chain", return_value=None) as load_chain,
        patch.object(receipt, "run_cascade") as run_cascade,
        patch.object(receipt.vision, "extract_receipt_items", return_value=None) as vision_items,
        patch.object(receipt.vision, "is_available", return_value=False) as vision_up,
    ):
        yield {
            "ocr": ocr_text,
            "load_chain": load_chain,
            "run_cascade": run_cascade,
            "vision": vision_items,
            "vision_up": vision_up,
        }


def _cascade_answer(json_text: str) -> SimpleNamespace:
    return SimpleNamespace(answer=json_text)


# --- OCR -> text chain happy path -------------------------------------------


def test_ocr_uses_dense_line_oriented_psm(tiers):
    tiers["ocr"].return_value = RECEIPT_TEXT
    receipt.parse_receipt(b"fake-receipt-jpeg")
    assert tiers["ocr"].call_args.kwargs.get("psm") == receipt.ocr.PSM_RECEIPT


def test_text_chain_happy_path_builds_estimated_candidates(tiers):
    tiers["ocr"].return_value = RECEIPT_TEXT
    tiers["load_chain"].return_value = _CHAIN
    tiers["run_cascade"].return_value = _cascade_answer(
        '[{"name": "Whole Milk 1 Gal", "category_guess": "dairy"}, '
        '{"name": "Cheddar Cheese", "category_guess": "dairy"}]'
    )

    result = receipt.parse_receipt(b"fake-receipt-jpeg")

    assert len(result.candidates) == 2
    milk, cheese = result.candidates
    assert milk.product_name.value == "Whole Milk 1 Gal"
    assert milk.expiry_date.source == "ESTIMATED"
    assert 0.0 < milk.expiry_date.confidence < 0.5  # always a low-trust guess
    assert cheese.product_name.value == "Cheddar Cheese"
    # purchase date "08/13/2026" parsed from the receipt text + 7 days (milk bucket)
    assert milk.expiry_date.value == "2026-08-20"
    # cheddar is named explicitly -> hard cheese bucket (120 days), not the
    # generic soft-cheese default despite category_guess="dairy"
    assert cheese.expiry_date.value == "2026-12-11"


def test_no_barcode_or_brand_fields_on_receipt_items(tiers):
    tiers["ocr"].return_value = RECEIPT_TEXT
    tiers["load_chain"].return_value = _CHAIN
    tiers["run_cascade"].return_value = _cascade_answer('[{"name": "Whole Milk", "category_guess": "dairy"}]')

    candidate = receipt.parse_receipt(b"x").candidates[0]
    assert candidate.upc.value is None
    assert candidate.brand.value is None


# --- vision fallback on garbled/empty OCR -----------------------------------


def test_vision_fallback_when_ocr_text_is_empty(tiers):
    tiers["ocr"].return_value = ""  # garbled photo, tesseract found nothing
    tiers["vision"].return_value = [{"name": "Whole Milk", "category_guess": "dairy"}]

    result = receipt.parse_receipt(b"x")

    assert len(result.candidates) == 1
    assert result.candidates[0].product_name.value == "Whole Milk"
    tiers["run_cascade"].assert_not_called()  # no text to feed the chain


def test_vision_fallback_when_text_chain_yields_nothing(tiers):
    tiers["ocr"].return_value = RECEIPT_TEXT
    tiers["load_chain"].return_value = _CHAIN
    tiers["run_cascade"].return_value = _cascade_answer("[]")  # chain ran, parsed nothing useful
    tiers["vision"].return_value = [{"name": "Cheddar Cheese", "category_guess": "dairy"}]

    result = receipt.parse_receipt(b"x")

    assert len(result.candidates) == 1
    assert result.candidates[0].product_name.value == "Cheddar Cheese"


def test_vision_unavailable_leaves_warning_and_falls_through(tiers):
    tiers["ocr"].return_value = ""
    tiers["vision"].return_value = None  # Ollama off / model not pulled

    result = receipt.parse_receipt(b"x")

    assert result.candidates == []
    assert any("vision" in w.lower() for w in result.warnings)


# --- purchase date: found vs defaulted to today -----------------------------


def test_purchase_date_found_in_receipt_text(tiers):
    tiers["ocr"].return_value = RECEIPT_TEXT
    tiers["load_chain"].return_value = _CHAIN
    tiers["run_cascade"].return_value = _cascade_answer('[{"name": "Whole Milk", "category_guess": "dairy"}]')

    candidate = receipt.parse_receipt(b"x").candidates[0]
    assert candidate.expiry_date.value == "2026-08-20"  # 08/13/2026 + 7 days


def test_purchase_date_defaults_to_today_when_none_found(tiers):
    tiers["ocr"].return_value = "SOME STORE\nWHOLE MILK 1GAL   3.99\nTOTAL  3.99\n"
    tiers["load_chain"].return_value = _CHAIN
    tiers["run_cascade"].return_value = _cascade_answer('[{"name": "Whole Milk", "category_guess": "dairy"}]')

    candidate = receipt.parse_receipt(b"x").candidates[0]
    expected = (date.today() + timedelta(days=7)).isoformat()
    assert candidate.expiry_date.value == expected


# --- no recall check for receipt items --------------------------------------


def test_no_recall_check_ever_runs(tiers):
    tiers["ocr"].return_value = RECEIPT_TEXT
    tiers["load_chain"].return_value = _CHAIN
    tiers["run_cascade"].return_value = _cascade_answer(
        '[{"name": "Whole Milk", "category_guess": "dairy"}, '
        '{"name": "Cheddar Cheese", "category_guess": "dairy"}]'
    )

    result = receipt.parse_receipt(b"x")

    assert all(c.recalls == [] for c in result.candidates)
    assert any("recall" in w.lower() for w in result.warnings)


def test_estimate_disclosure_warning_always_present(tiers):
    result = receipt.parse_receipt(b"x")
    assert any("estimated" in w.lower() for w in result.warnings)


# --- Ollama completely off: still returns something usable ------------------


def test_ollama_completely_off_still_returns_usable_items(tiers):
    """No text chain configured, vision unavailable -> raw OCR lines with a
    default shelf-life estimate, not an empty/failed result."""
    tiers["ocr"].return_value = RECEIPT_TEXT
    tiers["load_chain"].return_value = None
    tiers["vision"].return_value = None

    result = receipt.parse_receipt(b"x")

    assert len(result.candidates) >= 1
    names = [c.product_name.value for c in result.candidates]
    assert any("MILK" in (n or "") for n in names)
    # boilerplate lines never become fake candidates
    assert not any("TOTAL" in (n or "") or "VISA" in (n or "") for n in names)
    for c in result.candidates:
        assert c.expiry_date.source == "ESTIMATED"
        assert c.expiry_date.value is not None
    assert any("ollama" in w.lower() for w in result.warnings)


def test_never_raises_when_everything_is_off(tiers):
    result = receipt.parse_receipt(b"totally-unreadable-bytes")
    assert result.candidates == []  # nothing to work with, but no exception
    assert result.warnings


def test_run_cascade_error_degrades_instead_of_raising(tiers):
    tiers["ocr"].return_value = RECEIPT_TEXT
    tiers["load_chain"].return_value = _CHAIN
    tiers["run_cascade"].side_effect = OllamaConnectionError("Ollama down")

    result = receipt.parse_receipt(b"x")  # must not raise
    assert isinstance(result.candidates, list)
