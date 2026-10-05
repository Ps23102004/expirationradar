"""Synthetic image generators shared by test_barcode.py and test_ocr.py.

No real pantry photos exist yet, so tests draw their own with PIL (+
python-barcode for real, scannable barcode art) instead of requiring fixture
photos that don't exist. Keeps the suite standalone and deterministic.
"""

from __future__ import annotations

import io

import barcode as pybarcode
from barcode.writer import ImageWriter
from PIL import Image, ImageDraw, ImageFont

_FONT_CANDIDATES = (
    "/System/Library/Fonts/Supplemental/Arial.ttf",  # macOS
    "DejaVuSans.ttf",  # Linux (fonts-dejavu-core)
)


def _font(size: int):
    for name in _FONT_CANDIDATES:
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            continue
    return ImageFont.load_default(size)


def ean13_png(payload_12_digits: str, rotate: int = 0) -> bytes:
    """A real, pyzbar-decodable EAN-13 barcode image for `payload_12_digits`
    (the check digit is computed by python-barcode)."""
    ean = pybarcode.get("ean13", payload_12_digits, writer=ImageWriter())
    buf = io.BytesIO()
    ean.write(buf, options={"write_text": False})
    buf.seek(0)
    image = Image.open(buf).convert("RGB")
    if rotate:
        image = image.rotate(rotate, expand=True)
    out = io.BytesIO()
    image.save(out, format="PNG")
    return out.getvalue()


def blank_png(size: tuple[int, int] = (60, 60)) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", size, "white").save(buf, format="PNG")
    return buf.getvalue()


def text_png(lines: list[str], size: tuple[int, int] = (600, 300)) -> bytes:
    """A white image with each string in `lines` stamped at its own y-offset,
    like scattered print on packaging (product name, then a date stamp)."""
    image = Image.new("RGB", size, "white")
    draw = ImageDraw.Draw(image)
    font = _font(28)
    y = 20
    for line in lines:
        draw.text((20, y), line, fill="black", font=font)
        y += 60
    buf = io.BytesIO()
    image.save(buf, format="PNG")
    return buf.getvalue()
