"""Orchestrates the core scan loop (§3).

barcode → Open Food Facts → OCR + dates → (vision fallback) → openFDA recalls
→ ScanResult. Deterministic reads always win; the vision tier only fills holes.

# ponytail: v1 does no bbox-geometry matching — a multi-item shelf photo yields
# N loosely-associated candidates and the confirm-edit UI does final
# association. Upgrade path: match barcode/date boxes by pixel proximity.
"""

from __future__ import annotations

from expirationradar.models import ScanResult


def scan_image(image_bytes: bytes, check_recalls: bool = True) -> ScanResult:
    """The whole §3 pipeline. Never raises for a missing tier — degradations
    land in ScanResult.warnings and N/A fields."""
    raise NotImplementedError("Phase 2 — see plan §3")
