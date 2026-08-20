"""Expired-item safety messaging (Feature 2).

A short, genuinely useful lookup — not exhaustive, not alarmist. Most
shelf-stable pantry staples past their "best by" date are a quality issue,
not a safety one; this says so instead of crying wolf uniformly. Loose
case-insensitive substring match against the product name.
"""

from __future__ import annotations

from datetime import date

from expirationradar.models import PantryItem

# Ordered: first keyword match wins, so put narrower/more-specific buckets
# before broad ones.
_CATEGORIES: tuple[tuple[tuple[str, ...], dict], ...] = (
    (
        ("egg",),
        {
            "risk_note": "Eggs can harbor salmonella once past date, especially if stored warm.",
            "advice": "A float test (fresh eggs sink) is a rough guide, not a guarantee — when in doubt, discard.",
        },
    ),
    (
        ("milk", "cheese", "yogurt", "cream", "butter", "dairy"),
        {
            "risk_note": "Dairy can grow harmful bacteria past its date, even with no visible spoilage.",
            "advice": "Check for off smell, texture, or mold before use; when in doubt, throw it out.",
        },
    ),
    (
        ("chicken", "beef", "pork", "turkey", "meat", "poultry", "fish", "salmon",
         "shrimp", "bacon", "sausage"),
        {
            "risk_note": "Meat, poultry, and fish carry a real foodborne illness risk once expired.",
            "advice": "Do not taste-test to check; discard promptly and wash anything it touched.",
        },
    ),
    (
        ("produce", "lettuce", "spinach", "berries", "fruit", "vegetable", "salad", "greens"),
        {
            "risk_note": "Fresh produce mostly spoils visibly (mold, sliminess, rot) rather than posing a hidden risk.",
            "advice": "Trim visibly spoiled parts, or toss the whole item once mold or a sour smell appears.",
        },
    ),
    (
        ("rice", "pasta", "flour", "grain", "cereal", "oat", "oil"),
        {
            "risk_note": "Mostly a quality issue past date (rancidity in oils, staleness) — not usually a safety one, though pantry pests can get in.",
            "advice": "Check for a rancid smell (oils) or signs of insects; otherwise it's likely still fine to use.",
        },
    ),
    (
        ("canned", "jarred", "jar "),
        {
            "risk_note": "Canned/jarred goods are usually still safe well past the printed date — that's different from a bulging, leaking, rusted, or dented can, which IS a real botulism risk.",
            "advice": "If it looks and smells normal, it's typically fine; discard immediately if the can/jar is bulging, leaking, or was damaged.",
        },
    ),
)

_DEFAULT = {
    "risk_note": "Past its date, this is typically a quality issue (taste, texture) rather than a safety hazard.",
    "advice": "Use your judgment — check smell, appearance, and texture before eating.",
}


def safety_note(item_name: str) -> dict:
    """{risk_note, advice} for `item_name`. Always returns something — the
    default bucket covers anything unmatched."""
    lowered = item_name.lower()
    for keywords, note in _CATEGORIES:
        if any(k in lowered for k in keywords):
            return dict(note)
    return dict(_DEFAULT)


def is_expired(item: PantryItem) -> bool:
    return bool(item.expiry_date) and item.expiry_date < date.today().isoformat()


def annotate_expired(items: list[PantryItem]) -> list[PantryItem]:
    """Set `.safety_note` on every item that's actually expired; leaves the
    rest at their default `None`. Mutates in place, also returns `items` for
    chaining at a call site."""
    for item in items:
        if is_expired(item):
            item.safety_note = safety_note(item.product_name)
    return items


def _demo() -> None:
    assert safety_note("Whole Milk, 1 gal")["risk_note"].startswith("Dairy")
    assert safety_note("Chicken Breast")["risk_note"].startswith("Meat")
    assert safety_note("Large Eggs")["risk_note"].startswith("Eggs")
    assert safety_note("Baby Spinach, 5 oz")["risk_note"].startswith("Fresh produce")
    assert safety_note("Jasmine Rice")["risk_note"].startswith("Mostly a quality")
    assert safety_note("Canned Black Beans")["risk_note"].startswith("Canned/jarred")
    assert safety_note("Mystery Snack Bar") == _DEFAULT

    old = PantryItem(product_name="Old Yogurt", expiry_date="2020-01-01")
    fresh = PantryItem(product_name="Fresh Yogurt", expiry_date="2099-01-01")
    undated = PantryItem(product_name="Undated Yogurt", expiry_date="")
    annotate_expired([old, fresh, undated])
    assert old.safety_note is not None
    assert fresh.safety_note is None
    assert undated.safety_note is None
    print("safety ok")


if __name__ == "__main__":
    _demo()
