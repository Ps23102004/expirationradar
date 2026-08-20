"""Tests for recipes.py — the llm-ladder `recipes` chain is always mocked.

Load-bearing cases: the available:true happy path parses a real-shaped model
reply, and every way the chain can fail (no chain configured, Ollama off,
cascade error) raises RecipesUnavailable rather than crashing past this
module's boundary.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest
from llm_ladder.config import ChainConfig, TierConfig
from llm_ladder.engine import CascadeResult
from llm_ladder.ollama_client import OllamaConnectionError

from expirationradar import recipes
from expirationradar.models import PantryItem

ITEMS = [
    PantryItem(product_name="Whole Milk, 1 gal", expiry_date="2026-08-24", id=2),
    PantryItem(product_name="Baby Spinach, 5 oz", expiry_date="2026-08-22", id=3),
]

LIVE_REPLY = (
    '[{"title": "Spinach and milk breakfast bowl", '
    '"uses": ["Whole Milk, 1 gal", "Baby Spinach, 5 oz"], '
    '"steps": "Wilt the spinach, pour warm milk over it."}, '
    '{"title": "Creamed spinach", "uses": ["Baby Spinach, 5 oz"], '
    '"steps": "Saute spinach, stir in milk and simmer."}]'
)


@pytest.fixture(autouse=True)
def _configured_chain():
    chain = ChainConfig(name="recipes", tiers=[TierConfig("gemma4:e4b-mlx", 1, 0.5)])
    with patch.object(recipes, "load_chain", return_value=chain):
        yield


def test_empty_items_returns_empty_without_calling_the_chain():
    with patch.object(recipes, "run_cascade") as cascade:
        result = recipes.suggest([])
    assert result == []
    cascade.assert_not_called()


def test_available_path_parses_real_shaped_reply():
    with patch.object(
        recipes, "run_cascade",
        return_value=CascadeResult(answer=LIVE_REPLY, confidence=1.0, tier_index=0, model="gemma4:e4b-mlx"),
    ):
        suggestions = recipes.suggest(ITEMS)

    assert len(suggestions) == 2
    assert suggestions[0].title == "Spinach and milk breakfast bowl"
    assert suggestions[0].uses == ["Whole Milk, 1 gal", "Baby Spinach, 5 oz"]
    assert suggestions[0].steps


def test_caps_at_three_suggestions():
    reply = (
        '[{"title": "A", "uses": [], "steps": "x"}, {"title": "B", "uses": [], "steps": "x"}, '
        '{"title": "C", "uses": [], "steps": "x"}, {"title": "D", "uses": [], "steps": "x"}]'
    )
    with patch.object(
        recipes, "run_cascade",
        return_value=CascadeResult(answer=reply, confidence=1.0, tier_index=0, model="gemma4:e4b-mlx"),
    ):
        suggestions = recipes.suggest(ITEMS)
    assert len(suggestions) == 3


def test_no_chain_configured_raises_unavailable():
    with patch.object(recipes, "load_chain", return_value=None):
        with pytest.raises(recipes.RecipesUnavailable):
            recipes.suggest(ITEMS)


def test_ollama_off_raises_unavailable():
    with patch.object(recipes, "run_cascade", side_effect=OllamaConnectionError("connection refused")):
        with pytest.raises(recipes.RecipesUnavailable):
            recipes.suggest(ITEMS)


def test_unparseable_reply_returns_empty_not_unavailable():
    """A reply that came back but doesn't parse is 'nothing usable', not
    'unavailable' — Ollama answered, it just wasn't a useful answer."""
    with patch.object(
        recipes, "run_cascade",
        return_value=CascadeResult(answer="sorry, I can't help with that", confidence=1.0, tier_index=0, model="gemma4:e4b-mlx"),
    ):
        suggestions = recipes.suggest(ITEMS)
    assert suggestions == []
