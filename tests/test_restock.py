"""Tests for restock.py — consumption-rate forecasting (Feature 1): manual
self-report + simple two-point rate + AI-phrased nudge with a template
fallback. Real sqlite against a tmp_path DB (matches test_pantry.py /
test_watcher.py); the llm-ladder chain call is always mocked or absent.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest
from llm_ladder.config import ChainConfig, TierConfig
from llm_ladder.engine import CascadeResult
from llm_ladder.ollama_client import OllamaConnectionError

from expirationradar import pantry, restock
from expirationradar.models import PantryItem


@pytest.fixture
def rigged(tmp_path, monkeypatch):
    """Point restock.py's `pantry.connect()` calls at a tmp_path DB."""
    db_path = tmp_path / "pantry.db"
    real_connect = pantry.connect
    monkeypatch.setattr(pantry, "connect", lambda *a, **k: real_connect(db_path))
    return real_connect(db_path)


def _log(conn, item_id, percent, logged_at):
    conn.execute(
        "INSERT INTO consumption_log (item_id, percent_remaining, logged_at) VALUES (?, ?, ?)",
        (item_id, percent, logged_at),
    )
    conn.execute("UPDATE pantry_items SET percent_remaining = ? WHERE id = ?", (percent, item_id))
    conn.commit()


# -- log_usage ----------------------------------------------------------


def test_log_usage_updates_item_and_appends_log(rigged):
    item = pantry.add_item(rigged, PantryItem(product_name="Olive Oil"))

    restock.log_usage(item.id, 60.0)

    updated = pantry.get_item(rigged, item.id)
    assert updated.percent_remaining == 60.0
    rows = rigged.execute("SELECT * FROM consumption_log WHERE item_id = ?", (item.id,)).fetchall()
    assert len(rows) == 1
    assert rows[0]["percent_remaining"] == 60.0


@pytest.mark.parametrize("bad", [-1.0, 100.1, 200.0])
def test_log_usage_rejects_out_of_range(rigged, bad):
    item = pantry.add_item(rigged, PantryItem(product_name="Olive Oil"))
    with pytest.raises(ValueError):
        restock.log_usage(item.id, bad)


@pytest.mark.parametrize("ok", [0.0, 50.0, 100.0])
def test_log_usage_accepts_boundary_values(rigged, ok):
    item = pantry.add_item(rigged, PantryItem(product_name="Olive Oil"))
    restock.log_usage(item.id, ok)  # must not raise


# -- forecast -------------------------------------------------------------


def test_forecast_with_zero_logs_is_none(rigged):
    item = pantry.add_item(rigged, PantryItem(product_name="Olive Oil"))
    assert restock.forecast(item.id) is None


def test_forecast_with_one_log_is_none(rigged):
    item = pantry.add_item(rigged, PantryItem(product_name="Olive Oil"))
    restock.log_usage(item.id, 80.0)
    assert restock.forecast(item.id) is None


def test_forecast_with_two_logs_projects_days_until_empty(rigged):
    item = pantry.add_item(rigged, PantryItem(product_name="Olive Oil"))
    _log(rigged, item.id, 80.0, "2026-08-10T00:00:00+00:00")
    _log(rigged, item.id, 40.0, "2026-08-20T00:00:00+00:00")

    fc = restock.forecast(item.id)

    assert fc is not None
    assert fc["item_id"] == item.id
    assert fc["item_name"] == "Olive Oil"
    assert fc["percent_remaining"] == 40.0
    # rate = (80-40)/10 days = 4%/day; 40% / 4%/day = 10 days
    assert fc["days_until_empty"] == 10.0


def test_forecast_flat_usage_is_none(rigged):
    """No drop between reports -> no usable rate, not a divide-by-zero crash."""
    item = pantry.add_item(rigged, PantryItem(product_name="Olive Oil"))
    _log(rigged, item.id, 50.0, "2026-08-10T00:00:00+00:00")
    _log(rigged, item.id, 50.0, "2026-08-20T00:00:00+00:00")
    assert restock.forecast(item.id) is None


def test_forecast_refilled_item_is_none(rigged):
    """percent_remaining went UP (a refill) -> declines to guess, per spec."""
    item = pantry.add_item(rigged, PantryItem(product_name="Olive Oil"))
    _log(rigged, item.id, 20.0, "2026-08-10T00:00:00+00:00")
    _log(rigged, item.id, 90.0, "2026-08-20T00:00:00+00:00")
    assert restock.forecast(item.id) is None


def test_forecast_missing_item_is_none(rigged):
    assert restock.forecast(999) is None


# -- restock_digest: degradation ------------------------------------------


def test_restock_digest_skips_items_above_threshold(rigged):
    item = pantry.add_item(rigged, PantryItem(product_name="Rice"))
    _log(rigged, item.id, 90.0, "2026-08-01T00:00:00+00:00")
    _log(rigged, item.id, 85.0, "2026-08-20T00:00:00+00:00")  # slow burn, far out

    digest = restock.restock_digest(threshold_days=7)

    assert digest == []


def test_restock_digest_ollama_off_falls_back_to_template(rigged):
    item = pantry.add_item(rigged, PantryItem(product_name="Olive Oil"))
    _log(rigged, item.id, 80.0, "2026-08-15T00:00:00+00:00")
    _log(rigged, item.id, 40.0, "2026-08-20T00:00:00+00:00")  # 8%/day -> 5 days left

    with patch.object(restock, "run_cascade", side_effect=OllamaConnectionError("connection refused")):
        digest = restock.restock_digest(threshold_days=7)

    assert len(digest) == 1
    entry = digest[0]
    assert entry["item_id"] == item.id
    assert entry["item_name"] == "Olive Oil"
    assert "Olive Oil" in entry["message"]
    assert "restocking" in entry["message"]  # the plain template, never raised


def test_restock_digest_no_chain_configured_falls_back_to_template(rigged):
    item = pantry.add_item(rigged, PantryItem(product_name="Olive Oil"))
    _log(rigged, item.id, 80.0, "2026-08-15T00:00:00+00:00")
    _log(rigged, item.id, 40.0, "2026-08-20T00:00:00+00:00")

    with patch.object(restock, "load_chain", return_value=None):
        digest = restock.restock_digest(threshold_days=7)

    assert len(digest) == 1
    assert "restocking" in digest[0]["message"]


def test_restock_digest_uses_chain_reply_when_available(rigged):
    item = pantry.add_item(rigged, PantryItem(product_name="Olive Oil"))
    _log(rigged, item.id, 80.0, "2026-08-15T00:00:00+00:00")
    _log(rigged, item.id, 40.0, "2026-08-20T00:00:00+00:00")
    pantry.set_setting(rigged, "household_size", "3")

    chain = ChainConfig(name="restock_nudge", tiers=[TierConfig("gemma4:e4b-mlx", 1, 0.5)])
    reply = CascadeResult(answer="Olive oil is almost out for your household of 3!",
                           confidence=1.0, tier_index=0, model="gemma4:e4b-mlx")
    with patch.object(restock, "load_chain", return_value=chain), \
         patch.object(restock, "run_cascade", return_value=reply) as cascade:
        digest = restock.restock_digest(threshold_days=7)

    assert digest[0]["message"] == "Olive oil is almost out for your household of 3!"
    assert cascade.call_args.args[1] == "restock_nudge"


def test_restock_digest_empty_chain_reply_falls_back(rigged):
    item = pantry.add_item(rigged, PantryItem(product_name="Olive Oil"))
    _log(rigged, item.id, 80.0, "2026-08-15T00:00:00+00:00")
    _log(rigged, item.id, 40.0, "2026-08-20T00:00:00+00:00")

    chain = ChainConfig(name="restock_nudge", tiers=[TierConfig("gemma4:e4b-mlx", 1, 0.5)])
    reply = CascadeResult(answer="   ", confidence=1.0, tier_index=0, model="gemma4:e4b-mlx")
    with patch.object(restock, "load_chain", return_value=chain), \
         patch.object(restock, "run_cascade", return_value=reply):
        digest = restock.restock_digest(threshold_days=7)

    assert "restocking" in digest[0]["message"]  # blank reply -> template fallback


def test_restock_digest_household_size_defaults_to_one(rigged):
    item = pantry.add_item(rigged, PantryItem(product_name="Olive Oil"))
    _log(rigged, item.id, 80.0, "2026-08-15T00:00:00+00:00")
    _log(rigged, item.id, 40.0, "2026-08-20T00:00:00+00:00")

    with patch.object(restock, "load_chain", return_value=None):
        digest = restock.restock_digest(threshold_days=7)

    assert "household of 1" in digest[0]["message"]
