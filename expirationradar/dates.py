"""PURE regex/deterministic expiry parsing. No LLM, ever.

Handles MM/DD/YY(YY), "DD MON YYYY" (e.g. "15 MAR 2027"), "BEST BY / USE BY /
EXP / BB" label prefixes, and month-name-only forms ("March 2027", "Mar 27").
Gets the most test cases of any module (plan §6).
"""

from __future__ import annotations

import re
from datetime import date, timedelta

from expirationradar.models import Field

_MONTHS = {
    "jan": 1, "january": 1,
    "feb": 2, "february": 2,
    "mar": 3, "march": 3,
    "apr": 4, "april": 4,
    "may": 5,
    "jun": 6, "june": 6,
    "jul": 7, "july": 7,
    "aug": 8, "august": 8,
    "sep": 9, "sept": 9, "september": 9,
    "oct": 10, "october": 10,
    "nov": 11, "november": 11,
    "dec": 12, "december": 12,
}

# ponytail: no fuzzy homoglyph correction (e.g. "EXR" for "EXP") — this
# covers the common OCR noise (ragged whitespace, stray punctuation, case),
# add character-level fuzzing only if real scans show it's needed.
_LABEL = re.compile(
    r"\b(?:BEST\s*BY|USE\s*BY|SELL\s*BY|EXP(?:IRES?|IRATION)?|BB)\.?\s*[:\-]?",
    re.IGNORECASE,
)

# "03/15/27", "03-15-2027", "3.15.27" — US convention, per plan §3.
_NUMERIC = re.compile(r"\b(\d{1,2})[/\-.](\d{1,2})[/\-.](\d{2,4})\b")

# "15 MAR 2027", "15-MAR-27", "15 MAR, 2027"
_DAY_MONTH_YEAR = re.compile(r"\b(\d{1,2})[\s\-]+([A-Za-z]{3,9})\.?[\s\-,]+(\d{2,4})\b")

# "March 2027", "Mar 27" — month name, no day.
_MONTH_YEAR = re.compile(r"\b([A-Za-z]{3,9})\.?[\s\-]+(\d{4}|\d{2})\b")

# raw-pattern kind -> confidence, split on whether a BEST BY/USE BY/EXP/BB
# label was found within a few characters before the match. An explicit
# label is the strongest signal; a full day+month+name date is more
# confident than a bare MM/DD/YY (fewer ways to misread three letters as
# the wrong month than two digits as the wrong one); a month-with-no-day is
# the weakest since it's least specific.
_CONFIDENCE = {
    ("day_month_year", True): 0.95,
    ("day_month_year", False): 0.75,
    ("numeric", True): 0.85,
    ("numeric", False): 0.5,
    ("month_year", True): 0.6,
    ("month_year", False): 0.35,
}

_LABEL_GAP = 8  # max stray chars (OCR noise) between a label and its date
_SANITY_PAST = timedelta(days=5 * 365)
_SANITY_FUTURE = timedelta(days=10 * 365)


def _month_num(name: str) -> int | None:
    return _MONTHS.get(name.lower().rstrip("."))


def _full_year(yy: int) -> int:
    if yy > 99:
        return yy
    return 2000 + yy if yy < 80 else 1900 + yy  # standard credit-card-style pivot


def _in_sanity_window(d: date, today: date) -> bool:
    return (today - _SANITY_PAST) <= d <= (today + _SANITY_FUTURE)


def normalize_date(raw: str) -> str | None:
    """One raw date string -> ISO 'YYYY-MM-DD', or None if unparseable."""
    raw = raw.strip()

    m = _NUMERIC.fullmatch(raw)
    if m:
        mm, dd, yy = (int(g) for g in m.groups())
        try:
            return date(_full_year(yy), mm, dd).isoformat()
        except ValueError:
            return None

    m = _DAY_MONTH_YEAR.fullmatch(raw)
    if m:
        dd, mon, yy = m.groups()
        month = _month_num(mon)
        if month is None:
            return None
        try:
            return date(_full_year(int(yy)), month, int(dd)).isoformat()
        except ValueError:
            return None

    m = _MONTH_YEAR.fullmatch(raw)
    if m:
        mon, yy = m.groups()
        month = _month_num(mon)
        if month is None:
            return None
        try:
            # No day given. Default to the 1st: for a "flag it before it's
            # eaten" app, the pessimistic (earlier) read is the safe one.
            return date(_full_year(int(yy)), month, 1).isoformat()
        except ValueError:
            return None

    return None


def parse_dates(text: str) -> list[Field]:
    """Every expiry candidate in OCR text, best first, each as an ISO-valued
    Field with source="OCR" and a confidence reflecting how explicit the
    label/format was."""
    label_spans = [m.span() for m in _LABEL.finditer(text)]

    def has_nearby_label(start: int) -> bool:
        return any(
            lend <= start
            and start - lend <= _LABEL_GAP
            and "\n" not in text[lend:start]
            for _, lend in label_spans
        )

    # Most-specific pattern first: "15 MAR 2027" must claim its span before
    # the generic month-year pattern tries to match "MAR 2027" out of it.
    candidates = [
        (m.start(), m.end(), kind, m)
        for kind, pattern in (
            ("day_month_year", _DAY_MONTH_YEAR),
            ("numeric", _NUMERIC),
            ("month_year", _MONTH_YEAR),
        )
        for m in pattern.finditer(text)
    ]

    today = date.today()
    taken: list[tuple[int, int]] = []
    best: dict[str, Field] = {}

    for start, end, kind, m in candidates:
        if any(start < te and end > ts for ts, te in taken):
            continue
        iso = normalize_date(m.group(0))
        if iso is None or not _in_sanity_window(date.fromisoformat(iso), today):
            continue
        taken.append((start, end))

        confidence = _CONFIDENCE[(kind, has_nearby_label(start))]
        current = best.get(iso)
        if current is None or confidence > current.confidence:
            best[iso] = Field(value=iso, source="OCR", confidence=confidence)

    return sorted(best.values(), key=lambda f: f.confidence, reverse=True)


def _demo() -> None:
    assert normalize_date("03/15/27") == "2027-03-15"
    assert normalize_date("15 MAR 2027") == "2027-03-15"
    assert normalize_date("garbage") is None

    fields = parse_dates("Crunchy Oat Granola  BEST BY 03/15/2027  Net Wt 12oz")
    assert fields and fields[0].value == "2027-03-15"
    assert fields[0].confidence == 0.85
    print("dates ok")


if __name__ == "__main__":
    _demo()
