"""Tests for shelf_life.py — pure lookup, no mocking needed."""

from __future__ import annotations

import pytest

from expirationradar.shelf_life import estimate_shelf_life_days


@pytest.mark.parametrize(
    "name,hint,expected_category",
    [
        ("Large Eggs", None, "eggs"),
        ("Whole Milk, 1 gal", None, "milk / cream"),
        ("Cheddar Cheese Block", None, "hard cheese"),
        ("Plain Greek Yogurt", None, "yogurt"),
        ("Sourdough Bread Loaf", None, "fresh bread"),
        ("Baby Spinach, 5 oz", None, "leafy greens"),
        ("Russet Potatoes, 5 lb", None, "root vegetables"),
        ("Gala Apples", None, "fresh produce"),
        ("Chicken Breast", None, "fresh meat / poultry / fish"),
        ("Atlantic Salmon Fillet", None, "fresh meat / poultry / fish"),
        ("Frozen Peas", None, "frozen"),
        ("Frozen Chicken Breast", None, "frozen"),  # frozen overrides meat
        ("Jasmine Rice, 5 lb", None, "dry grains / pantry staples"),
        ("Canned Black Beans", None, "canned / jarred goods"),  # canned overrides "bean"
        ("Canned Chicken Breast", None, "canned / jarred goods"),  # canned overrides "chicken"
        ("Ranch Dressing", None, "condiments / sauces (sealed)"),
        ("Potato Chips", None, "snacks / cereal"),  # snacks overrides "potato"
        ("Corn Flakes Cereal", None, "snacks / cereal"),
        ("Mystery Item Q7", None, "unknown"),
        ("Some Product", "dairy", "soft cheese / dairy"),  # generic hint, no specific match
    ],
)
def test_category_matches(name, hint, expected_category):
    days, category = estimate_shelf_life_days(name, category_hint=hint)
    assert category == expected_category
    assert isinstance(days, int) and days > 0


def test_default_fallback_is_conservative():
    days, category = estimate_shelf_life_days("Completely Unrecognizable SKU 42")
    assert (days, category) == (14, "unknown")


def test_always_returns_something_never_none():
    days, category = estimate_shelf_life_days("")
    assert days > 0
    assert category


@pytest.mark.parametrize(
    "short_lived,long_lived",
    [
        ("Chicken Breast", "Jasmine Rice"),
        ("Baby Spinach", "Canned Black Beans"),
        ("Whole Milk", "Frozen Peas"),
    ],
)
def test_sane_relative_day_ranges(short_lived, long_lived):
    """Perishables get meaningfully shorter estimates than pantry-stable
    goods — the core promise of the table, not just that it returns a number."""
    short_days, _ = estimate_shelf_life_days(short_lived)
    long_days, _ = estimate_shelf_life_days(long_lived)
    assert short_days < long_days


def test_frozen_is_the_longest_bucket():
    frozen_days, _ = estimate_shelf_life_days("Frozen Chicken Breast")
    fresh_days, _ = estimate_shelf_life_days("Chicken Breast")
    assert frozen_days > fresh_days
