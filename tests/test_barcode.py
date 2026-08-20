"""barcode.py: pyzbar decode (+rotations) and UPC-A/EAN-13 check-digit math."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent / "fixtures"))
from synth_images import blank_png, ean13_png  # noqa: E402

from expirationradar.barcode import decode_barcodes, is_valid_upc  # noqa: E402

# From docs/API.md's own RecallMatch/ScanCandidate example — a real, valid EAN-13.
VALID_EAN13 = "0038000138416"
VALID_UPCA = "036000291452"  # a real, well-known UPC-A (Kellogg's Corn Flakes)


def test_is_valid_upc_accepts_real_codes():
    assert is_valid_upc(VALID_EAN13) is True
    assert is_valid_upc(VALID_UPCA) is True


def test_is_valid_upc_rejects_bad_check_digit():
    assert is_valid_upc(VALID_EAN13[:-1] + "0") is False
    assert is_valid_upc(VALID_UPCA[:-1] + "9") is False


def test_is_valid_upc_rejects_wrong_length_or_non_digits():
    assert is_valid_upc("12345") is False
    assert is_valid_upc("") is False
    assert is_valid_upc("003800013841X") is False
    assert is_valid_upc("00380001384166") is False  # 14 digits


def test_decode_barcodes_reads_a_real_barcode_upright():
    assert decode_barcodes(ean13_png("003800013841")) == [VALID_EAN13]


def test_decode_barcodes_falls_back_to_rotation():
    for angle in (90, 180, 270):
        assert decode_barcodes(ean13_png("003800013841", rotate=angle)) == [VALID_EAN13]


def test_decode_barcodes_no_barcode_present_returns_empty():
    assert decode_barcodes(blank_png()) == []


def test_decode_barcodes_dedupes_first_seen_order():
    # Straight-on decode succeeds, so the rotation passes never even run;
    # a single real barcode should never produce duplicate entries.
    result = decode_barcodes(ean13_png("003800013841"))
    assert result == list(dict.fromkeys(result))
