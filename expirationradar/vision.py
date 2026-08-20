"""FALLBACK-ONLY tier: Ollama /api/chat + image → strict JSON.

Triggered only when barcode + OFF + OCR left a date or product missing (§3
step 4). Never overrides a deterministic read; everything it produces is
labeled VISION provenance. Ollama down or model not pulled → returns [] and
the caller degrades to "N/A (vision unavailable)" — it never raises upward.

Model tag lives in chains.yaml under `vision_extract` (§5).
"""

from __future__ import annotations

from expirationradar.models import ScanCandidate


def is_available() -> bool:
    """True if the vision model in chains.yaml is reachable and pulled."""
    raise NotImplementedError("Phase 2 — see plan §5")


def extract(image_bytes: bytes) -> list[ScanCandidate]:
    """One image call → candidates with VISION-sourced fields. [] on failure."""
    raise NotImplementedError("Phase 2 — see plan §5")
