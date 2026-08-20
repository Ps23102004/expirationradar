"""Tests for safety.py (Feature 2) — the static category lookup and the
expired-only annotation wiring. No LLM, no I/O: pure string matching + date
math against `date.today()`.
"""

from __future__ import annotations

from datetime import date, timedelta

from expirationradar import safety
from expirationradar.models import PantryItem


def _bucket(name: str) -> dict:
    return safety.safety_note(name)


# -- category buckets -------------------------------------------------------


def test_dairy_bucket():
    note = _bucket("Whole Milk, 1 gal")
    assert "bacteria" in note["risk_note"].lower()
    assert note == _bucket("Sharp Cheddar Cheese")
    assert note == _bucket("Greek Yogurt")


def test_meat_poultry_fish_bucket():
    note = _bucket("Chicken Breast")
    assert "foodborne" in note["risk_note"].lower()
    assert note == _bucket("Ground Beef")
    assert note == _bucket("Atlantic Salmon Fillet")


def test_egg_bucket():
    note = _bucket("Large Grade A Eggs")
    assert "salmonella" in note["risk_note"].lower()


def test_produce_bucket_not_alarmist():
    note = _bucket("Baby Spinach, 5 oz")
    assert "mold" in note["risk_note"].lower() or "spoil" in note["risk_note"].lower()
    # produce is a mold/spoilage framing, not a foodborne-illness one
    assert "foodborne" not in note["risk_note"].lower()


def test_grains_pantry_staples_are_quality_not_safety():
    note = _bucket("Jasmine Rice")
    assert "quality" in note["risk_note"].lower()
    assert note == _bucket("All-Purpose Flour")


def test_canned_goods_do_not_overstate_risk():
    note = _bucket("Canned Black Beans")
    assert "safe" in note["risk_note"].lower()  # explicitly reassures on routine past-date
    assert "bulging" in note["risk_note"].lower() or "bulging" in note["advice"].lower()


def test_default_bucket_for_unmatched_item():
    note = _bucket("Mystery Snack Bar")
    assert note == safety._DEFAULT
    assert "quality" in note["risk_note"].lower()


def test_case_insensitive_match():
    assert _bucket("WHOLE MILK") == _bucket("whole milk")


def test_always_returns_both_keys():
    for name in ("Milk", "Chicken", "Eggs", "Spinach", "Rice", "Canned Corn", "Anything"):
        note = safety.safety_note(name)
        assert set(note) == {"risk_note", "advice"}
        assert note["risk_note"] and note["advice"]


# -- is_expired / annotate_expired ------------------------------------------


def test_is_expired_true_for_past_date():
    past = (date.today() - timedelta(days=1)).isoformat()
    assert safety.is_expired(PantryItem(product_name="X", expiry_date=past))


def test_is_expired_false_for_future_date():
    future = (date.today() + timedelta(days=1)).isoformat()
    assert not safety.is_expired(PantryItem(product_name="X", expiry_date=future))


def test_is_expired_false_for_undated_item():
    assert not safety.is_expired(PantryItem(product_name="X", expiry_date=""))


def test_is_expired_false_for_today():
    """Not yet past its date -- expires today, not expired yet."""
    today = date.today().isoformat()
    assert not safety.is_expired(PantryItem(product_name="X", expiry_date=today))


def test_annotate_expired_sets_note_only_on_expired_items():
    past = (date.today() - timedelta(days=1)).isoformat()
    future = (date.today() + timedelta(days=1)).isoformat()
    expired_item = PantryItem(product_name="Old Milk", expiry_date=past)
    fresh_item = PantryItem(product_name="Fresh Milk", expiry_date=future)
    undated_item = PantryItem(product_name="Undated Milk", expiry_date="")

    result = safety.annotate_expired([expired_item, fresh_item, undated_item])

    assert result[0].safety_note is not None
    assert result[1].safety_note is None
    assert result[2].safety_note is None
    # no false positive: a not-yet-expired item never gets a risk note
    assert fresh_item.safety_note is None


def test_annotate_expired_mutates_in_place():
    past = (date.today() - timedelta(days=1)).isoformat()
    item = PantryItem(product_name="Old Cheese", expiry_date=past)
    safety.annotate_expired([item])
    assert item.safety_note is not None
    assert item.safety_note["risk_note"]
