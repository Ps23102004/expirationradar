"""Tests for vision.py — the Ollama chat call is always mocked.

No hardware, no network, no pulled model needed. The two load-bearing cases:
a real model reply parses into VISION-provenance candidates, and every way
Ollama can be missing returns the `None` sentinel instead of raising.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest
from llm_ladder.config import ChainConfig, TierConfig, load_chains
from llm_ladder.ollama_client import OllamaConnectionError, OllamaModelNotFoundError

from expirationradar import vision

# Verbatim from a live qwen3-vl:8b call on 2026-08-20 (single item, then shelf).
LIVE_SINGLE = (
    '[{"name": "Crunchy Oat Granola, 12 oz", "brand": "Northvale Foods", '
    '"expiry_date": "2027-03-15", "upc": null}]'
)
LIVE_MULTI = (
    '[{"name": "Crunchy Oat Granola", "brand": "Northvale Foods", '
    '"expiry_date": "2027-03-15", "upc": null}, '
    '{"name": "Whole Milk 1 gal", "brand": "Valley Dairy", '
    '"expiry_date": "2026-08-24", "upc": null}]'
)

IMAGE = b"pretend-jpeg-bytes"


def _reply(content: str) -> dict:
    return {"message": {"role": "assistant", "content": content}}


@pytest.fixture(autouse=True)
def _configured_chain():
    """Pin the chain so tests don't depend on chains.yaml's current tag."""
    chain = ChainConfig(
        name="vision_extract", tiers=[TierConfig("qwen3-vl:8b", 1, 0.6)]
    )
    with patch.object(vision, "load_chain", return_value=chain):
        yield


def test_parses_a_real_single_item_reply():
    with patch.object(vision, "chat", return_value=_reply(LIVE_SINGLE)) as chat:
        candidates = vision.extract(IMAGE)

    assert len(candidates) == 1
    c = candidates[0]
    assert c.product_name.value == "Crunchy Oat Granola, 12 oz"
    assert c.brand.value == "Northvale Foods"
    assert c.expiry_date.value == "2027-03-15"
    assert c.upc.value is None

    # Everything vision produces is labeled VISION and ranks below every
    # deterministic confidence in the app.
    for f in (c.product_name, c.brand, c.expiry_date):
        assert f.source == "VISION"
        assert f.confidence == vision.VISION_CONFIDENCE < 0.5

    # The image really was sent as base64 on the patched `images` kwarg.
    assert chat.call_args.kwargs["images"] == ["cHJldGVuZC1qcGVnLWJ5dGVz"]


def test_parses_a_multi_item_shelf_reply():
    with patch.object(vision, "chat", return_value=_reply(LIVE_MULTI)):
        candidates = vision.extract(IMAGE)

    assert [c.product_name.value for c in candidates] == [
        "Crunchy Oat Granola",
        "Whole Milk 1 gal",
    ]


def test_strips_code_fences_and_prose_around_the_json():
    noisy = "Here is what I see:\n```json\n" + LIVE_SINGLE + "\n```\nHope that helps!"
    with patch.object(vision, "chat", return_value=_reply(noisy)):
        assert len(vision.extract(IMAGE)) == 1


def test_non_iso_date_is_normalized_through_dates_py():
    with patch.object(
        vision, "chat", return_value=_reply('[{"name": "X", "expiry_date": "03/15/27"}]')
    ):
        assert vision.extract(IMAGE)[0].expiry_date.value == "2027-03-15"


def test_a_upc_that_fails_its_check_digit_is_dropped():
    good = '[{"name": "A", "upc": "0038000138416"}]'
    bad = '[{"name": "A", "upc": "0038000138417"}]'

    with patch.object(vision, "chat", return_value=_reply(good)):
        assert vision.extract(IMAGE)[0].upc.value == "0038000138416"
    with patch.object(vision, "chat", return_value=_reply(bad)):
        assert vision.extract(IMAGE)[0].upc.value is None


@pytest.mark.parametrize("junk", ["", "I'm sorry, I can't help with that.", "{}", "[]"])
def test_unparseable_or_empty_replies_yield_no_candidates(junk):
    with patch.object(vision, "chat", return_value=_reply(junk)):
        assert vision.extract(IMAGE) == []


def test_nullish_strings_become_empty_fields():
    reply = '[{"name": "Oats", "brand": "unknown", "expiry_date": "N/A", "upc": "none"}]'
    with patch.object(vision, "chat", return_value=_reply(reply)):
        c = vision.extract(IMAGE)[0]
    assert c.product_name.value == "Oats"
    assert c.brand.value is None and c.expiry_date.value is None and c.upc.value is None


@pytest.mark.parametrize(
    "error",
    [
        OllamaConnectionError("Could not reach Ollama endpoint"),
        OllamaModelNotFoundError("Model 'qwen3-vl:8b' isn't available"),
    ],
)
def test_ollama_off_or_model_missing_returns_the_none_sentinel(error):
    """Never raises upward — None is what "vision unavailable" looks like."""
    with patch.object(vision, "chat", side_effect=error):
        assert vision.extract(IMAGE) is None


def test_no_configured_chain_returns_the_none_sentinel():
    with patch.object(vision, "load_chain", return_value=None):
        assert vision.extract(IMAGE) is None
        assert vision.is_available() is False


def test_none_and_empty_list_are_different_states():
    """[] = vision ran and saw nothing; None = vision could not run at all."""
    with patch.object(vision, "chat", return_value=_reply("[]")):
        assert vision.extract(IMAGE) == []
    with patch.object(vision, "chat", side_effect=OllamaConnectionError("off")):
        assert vision.extract(IMAGE) is None


def test_is_available_checks_the_tag_list_without_loading_the_model():
    with patch.object(vision.requests, "get") as get:
        get.return_value.json.return_value = {
            "models": [{"name": "qwen3-vl:8b"}, {"name": "gemma4:e4b-mlx"}]
        }
        assert vision.is_available() is True
        assert get.call_args.args[0].endswith("/api/tags")

        get.return_value.json.return_value = {"models": [{"name": "gemma4:e4b-mlx"}]}
        assert vision.is_available() is False


def test_is_available_is_false_when_ollama_is_unreachable():
    with patch.object(
        vision.requests, "get", side_effect=vision.requests.RequestException("refused")
    ):
        assert vision.is_available() is False


def test_shipped_chains_yaml_configures_both_chains(_configured_chain):
    """The repo's own chains.yaml must parse and name a tag for the chains
    scan.py and vision.py actually ask for (chains.yaml history: unverified
    tags have burned this project before)."""
    chains = load_chains(str(vision.CHAINS_PATH))  # the real file, not the patch
    assert chains["vision_extract"].tiers[0].model
    assert chains["normalize_product"].tiers[0].model
