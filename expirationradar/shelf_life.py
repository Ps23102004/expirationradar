"""Curated static shelf-life-from-purchase table (receipt.py).

Same spirit as safety.py's category lookup: a hand-curated subset, not a live
API — USDA FoodKeeper is a downloadable dataset, not a query endpoint. Values
are general USDA/FDA-guidance ballparks, rounded to sensible increments and
deliberately on the conservative (shorter) side: better to under-promise
shelf life than over-promise it for a receipt with no printed expiry date.

Ordered: first keyword match wins (safety.py's pattern), so narrower/more-
specific buckets come before broad ones. `frozen` is checked before anything
else — a frozen item shouldn't fall into the (short) fresh-meat bucket just
because "chicken" also matches.
"""

from __future__ import annotations

_FROZEN_KEYWORDS = ("frozen",)
_FROZEN_DAYS = 180  # 6 months — a quality ceiling, not a safety one

# Packaging beats ingredient: "canned chicken" or "canned black beans" should
# get the long canned-goods shelf life, not the short fresh-meat bucket or
# the dry-grains bucket just because "chicken"/"bean" also appear. Checked
# right after frozen, before any ingredient-keyword bucket below.
_CANNED_KEYWORDS = ("canned", "can ", "jarred", "jar ", "tin ")
_CANNED_DAYS = 730  # 2 years

# (keywords, category label, days)
_CATEGORIES: tuple[tuple[tuple[str, ...], str, int], ...] = (
    (("egg",), "eggs", 30),
    # Hard cheese explicitly named lasts much longer unopened than the
    # soft-cheese-like default below — only when the name says so.
    (
        ("cheddar", "parmesan", "parmigiano", "romano", "asiago", "gruyere",
         "gouda", "swiss cheese", "hard cheese"),
        "hard cheese",
        120,
    ),
    (("butter",), "butter", 60),
    (("yogurt", "yoghurt"), "yogurt", 14),
    (("milk", "cream", "half and half", "half-and-half"), "milk / cream", 7),
    # Generic "cheese"/"dairy" falls here last — conservative soft-cheese
    # default, and only when nothing more specific above already matched.
    (("cheese", "dairy"), "soft cheese / dairy", 14),
    (("bread", "bagel", "bun", "roll", "tortilla", "bakery"), "fresh bread", 7),
    (
        # Checked before root vegetables/produce below: "potato chips" and
        # "corn chips" would otherwise match "potato"/"corn" as fresh produce.
        ("chip", "cracker", "cereal", "snack", "cookie", "pretzel",
         "popcorn", "granola bar"),
        "snacks / cereal",
        90,
    ),
    (
        ("lettuce", "spinach", "kale", "arugula", "chard", "cabbage",
         "salad mix", "greens"),
        "leafy greens",
        5,
    ),
    (
        ("potato", "onion", "garlic", "carrot", "beet", "turnip", "squash",
         "sweet potato", "yam", "ginger", "root vegetable"),
        "root vegetables",
        21,
    ),
    (
        ("apple", "banana", "orange", "berries", "berry", "grape", "melon",
         "tomato", "cucumber", "pepper", "broccoli", "fruit", "vegetable",
         "produce"),
        "fresh produce",
        7,
    ),
    (
        ("chicken", "beef", "pork", "turkey", "meat", "poultry", "fish",
         "salmon", "shrimp", "bacon", "sausage", "seafood"),
        "fresh meat / poultry / fish",
        3,
    ),
    (
        ("rice", "pasta", "flour", "sugar", "grain", "oats", "quinoa",
         "lentil", "bean", "legume"),
        "dry grains / pantry staples",
        365,
    ),
    (
        # Sealed/unopened shelf life — a receipt is a purchase event, not an
        # "opened" one, so this deliberately skips the shorter after-opening
        # fridge life most of these condiments also have.
        ("ketchup", "mustard", "mayo", "mayonnaise", "sauce", "condiment",
         "dressing", "syrup", "jam", "jelly", "honey", "vinegar", "oil"),
        "condiments / sauces (sealed)",
        180,
    ),
)

_DEFAULT_DAYS = 14
_DEFAULT_CATEGORY = "unknown"


def estimate_shelf_life_days(item_name: str, category_hint: str | None = None) -> tuple[int, str]:
    """Typical days-from-purchase for `item_name` (with an optional category
    guess to help matching). Always returns something — `_DEFAULT_DAYS` if
    nothing matches, since under-promising shelf life is the safe default.
    """
    text = f"{category_hint or ''} {item_name}".lower()

    if any(k in text for k in _FROZEN_KEYWORDS):
        return _FROZEN_DAYS, "frozen"
    if any(k in text for k in _CANNED_KEYWORDS):
        return _CANNED_DAYS, "canned / jarred goods"

    for keywords, category, days in _CATEGORIES:
        if any(k in text for k in keywords):
            return days, category

    return _DEFAULT_DAYS, _DEFAULT_CATEGORY


def _demo() -> None:
    assert estimate_shelf_life_days("Whole Milk, 1 gal") == (7, "milk / cream")
    assert estimate_shelf_life_days("Large Eggs") == (30, "eggs")
    assert estimate_shelf_life_days("Cheddar Cheese Block") == (120, "hard cheese")
    assert estimate_shelf_life_days("Brie", category_hint="dairy") == (14, "soft cheese / dairy")
    assert estimate_shelf_life_days("Frozen Chicken Breast") == (180, "frozen")
    assert estimate_shelf_life_days("Chicken Breast") == (3, "fresh meat / poultry / fish")
    assert estimate_shelf_life_days("Baby Spinach") == (5, "leafy greens")
    assert estimate_shelf_life_days("Russet Potatoes") == (21, "root vegetables")
    assert estimate_shelf_life_days("Jasmine Rice") == (365, "dry grains / pantry staples")
    assert estimate_shelf_life_days("Canned Black Beans") == (730, "canned / jarred goods")
    assert estimate_shelf_life_days("Ranch Dressing") == (180, "condiments / sauces (sealed)")
    assert estimate_shelf_life_days("Potato Chips") == (90, "snacks / cereal")
    assert estimate_shelf_life_days("Mystery Item X99") == (14, "unknown")
    assert estimate_shelf_life_days("Item", category_hint="dairy") == (14, "soft cheese / dairy")
    print("shelf_life ok")


if __name__ == "__main__":
    _demo()
