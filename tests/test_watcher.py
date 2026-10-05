"""Tests for watcher.py — one idempotent autonomous pass (plan §4).

Load-bearing cases: running the pass twice with unchanged state reports zero
new items (not a duplicate alert), the openFDA recheck failing degrades
gracefully while the local expiry check still completes, the written digest
matches tests/fixtures/digest.json's shape, and --install/--uninstall never
touch a real launchd job (subprocess is always mocked).
"""

from __future__ import annotations

import json
from datetime import date, timedelta
from pathlib import Path
from unittest.mock import patch

import pytest

from expirationradar import openfda, server as server_module, watcher
from expirationradar.models import PantryItem, RecallMatch
from expirationradar.pantry import add_item, connect


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


# Two days out, so the item is always inside the 5-day window the tests use.
SOON = (date.today() + timedelta(days=2)).isoformat()

@pytest.fixture
def rigged(tmp_path, monkeypatch):
    """Point every path the watcher touches at tmp_path; nothing real is touched."""
    db_path = tmp_path / "pantry.db"
    conn = connect(db_path)
    monkeypatch.setattr(watcher.pantry, "connect", lambda *a, **k: connect(db_path))
    monkeypatch.setattr(server_module, "DIGEST_PATH", tmp_path / "last_digest.json")
    monkeypatch.setattr(watcher, "WATCH_LOG", tmp_path / "watch.log")
    monkeypatch.setattr(watcher, "PLIST_PATH", tmp_path / "com.parth.expirationradar.plist")
    return conn


def test_idempotent_second_pass_reports_zero_new(rigged):
    add_item(rigged, PantryItem(product_name="Crunchy Oat Granola", upc="0038000138416",
                                 expiry_date=SOON))
    with patch.object(openfda, "search_by_upc", return_value=[_match()]), \
         patch.object(openfda, "search_by_terms", return_value=[]):
        first = watcher.run_once(days=5)
        second = watcher.run_once(days=5)

    assert first.new_recalls == 1
    assert second.new_recalls == 0  # UNIQUE(item_id, recall_number) dedup — not re-alerted

    digest_path = server_module.DIGEST_PATH
    digest = json.loads(digest_path.read_text())
    assert len(digest["expiring_soon"]) == 1  # still reported every pass, just not re-alerted


def test_idempotent_second_pass_no_new_notification(rigged):
    """The notification-worthy 'new' set (recalls + newly-crossed expiry) is
    empty on a second pass over unchanged state."""
    add_item(rigged, PantryItem(product_name="Crunchy Oat Granola", upc="0038000138416",
                                 expiry_date=SOON))
    with patch.object(openfda, "search_by_upc", return_value=[_match()]), \
         patch.object(openfda, "search_by_terms", return_value=[]), \
         patch.object(watcher, "_notify") as notify:
        watcher.run_once(days=5)
        notify.assert_called_once()  # first pass: new recall + newly-crossed expiry
        notify.reset_mock()
        watcher.run_once(days=5)
        notify.assert_not_called()  # second pass: nothing new


def test_network_down_expiry_check_still_completes(rigged):
    """The single most important property: a dead openFDA call degrades into
    WatcherRun.error, but the local-only expiry check still runs and a valid
    digest still gets written."""
    add_item(rigged, PantryItem(product_name="Old Yogurt", expiry_date="2020-01-01"))

    with patch.object(openfda, "search_by_upc", side_effect=openfda.OpenFDAError("could not reach openFDA")), \
         patch.object(openfda, "search_by_terms", side_effect=openfda.OpenFDAError("could not reach openFDA")):
        run = watcher.run_once(days=5)

    assert run.error  # the network failure was logged...
    assert run.expired == 1  # ...but the expiry check still ran and found the item

    digest = json.loads(server_module.DIGEST_PATH.read_text())
    assert digest["generated_at"]  # a valid digest was still written
    assert len(digest["expired"]) == 1


def test_digest_shape_matches_fixture(rigged):
    add_item(rigged, PantryItem(product_name="Whole Milk, 1 gal", expiry_date="2026-08-24"))
    with patch.object(openfda, "search_by_upc", return_value=[]), \
         patch.object(openfda, "search_by_terms", return_value=[]):
        watcher.run_once(days=5)

    digest = json.loads(server_module.DIGEST_PATH.read_text())
    fixture = json.loads((Path(__file__).parent / "fixtures" / "digest.json").read_text())
    assert set(digest.keys()) == set(fixture.keys())
    assert digest["days"] == 5
    assert isinstance(digest["expiring_soon"], list) and isinstance(digest["expired"], list)
    assert set(digest["expiring_soon"][0].keys()) == set(fixture["expiring_soon"][0].keys())


def test_watch_log_appended(rigged):
    add_item(rigged, PantryItem(product_name="Milk", expiry_date=SOON))
    with patch.object(openfda, "search_by_upc", return_value=[]), \
         patch.object(openfda, "search_by_terms", return_value=[]):
        watcher.run_once(days=5)
        watcher.run_once(days=5)

    lines = watcher.WATCH_LOG.read_text().splitlines()
    assert len(lines) == 2
    assert "items=1" in lines[0]


def test_install_writes_plist_and_calls_launchctl_load(rigged):
    with patch.object(watcher.subprocess, "run") as run:
        path = watcher.install()

    assert path == watcher.PLIST_PATH
    assert path.exists()
    content = path.read_text()
    assert "com.parth.expirationradar" in content
    assert "<integer>8</integer>" in content and "<integer>30</integer>" in content
    run.assert_called_once_with(
        ["launchctl", "load", str(path)], check=False, capture_output=True
    )


def test_uninstall_removes_plist_and_calls_launchctl_unload(rigged):
    watcher.PLIST_PATH.write_text("placeholder")
    with patch.object(watcher.subprocess, "run") as run:
        removed = watcher.uninstall()

    assert removed is True
    assert not watcher.PLIST_PATH.exists()
    run.assert_called_once_with(
        ["launchctl", "unload", str(watcher.PLIST_PATH)], check=False, capture_output=True
    )


def test_uninstall_when_not_installed_returns_false(rigged):
    assert watcher.uninstall() is False


def test_notify_swallows_osascript_failure(rigged):
    with patch.object(watcher.subprocess, "run", side_effect=FileNotFoundError("no osascript")):
        watcher._notify([_match()], [])  # must not raise
