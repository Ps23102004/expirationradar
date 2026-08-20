"""pyzbar decode (+90° rotations), UPC-A/EAN-13 check-digit validation.

Deterministic, most reliable signal in the scan flow (§3 step 1).
Multiple barcodes in one photo = multiple item candidates.
"""

from __future__ import annotations

import io

from PIL import Image
from pyzbar.pyzbar import decode as zbar_decode


def decode_barcodes(image_bytes: bytes) -> list[str]:
    """Every barcode payload found in the image, trying 0/90/180/270 rotations.
    De-duplicated, check-digit-validated, in first-seen order."""
    image = Image.open(io.BytesIO(image_bytes))
    image.load()

    found: list[str] = []
    seen: set[str] = set()

    # Straight-on pass first; a pantry photo is usually shot upright, so this
    # is the common case and the only rotation most calls ever pay for.
    # Only try the sideways/upside-down rotations if that pass found nothing.
    for angle in (0, 90, 180, 270):
        if angle != 0 and found:
            break
        rotated = image if angle == 0 else image.rotate(angle, expand=True)
        for symbol in zbar_decode(rotated):
            try:
                payload = symbol.data.decode("utf-8")
            except UnicodeDecodeError:
                continue
            if payload in seen or not is_valid_upc(payload):
                continue
            seen.add(payload)
            found.append(payload)

    return found


def is_valid_upc(code: str) -> bool:
    """UPC-A (12) / EAN-13 (13) mod-10 check digit.

    Same GS1 algorithm covers both lengths: weight the digits 3/1 alternating
    from the one immediately left of the check digit.
    """
    if not code.isdigit() or len(code) not in (12, 13):
        return False
    digits = [int(c) for c in code]
    check_digit, body = digits[-1], digits[:-1]
    total = sum(d * (3 if i % 2 == 0 else 1) for i, d in enumerate(reversed(body)))
    return (10 - total % 10) % 10 == check_digit


def _demo() -> None:
    assert is_valid_upc("0038000138416") is True  # docs/API.md's recall fixture UPC
    assert is_valid_upc("0038000138417") is False  # wrong check digit
    assert is_valid_upc("not-a-upc") is False
    assert is_valid_upc("12345") is False  # wrong length
    assert decode_barcodes(_blank_png()) == []
    print("barcode ok")


def _blank_png() -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (50, 50), "white").save(buf, format="PNG")
    return buf.getvalue()


if __name__ == "__main__":
    _demo()
