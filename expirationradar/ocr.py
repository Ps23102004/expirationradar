"""tesseract (pytesseract, sparse-text PSM) — deterministic text extraction.

Same shape as fairdeal/ocr.py. Always runs: dates aren't in barcodes (§3 step 3).
The `tesseract` binary is expected at /opt/homebrew/bin/tesseract.
"""

from __future__ import annotations

import io

import pytesseract
from PIL import Image, UnidentifiedImageError


class OCRError(Exception):
    """Raised when tesseract is missing or the image can't be read."""


# PSM 11 ("sparse text, no orientation/OSD") over the default PSM 3: a pantry
# label isn't a paragraph, it's scattered fragments at different scales
# (brand logo, ingredients block, and a small stamped "BEST BY 03/15/27" that
# often isn't even aligned with the rest of the print) — PSM 11 pulls out
# whatever text it finds anywhere in the frame instead of assuming one uniform
# block, which is what a stamped date needs.
_PSM_LABEL = 11

# PSM 6 ("a single uniform block of text") — a receipt is dense, line-oriented
# print (one item per line, roughly one column), the opposite of a label's
# scattered fragments. Public: receipt.py passes this explicitly.
PSM_RECEIPT = 6


def extract_text(image_bytes: bytes, psm: int = _PSM_LABEL) -> str:
    """Raw OCR text from the photo. Raises OCRError if tesseract is unusable.

    `psm` defaults to the pantry-label mode (sparse text); pass `PSM_RECEIPT`
    (or any tesseract PSM number) for denser, line-oriented documents.
    """
    try:
        image = Image.open(io.BytesIO(image_bytes))
        image.load()
    except (UnidentifiedImageError, OSError) as exc:
        raise OCRError(f"could not read image: {exc}") from exc

    try:
        text = pytesseract.image_to_string(image, config=f"--psm {psm}")
    except pytesseract.TesseractNotFoundError as exc:
        raise OCRError(f"tesseract binary not found: {exc}") from exc
    except Exception:  # noqa: BLE001 - a bad frame degrades to "", never crashes the scan
        return ""

    return text.strip()


def _demo() -> None:
    try:
        extract_text(b"not an image")
    except OCRError:
        pass
    else:
        raise AssertionError("expected OCRError on garbage bytes")

    buf = io.BytesIO()
    Image.new("RGB", (50, 50), "white").save(buf, format="PNG")
    assert extract_text(buf.getvalue()) == ""  # blank image -> no crash, no text
    print("ocr ok")


if __name__ == "__main__":
    _demo()
