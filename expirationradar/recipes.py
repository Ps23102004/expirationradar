"""Expiring pantry items -> llm-ladder `recipes` chain -> 2-3 use-it-up ideas.

`suggest()` is the one entry point `server.py`'s `/api/recipes` handler and
`cli.py`'s `recipes` command call. It never raises past this module's
boundary for the "Ollama's off" case — that's `RecipesUnavailable`, which
both callers catch to produce the "available: false" / "not available right
now" degradation documented in docs/API.md. A genuinely empty result (no
items, or the model answered but had nothing usable) is just `[]`, not an
error.
"""

from __future__ import annotations

import json

from llm_ladder.engine import run_cascade
from llm_ladder.ollama_client import OllamaConnectionError

from expirationradar.models import PantryItem, RecipeSuggestion
from expirationradar.vision import load_chain

_PROMPT = """You are a kitchen assistant helping someone use up groceries before they
spoil. Here are the pantry items expiring soon:

{items}

Suggest 2-3 short "use it up" recipe ideas using these items. Return ONLY a
JSON array, no prose, no markdown code fences, in this exact shape:
[{{"title": "...", "uses": ["<item name from the list above>", ...], "steps": "..."}}]

Rules:
- "uses" entries must be copied verbatim from the item list above.
- "steps" is 1-3 short sentences, not a full recipe card.
- Prefer ideas that use more than one of the listed items.
"""


class RecipesUnavailable(Exception):
    """The recipes chain can't run right now (Ollama off, model not pulled,
    no `recipes` chain in chains.yaml, or the cascade itself failed)."""


def suggest(items: list[PantryItem]) -> list[RecipeSuggestion]:
    """2-3 suggestions for `items`. `[]` if there's nothing to cook down.

    Raises `RecipesUnavailable` when the chain can't run at all — callers
    turn that into `available: false` (server.py) / a "not available"
    message (cli.py), never a crash.
    """
    if not items:
        return []

    chain = load_chain("recipes")
    if not chain or not chain.tiers:
        raise RecipesUnavailable("no `recipes` chain configured in chains.yaml")

    names = [item.product_name for item in items]
    prompt = _PROMPT.format(items="\n".join(f"- {n}" for n in names))
    try:
        result = run_cascade(prompt, "recipes", chain)
    except (OllamaConnectionError, ValueError, RuntimeError) as exc:
        raise RecipesUnavailable(f"recipes chain failed: {exc}") from exc

    return _parse_suggestions(result.answer)[:3]


def _parse_suggestions(raw: str) -> list[RecipeSuggestion]:
    """The JSON array out of a model reply, tolerating fences/prose around it."""
    start, end = raw.find("["), raw.rfind("]")
    if start == -1 or end < start:
        return []
    try:
        items = json.loads(raw[start : end + 1])
    except ValueError:
        return []
    if not isinstance(items, list):
        return []

    suggestions = []
    for item in items:
        if not isinstance(item, dict):
            continue
        title = item.get("title")
        if not isinstance(title, str) or not title.strip():
            continue
        uses = [u for u in item.get("uses", []) if isinstance(u, str) and u.strip()]
        steps = item.get("steps", "")
        suggestions.append(
            RecipeSuggestion(title=title.strip(), uses=uses, steps=steps if isinstance(steps, str) else "")
        )
    return suggestions


def _demo() -> None:
    parsed = _parse_suggestions(
        '```json\n[{"title": "Spinach milk bowl", "uses": ["Whole Milk, 1 gal", '
        '"Baby Spinach, 5 oz"], "steps": "Wilt spinach, pour milk."}, '
        '"not a dict", {"uses": ["no title"]}]\n```'
    )
    assert len(parsed) == 1, parsed
    assert parsed[0].title == "Spinach milk bowl"
    assert parsed[0].uses == ["Whole Milk, 1 gal", "Baby Spinach, 5 oz"]
    assert suggest([]) == []
    print("recipes ok")


if __name__ == "__main__":
    _demo()
