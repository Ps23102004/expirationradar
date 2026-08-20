"""PURE regex/deterministic expiry parsing. No LLM, ever.

Handles MM/DD/YY(YY), DD/MM/YY ambiguity, "BEST BY / USE BY / EXP / SELL BY"
prefixes, and month-name forms ("JAN 2027", "12 MAR 26").
Gets the most test cases of any module (plan §6).
"""

from __future__ import annotations

from expirationradar.models import Field


def parse_dates(text: str) -> list[Field]:
    """Every expiry candidate in OCR text, best first, each as an ISO-valued
    Field with source="OCR" and a confidence reflecting how explicit the
    label/format was."""
    raise NotImplementedError("Phase 1 — see plan §3")


def normalize_date(raw: str) -> str | None:
    """One raw date string → ISO 'YYYY-MM-DD', or None if unparseable."""
    raise NotImplementedError("Phase 1 — see plan §3")
