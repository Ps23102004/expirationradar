"""Consumption-rate restock forecasting (Feature 1).

Guessing quantity-remaining from a photo is unreliable, so this is manual
self-report + simple math + an AI-phrased nudge — never vision-based
quantity estimation. Degrades exactly like recipes.py: on any chain failure
(Ollama off, model not pulled, cascade error) `restock_digest` falls back to
a plain template string, never raises.
"""

from __future__ import annotations

from datetime import datetime, timezone

from llm_ladder.engine import run_cascade
from llm_ladder.ollama_client import OllamaConnectionError

from expirationradar import pantry
from expirationradar.vision import load_chain

DEFAULT_THRESHOLD_DAYS = 7

_PROMPT = (
    'Item "{name}" has about {days:.0f} days left before it runs out, for a '
    "household of {size}. Write one short, friendly restock reminder sentence. "
    "Return ONLY that sentence — no quotes, no markdown."
)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def log_usage(item_id: int, percent_remaining: float) -> None:
    """Self-report how much of an item is left. Updates the pantry row and
    appends a `consumption_log` row the rate is fit from."""
    if not 0 <= percent_remaining <= 100:
        raise ValueError("percent_remaining must be between 0 and 100")

    conn = pantry.connect()
    try:
        conn.execute(
            "UPDATE pantry_items SET percent_remaining = ? WHERE id = ?",
            (percent_remaining, item_id),
        )
        conn.execute(
            "INSERT INTO consumption_log (item_id, percent_remaining, logged_at) VALUES (?, ?, ?)",
            (item_id, percent_remaining, _now()),
        )
        conn.commit()
    finally:
        conn.close()


def forecast(item_id: int) -> dict | None:
    """Days until `item_id` runs out, from a simple two-point consumption
    rate (oldest -> newest log). `None` when there's not enough signal:
    fewer than 2 logged reports, or the level is flat/increasing (a refill —
    this isn't restock-aware, so it just declines to guess)."""
    conn = pantry.connect()
    try:
        item = pantry.get_item(conn, item_id)
        if item is None:
            return None
        rows = conn.execute(
            "SELECT percent_remaining, logged_at FROM consumption_log "
            "WHERE item_id = ? ORDER BY logged_at ASC",
            (item_id,),
        ).fetchall()
    finally:
        conn.close()

    if len(rows) < 2:
        return None

    oldest, newest = rows[0], rows[-1]
    days_elapsed = (
        datetime.fromisoformat(newest["logged_at"]) - datetime.fromisoformat(oldest["logged_at"])
    ).total_seconds() / 86400
    percent_drop = oldest["percent_remaining"] - newest["percent_remaining"]
    if days_elapsed <= 0 or percent_drop <= 0:
        return None  # flat or refilled since the first report — no usable rate

    rate_per_day = percent_drop / days_elapsed
    days_until_empty = newest["percent_remaining"] / rate_per_day

    return {
        "item_id": item_id,
        "item_name": item.product_name,
        "days_until_empty": round(days_until_empty, 1),
        "percent_remaining": newest["percent_remaining"],
    }


def _nudge_message(chain, item_name: str, days: float, household_size: int) -> str:
    fallback = f"{item_name}: ~{days:.0f} days left — consider restocking (household of {household_size})."
    if not chain or not chain.tiers:
        return fallback
    prompt = _PROMPT.format(name=item_name, days=days, size=household_size)
    try:
        result = run_cascade(prompt, "restock_nudge", chain)
    except (OllamaConnectionError, ValueError, RuntimeError):
        return fallback
    text = result.answer.strip().strip('"')
    return text or fallback


def restock_digest(threshold_days: int = DEFAULT_THRESHOLD_DAYS) -> list[dict]:
    """Every active item forecast to run out within `threshold_days`, each
    with a friendly nudge message (chain-phrased, or the template fallback)."""
    conn = pantry.connect()
    try:
        items = pantry.list_items(conn)
        household_size = int(pantry.get_setting(conn, "household_size", "1") or "1")
    finally:
        conn.close()

    chain = load_chain("restock_nudge")

    digest = []
    for item in items:
        fc = forecast(item.id)
        if fc is None or fc["days_until_empty"] > threshold_days:
            continue
        message = _nudge_message(chain, fc["item_name"], fc["days_until_empty"], household_size)
        digest.append(
            {
                "item_id": fc["item_id"],
                "item_name": fc["item_name"],
                "days_until_empty": fc["days_until_empty"],
                "message": message,
            }
        )
    return digest


def _demo() -> None:
    import tempfile
    from pathlib import Path
    from unittest.mock import patch

    with tempfile.TemporaryDirectory() as tmp:
        db_path = Path(tmp) / "t.db"
        real_connect = pantry.connect
        with patch.object(pantry, "connect", lambda *a, **k: real_connect(db_path)):
            conn = pantry.connect(db_path)
            item = pantry.add_item(conn, pantry.PantryItem(product_name="Olive Oil"))
            conn.close()

            assert forecast(item.id) is None  # 0 logs

            log_usage(item.id, 80.0)
            assert forecast(item.id) is None  # 1 log

            conn = pantry.connect(db_path)
            conn.execute(
                "UPDATE consumption_log SET logged_at = ? WHERE item_id = ?",
                ("2026-08-10T00:00:00+00:00", item.id),
            )
            conn.commit()
            conn.close()
            log_usage(item.id, 40.0)
            conn = pantry.connect(db_path)
            conn.execute(
                "UPDATE consumption_log SET logged_at = ? WHERE item_id = ? AND percent_remaining = 40.0",
                ("2026-08-20T00:00:00+00:00", item.id),
            )
            conn.commit()
            conn.close()

            fc = forecast(item.id)
            assert fc is not None
            assert fc["item_name"] == "Olive Oil"
            assert fc["days_until_empty"] == 10.0  # 40% / (40%/10d)

            try:
                log_usage(item.id, 150.0)
                raise AssertionError("expected ValueError")
            except ValueError:
                pass

    print("restock ok")


if __name__ == "__main__":
    _demo()
