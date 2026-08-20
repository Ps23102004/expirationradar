"""dates.py: pure deterministic expiry parsing — the most heavily-cased
module (plan §6). No LLM, no network, no filesystem: every case here is
just text in, Field/str out.
"""

from __future__ import annotations

from datetime import date, timedelta

from expirationradar.dates import normalize_date, parse_dates

# ---------------------------------------------------------------------------
# normalize_date: one raw string -> ISO date or None
# ---------------------------------------------------------------------------


def test_normalize_numeric_mm_dd_yy():
    assert normalize_date("03/15/27") == "2027-03-15"


def test_normalize_numeric_mm_dd_yyyy():
    assert normalize_date("03/15/2027") == "2027-03-15"


def test_normalize_numeric_dash_and_dot_separators():
    assert normalize_date("03-15-2027") == "2027-03-15"
    assert normalize_date("03.15.2027") == "2027-03-15"


def test_normalize_two_digit_year_pivot():
    # 00-79 -> 20xx, 80-99 -> 19xx (standard credit-card-style pivot)
    assert normalize_date("01/01/09") == "2009-01-01"
    assert normalize_date("01/01/79") == "2079-01-01"
    assert normalize_date("01/01/80") == "1980-01-01"
    assert normalize_date("01/01/99") == "1999-01-01"


def test_normalize_day_month_name_year():
    assert normalize_date("15 MAR 2027") == "2027-03-15"
    assert normalize_date("15 mar 2027") == "2027-03-15"  # case-insensitive
    assert normalize_date("15-MAR-27") == "2027-03-15"
    assert normalize_date("1 JAN 2027") == "2027-01-01"


def test_normalize_day_month_name_full_month():
    assert normalize_date("15 March 2027") == "2027-03-15"


def test_normalize_month_year_only_defaults_to_first_of_month():
    assert normalize_date("March 2027") == "2027-03-01"
    assert normalize_date("Mar 27") == "2027-03-01"


def test_normalize_unparseable_returns_none():
    assert normalize_date("garbage") is None
    assert normalize_date("") is None
    assert normalize_date("2027") is None


def test_normalize_invalid_calendar_dates_return_none():
    assert normalize_date("13/40/2027") is None  # no month 13
    assert normalize_date("02/30/2027") is None  # no Feb 30
    assert normalize_date("32 MAR 2027") is None  # no day 32


# ---------------------------------------------------------------------------
# parse_dates: scan free text, return Fields best-first
# ---------------------------------------------------------------------------


def test_parse_dates_no_dates_in_text():
    assert parse_dates("Crunchy Oat Granola, net wt 12 oz") == []


def test_parse_dates_empty_text():
    assert parse_dates("") == []


def test_parse_dates_labeled_numeric_is_high_confidence():
    fields = parse_dates("Crunchy Oat Granola BEST BY 03/15/2027 12oz")
    assert len(fields) == 1
    assert fields[0].value == "2027-03-15"
    assert fields[0].source == "OCR"
    assert fields[0].confidence == 0.85


def test_parse_dates_bare_numeric_is_lower_confidence_than_labeled():
    labeled = parse_dates("BEST BY 03/15/2027")[0]
    bare = parse_dates("some text 03/15/2027 more text")[0]
    assert bare.value == labeled.value == "2027-03-15"
    assert bare.confidence < labeled.confidence


def test_parse_dates_day_month_year_beats_numeric_confidence():
    numeric = parse_dates("03/15/2027")[0]
    named = parse_dates("15 MAR 2027")[0]
    assert named.confidence > numeric.confidence


def test_parse_dates_labeled_day_month_year_highest_confidence():
    fields = parse_dates("USE BY 15 MAR 2027")
    assert fields[0].value == "2027-03-15"
    assert fields[0].confidence == 0.95


def test_parse_dates_month_year_only_lowest_confidence():
    labeled = parse_dates("BEST BY MAR 2027")[0]
    bare = parse_dates("MAR 2027")[0]
    assert labeled.value == bare.value == "2027-03-01"
    assert bare.confidence < labeled.confidence
    assert bare.confidence < parse_dates("03/15/2027")[0].confidence


def test_parse_dates_recognizes_all_label_variants():
    for label in ("BEST BY", "USE BY", "SELL BY", "EXP", "EXPIRES", "EXPIRATION", "BB"):
        fields = parse_dates(f"{label} 03/15/2027")
        assert fields, f"label {label!r} produced no match"
        assert fields[0].value == "2027-03-15"
        assert fields[0].confidence == 0.85, f"label {label!r} wasn't recognized as a label"


def test_parse_dates_label_is_case_insensitive():
    assert parse_dates("best by 03/15/2027")[0].confidence == 0.85
    assert parse_dates("Best By 03/15/2027")[0].confidence == 0.85


def test_parse_dates_tolerates_extra_whitespace_in_label():
    fields = parse_dates("BEST   BY    03/15/2027")
    assert fields[0].confidence == 0.85


def test_parse_dates_does_not_false_positive_on_word_containing_bb():
    # "BB" must not match inside an unrelated word like "cabbage".
    fields = parse_dates("cabbage soup 03/15/2027")
    assert fields[0].confidence < 0.85


def test_parse_dates_multiple_dates_sorted_best_first():
    fields = parse_dates("packed 03/01/2027, BEST BY 03/15/2027")
    assert len(fields) == 2
    assert fields[0].value == "2027-03-15"  # the labeled one wins
    assert fields[0].confidence >= fields[1].confidence


def test_parse_dates_dedupes_same_iso_value_keeping_higher_confidence():
    # "15 MAR 2027" appears once; a bare numeric restating the same date
    # should not produce a second, lower-confidence entry for it.
    fields = parse_dates("BEST BY 03/15/2027 ... 03/15/2027 again")
    values = [f.value for f in fields]
    assert values.count("2027-03-15") == 1
    assert fields[0].confidence == 0.85


def test_parse_dates_day_month_year_does_not_double_count_as_month_year():
    # "15 MAR 2027" must not also register as a separate "MAR 2027" hit.
    fields = parse_dates("15 MAR 2027")
    assert len(fields) == 1


def test_parse_dates_rejects_dates_too_far_in_the_past():
    stale = date.today() - timedelta(days=6 * 365)
    raw = stale.strftime("%m/%d/%Y")
    assert parse_dates(f"BEST BY {raw}") == []


def test_parse_dates_rejects_dates_too_far_in_the_future():
    garbage = date.today() + timedelta(days=11 * 365)
    raw = garbage.strftime("%m/%d/%Y")
    assert parse_dates(f"BEST BY {raw}") == []


def test_parse_dates_accepts_dates_within_the_sanity_window():
    near_past = date.today() - timedelta(days=4 * 365)
    near_future = date.today() + timedelta(days=9 * 365)
    assert parse_dates(f"BEST BY {near_past.strftime('%m/%d/%Y')}") != []
    assert parse_dates(f"BEST BY {near_future.strftime('%m/%d/%Y')}") != []


def test_parse_dates_ignores_invalid_calendar_dates_without_crashing():
    assert parse_dates("BEST BY 13/40/2027 nothing else here") == []
