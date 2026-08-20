"""ocr.py: tesseract extraction — deterministic, degrades to "" per-region,
only raises OCRError when the image itself can't be read."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent / "fixtures"))
from synth_images import blank_png, text_png  # noqa: E402

import pytest

from expirationradar.ocr import OCRError, extract_text  # noqa: E402


def test_extract_text_reads_stamped_text():
    text = extract_text(text_png(["CRUNCHY OAT GRANOLA", "BEST BY 03/15/2027"]))
    assert "CRUNCHY OAT GRANOLA" in text
    assert "BEST BY 03/15/2027" in text


def test_extract_text_reads_scattered_lines():
    # Sparse-text PSM should pick up fragments regardless of layout, not just
    # a single uniform block.
    text = extract_text(text_png(["WHOLE MILK", "EXP 08/24/26", "1 GAL"]))
    assert "WHOLE MILK" in text
    assert "EXP" in text
    assert "1 GAL" in text


def test_extract_text_blank_image_returns_empty_string_not_error():
    assert extract_text(blank_png()) == ""


def test_extract_text_garbage_bytes_raises_ocr_error():
    with pytest.raises(OCRError):
        extract_text(b"this is not an image, it's plain bytes")


def test_extract_text_empty_bytes_raises_ocr_error():
    with pytest.raises(OCRError):
        extract_text(b"")


def test_extract_text_is_deterministic():
    image = text_png(["SAME LABEL EVERY TIME"])
    assert extract_text(image) == extract_text(image)
