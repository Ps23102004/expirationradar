"""pyzbar decode (+90° rotations), UPC-A/EAN-13 check-digit validation.

Deterministic, most reliable signal in the scan flow (§3 step 1).
Multiple barcodes in one photo = multiple item candidates.
"""

from __future__ import annotations


def decode_barcodes(image_bytes: bytes) -> list[str]:
    """Every barcode payload found in the image, trying 0/90/180/270 rotations.
    De-duplicated, check-digit-validated, in first-seen order."""
    raise NotImplementedError("Phase 1 — see plan §3")


def is_valid_upc(code: str) -> bool:
    """UPC-A (12) / EAN-13 (13) mod-10 check digit."""
    raise NotImplementedError("Phase 1 — see plan §3")
