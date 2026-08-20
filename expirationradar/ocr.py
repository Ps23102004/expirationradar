"""tesseract (pytesseract, sparse-text PSM) — deterministic text extraction.

Same shape as fairdeal/ocr.py. Always runs: dates aren't in barcodes (§3 step 3).
The `tesseract` binary is expected at /opt/homebrew/bin/tesseract.
"""

from __future__ import annotations


class OCRError(Exception):
    """Raised when tesseract is missing or the image can't be read."""


def extract_text(image_bytes: bytes) -> str:
    """Raw OCR text from the photo. Raises OCRError if tesseract is unusable."""
    raise NotImplementedError("Phase 1 — see plan §3")
