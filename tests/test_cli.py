"""CLI tests via typer's CliRunner — mirrors grantradar/tests/test_cli.py.

Pantry-backed commands are pointed at a tmp_path sqlite db (real CRUD, no
mocking of pantry.py itself); scan.run_scan and recipes.suggest are mocked
per the parallel-work contract in the task brief.
"""

from __future__ import annotations

import json
from unittest.mock import patch

import pytest
from typer.testing import CliRunner

from expirationradar import cli, pantry
from expirationradar.models import Field, PantryItem, RecipeSuggestion, ScanCandidate, ScanResult
from expirationradar.pantry import connect

runner = CliRunner()


@pytest.fixture(autouse=True)
def tmp_pantry_db(tmp_path, monkeypatch):
    """Every test gets a fresh, isolated pantry db — nothing touches
    ~/.expirationradar."""
    db_path = tmp_path / "pantry.db"
    monkeypatch.setattr(pantry, "connect", lambda *a, **k: connect(db_path))


def test_scan_json_prints_scan_result(tmp_path):
    image_path = tmp_path / "photo.jpg"
    image_path.write_bytes(b"fake jpeg bytes")
    result = ScanResult(candidates=[ScanCandidate(upc=Field("012345", "BARCODE", 1.0))], scanned_at="now")

    with patch("expirationradar.cli.scan_module.run_scan", return_value=result) as mock_scan:
        invocation = runner.invoke(cli.app, ["scan", str(image_path), "--json"])

    assert invocation.exit_code == 0
    mock_scan.assert_called_once_with(b"fake jpeg bytes")
    printed = json.loads(invocation.stdout)
    assert printed["candidates"][0]["upc"]["value"] == "012345"


def test_scan_yes_adds_candidates_to_pantry(tmp_path):
    image_path = tmp_path / "photo.jpg"
    image_path.write_bytes(b"fake jpeg bytes")
    result = ScanResult(
        candidates=[ScanCandidate(product_name=Field("Milk", "BARCODE", 0.9), expiry_date=Field("2026-09-01", "OCR", 0.8))],
        scanned_at="now",
    )

    with patch("expirationradar.cli.scan_module.run_scan", return_value=result):
        invocation = runner.invoke(cli.app, ["scan", str(image_path), "--yes"])

    assert invocation.exit_code == 0
    conn = pantry.connect()
    items = pantry.list_items(conn)
    assert [i.product_name for i in items] == ["Milk"]


def test_scan_without_yes_prompts_and_skips_on_no(tmp_path):
    image_path = tmp_path / "photo.jpg"
    image_path.write_bytes(b"fake jpeg bytes")
    result = ScanResult(candidates=[ScanCandidate(product_name=Field("Milk", "BARCODE", 0.9))], scanned_at="now")

    with patch("expirationradar.cli.scan_module.run_scan", return_value=result):
        invocation = runner.invoke(cli.app, ["scan", str(image_path)], input="n\n")

    assert invocation.exit_code == 0
    conn = pantry.connect()
    assert pantry.list_items(conn) == []


def test_pantry_add_and_list():
    invocation = runner.invoke(cli.app, ["pantry", "add", "Milk", "--expiry", "2026-09-01"])
    assert invocation.exit_code == 0

    invocation = runner.invoke(cli.app, ["pantry", "list", "--json"])
    assert invocation.exit_code == 0
    items = json.loads(invocation.stdout)
    assert items[0]["product_name"] == "Milk"


def test_pantry_rm_missing_item_exits_nonzero():
    invocation = runner.invoke(cli.app, ["pantry", "rm", "999"])
    assert invocation.exit_code == 1


def test_pantry_consume_roundtrip():
    runner.invoke(cli.app, ["pantry", "add", "Milk"])
    conn = pantry.connect()
    item_id = pantry.list_items(conn)[0].id

    invocation = runner.invoke(cli.app, ["pantry", "consume", str(item_id)])
    assert invocation.exit_code == 0
    assert pantry.list_items(conn) == []  # consumed, excluded by default


def test_digest_prints_never_run_message_when_no_file():
    with patch("expirationradar.cli.read_digest", return_value={
        "generated_at": "", "days": 5, "expiring_soon": [], "expired": [], "new_recalls": [],
    }):
        invocation = runner.invoke(cli.app, ["digest"])
    assert invocation.exit_code == 0
    assert "hasn't run yet" in invocation.stdout


def test_export_lists_expiring_and_expired_items():
    conn = pantry.connect()
    from datetime import date, timedelta

    pantry.add_item(conn, PantryItem(product_name="Old Yogurt", expiry_date=(date.today() - timedelta(days=1)).isoformat()))
    pantry.add_item(conn, PantryItem(product_name="Soon Milk", expiry_date=(date.today() + timedelta(days=2)).isoformat()))

    invocation = runner.invoke(cli.app, ["export"])
    assert invocation.exit_code == 0
    assert "Old Yogurt" in invocation.stdout
    assert "Soon Milk" in invocation.stdout


def test_export_empty_pantry_says_no_action_needed():
    invocation = runner.invoke(cli.app, ["export"])
    assert invocation.exit_code == 0
    assert "good shape" in invocation.stdout


def test_recipes_empty_pantry_says_nothing_to_cook():
    invocation = runner.invoke(cli.app, ["recipes"])
    assert invocation.exit_code == 0
    assert "nothing to cook down" in invocation.stdout.lower()


def test_recipes_unavailable_degrades_cleanly():
    with patch(
        "expirationradar.cli.recipes_module.suggest",
        side_effect=cli.recipes_module.RecipesUnavailable("Ollama off"),
    ):
        invocation = runner.invoke(cli.app, ["recipes"])
    assert invocation.exit_code == 0
    assert "not available" in invocation.stdout.lower() or "aren't available" in invocation.stdout


def test_recipes_prints_suggestions_when_available():
    suggestion = RecipeSuggestion(title="Milk toast", uses=["Milk"], steps="Combine and toast.")
    with patch("expirationradar.cli.recipes_module.suggest", return_value=[suggestion]):
        invocation = runner.invoke(cli.app, ["recipes"])
    assert invocation.exit_code == 0
    assert "Milk toast" in invocation.stdout


def test_serve_passes_port_straight_to_server_main():
    # main() takes the port as an argument rather than reading it back off
    # $EXPIRATIONRADAR_PORT — server.py may already be imported (freezing its
    # env-var read) by the time this command's --port option is parsed.
    with patch("expirationradar.server.main") as mock_main:
        invocation = runner.invoke(cli.app, ["serve", "--port", "9999"])
    assert invocation.exit_code == 0
    mock_main.assert_called_once_with(9999)


def test_serve_defaults_port_to_none():
    with patch("expirationradar.server.main") as mock_main:
        invocation = runner.invoke(cli.app, ["serve"])
    assert invocation.exit_code == 0
    mock_main.assert_called_once_with(None)
