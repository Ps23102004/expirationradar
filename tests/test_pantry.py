"""Tests for pantry.py CRUD — real sqlite against a tmp_path DB, no mocking.

Load-bearing cases: sort order (soonest expiry first, undated last),
consume/delete semantics, and the `recall_hits` UNIQUE(item_id,
recall_number) dedup that gives the watcher its once-per-recall guarantee.
"""

from __future__ import annotations

import pytest

from expirationradar.models import PantryItem, RecallMatch, WatcherRun
from expirationradar.pantry import (
    add_item,
    connect,
    consume_item,
    delete_item,
    expired_items,
    expiring_within,
    get_item,
    list_items,
    record_recall_hit,
    record_watcher_run,
)


@pytest.fixture
def conn(tmp_path):
    return connect(tmp_path / "pantry.db")


def _match(recall_number="F-0455-2026", status="Ongoing"):
    return RecallMatch(
        recall_number=recall_number,
        status=status,
        classification="Class I",
        reason="Undeclared milk allergen.",
        product_description="Crunchy Oat Granola",
        recalling_firm="Northvale Foods, Inc.",
        report_date="20260805",
        matched_on="upc",
    )


def test_add_item_populates_id_and_added_at(conn):
    item = add_item(conn, PantryItem(product_name="Milk"))

    assert item.id is not None
    assert item.added_at  # server-stamped since not supplied

    fetched = get_item(conn, item.id)
    assert fetched == item


def test_add_item_keeps_supplied_added_at(conn):
    item = add_item(conn, PantryItem(product_name="Milk", added_at="2020-01-01T00:00:00"))
    assert item.added_at == "2020-01-01T00:00:00"


def test_get_item_missing_returns_none(conn):
    assert get_item(conn, 999) is None


def test_list_items_sorted_soonest_first_undated_last(conn):
    add_item(conn, PantryItem(product_name="Undated", expiry_date=""))
    add_item(conn, PantryItem(product_name="Later", expiry_date="2026-12-01"))
    add_item(conn, PantryItem(product_name="Soonest", expiry_date="2026-08-24"))

    items = list_items(conn)

    assert [i.product_name for i in items] == ["Soonest", "Later", "Undated"]


def test_list_items_excludes_consumed_by_default(conn):
    item = add_item(conn, PantryItem(product_name="Milk"))
    consume_item(conn, item.id)
    add_item(conn, PantryItem(product_name="Eggs"))

    active = list_items(conn)
    assert [i.product_name for i in active] == ["Eggs"]

    everything = list_items(conn, include_consumed=True)
    assert {i.product_name for i in everything} == {"Milk", "Eggs"}


def test_consume_item_sets_consumed_at(conn):
    item = add_item(conn, PantryItem(product_name="Milk"))
    assert item.consumed_at is None

    consumed = consume_item(conn, item.id)

    assert consumed.consumed_at is not None
    assert get_item(conn, item.id).consumed_at == consumed.consumed_at


def test_consume_item_is_idempotent(conn):
    item = add_item(conn, PantryItem(product_name="Milk"))
    first = consume_item(conn, item.id)
    second = consume_item(conn, item.id)

    assert first.consumed_at == second.consumed_at  # unchanged on re-consume


def test_consume_item_missing_returns_none(conn):
    assert consume_item(conn, 999) is None


def test_delete_item_removes_row(conn):
    item = add_item(conn, PantryItem(product_name="Milk"))

    assert delete_item(conn, item.id) is True
    assert get_item(conn, item.id) is None
    assert delete_item(conn, item.id) is False  # already gone


def test_delete_item_cascades_recall_hits(conn):
    item = add_item(conn, PantryItem(product_name="Milk"))
    record_recall_hit(conn, item.id, _match())

    delete_item(conn, item.id)

    remaining = conn.execute("SELECT count(*) c FROM recall_hits").fetchone()["c"]
    assert remaining == 0


def test_expiring_within_and_expired_split_correctly(conn):
    from datetime import date, timedelta

    today = date.today()
    soon = (today + timedelta(days=2)).isoformat()
    far = (today + timedelta(days=30)).isoformat()
    past = (today - timedelta(days=1)).isoformat()

    add_item(conn, PantryItem(product_name="Soon", expiry_date=soon))
    add_item(conn, PantryItem(product_name="Far", expiry_date=far))
    add_item(conn, PantryItem(product_name="Past", expiry_date=past))
    consumed = add_item(conn, PantryItem(product_name="ConsumedPast", expiry_date=past))
    consume_item(conn, consumed.id)

    expiring = expiring_within(conn, days=5)
    expired = expired_items(conn)

    assert [i.product_name for i in expiring] == ["Soon"]
    assert [i.product_name for i in expired] == ["Past"]
    # consumed items never show up in either list
    assert "ConsumedPast" not in [i.product_name for i in expiring + expired]


def test_record_recall_hit_dedupes_on_unique_constraint(conn):
    item = add_item(conn, PantryItem(product_name="Milk"))

    first = record_recall_hit(conn, item.id, _match())
    second = record_recall_hit(conn, item.id, _match())  # same recall_number again

    assert first is True   # newly inserted
    assert second is False  # already seen, caught by UNIQUE(item_id, recall_number)
    count = conn.execute("SELECT count(*) c FROM recall_hits").fetchone()["c"]
    assert count == 1


def test_record_recall_hit_allows_different_recalls_for_same_item(conn):
    item = add_item(conn, PantryItem(product_name="Milk"))

    record_recall_hit(conn, item.id, _match(recall_number="F-0001-2026"))
    record_recall_hit(conn, item.id, _match(recall_number="F-0002-2026"))

    count = conn.execute("SELECT count(*) c FROM recall_hits").fetchone()["c"]
    assert count == 2


def test_record_watcher_run_populates_id(conn):
    run = record_watcher_run(conn, WatcherRun(items_checked=3, new_recalls=1))

    assert run.id is not None
    assert run.ran_at  # server-stamped
    stored = conn.execute("SELECT * FROM watcher_runs WHERE id = ?", (run.id,)).fetchone()
    assert stored["items_checked"] == 3
    assert stored["new_recalls"] == 1
