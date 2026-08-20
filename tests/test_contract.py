"""Phase 0 guard: the fixtures, models.py and docs/API.md must not drift apart.

Every fixture is round-tripped back through its dataclass, so an added,
renamed or removed field fails here instead of surprising the frontend.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from expirationradar.models import (
    PROVENANCE,
    Digest,
    Field,
    PantryItem,
    RecallMatch,
    ScanCandidate,
    ScanResult,
    to_json_dict,
)
from expirationradar.pantry import connect

FIXTURES = Path(__file__).parent / "fixtures"


def load(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text())


def rebuild_scan_result(d: dict) -> ScanResult:
    return ScanResult(
        candidates=[
            ScanCandidate(
                product_name=Field(**c["product_name"]),
                brand=Field(**c["brand"]),
                upc=Field(**c["upc"]),
                expiry_date=Field(**c["expiry_date"]),
                recalls=[RecallMatch(**r) for r in c["recalls"]],
            )
            for c in d["candidates"]
        ],
        scanned_at=d["scanned_at"],
        vision_available=d["vision_available"],
        warnings=d["warnings"],
    )


def test_scan_result_fixture_matches_models():
    raw = load("scan_result.json")
    assert to_json_dict(rebuild_scan_result(raw)) == raw

    barcode_item, fallback_item = raw["candidates"]
    assert barcode_item["upc"]["source"] == "BARCODE"
    assert barcode_item["recalls"][0]["status"] == "Ongoing"
    # The fallback path must show null (not "") for what it couldn't resolve.
    assert fallback_item["upc"]["value"] is None
    assert fallback_item["product_name"]["source"] == "VISION"


def test_pantry_fixture_matches_models():
    raw = load("pantry.json")
    assert to_json_dict([PantryItem(**i) for i in raw["items"]]) == raw["items"]


def test_digest_fixture_matches_models():
    raw = load("digest.json")
    rebuilt = Digest(
        generated_at=raw["generated_at"],
        days=raw["days"],
        expiring_soon=[PantryItem(**i) for i in raw["expiring_soon"]],
        expired=[PantryItem(**i) for i in raw["expired"]],
        new_recalls=[RecallMatch(**r) for r in raw["new_recalls"]],
        restock_forecasts=raw["restock_forecasts"],
    )
    assert to_json_dict(rebuilt) == raw


@pytest.mark.parametrize("name", ["scan_result.json", "digest.json"])
def test_every_provenance_is_a_known_value(name):
    for field_dict in _walk_fields(load(name)):
        assert field_dict["source"] in PROVENANCE
        if field_dict["value"] is None:
            assert field_dict["confidence"] == 0.0


def _walk_fields(node):
    """Yield every dict that looks like a serialized Field."""
    if isinstance(node, dict):
        if set(node) == {"value", "source", "confidence"}:
            yield node
        for v in node.values():
            yield from _walk_fields(v)
    elif isinstance(node, list):
        for v in node:
            yield from _walk_fields(v)


def test_external_api_fixtures_keep_the_fields_we_parse():
    fda = load("openfda_enforcement.json")["results"][0]
    assert {"recall_number", "status", "classification", "reason_for_recall",
            "product_description", "recalling_firm", "report_date",
            "code_info"} <= set(fda)
    # openfda.search_by_upc matches on code_info, so the UPC must live there.
    assert "0038000138416" in fda["code_info"]

    off = load("openfoodfacts_product.json")
    assert off["status"] == 1
    assert {"product_name", "brands"} <= set(off["product"])


def test_pantry_schema_applies_and_dedupes_recalls(tmp_path):
    conn = connect(tmp_path / "pantry.db")
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"pantry_items", "recall_hits", "watcher_runs"} <= tables

    conn.execute("INSERT INTO pantry_items (product_name, added_at) VALUES ('Milk', 'now')")
    item_id = conn.execute("SELECT id FROM pantry_items").fetchone()["id"]
    for _ in range(2):
        conn.execute(
            "INSERT OR IGNORE INTO recall_hits (item_id, recall_number, status, first_seen_at)"
            " VALUES (?, 'F-0455-2026', 'Ongoing', 'now')",
            (item_id,),
        )
    # UNIQUE(item_id, recall_number) is what makes the watcher alert exactly once.
    assert conn.execute("SELECT count(*) c FROM recall_hits").fetchone()["c"] == 1

    connect(tmp_path / "pantry.db")  # re-opening an existing DB is a no-op
