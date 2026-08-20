"""HTTP layer tests for server.py — routing, base64 cap, and the openFDA-style
graceful-degradation contract for /api/recipes. scan.run_scan/pantry are
mocked or pointed at a tmp_path sqlite db so nothing touches ~/.expirationradar.
"""

from __future__ import annotations

import base64
import json
import threading
from http.client import HTTPConnection
from unittest.mock import patch

import pytest

from expirationradar import server as server_module
from expirationradar.models import Field, PantryItem, RecallMatch, RecipeSuggestion, ScanCandidate, ScanResult
from expirationradar.pantry import connect
from expirationradar.server import Handler


@pytest.fixture()
def live_server(tmp_path):
    """Boot a real Handler on an ephemeral port, pointed at a tmp_path db and
    digest file so tests never touch ~/.expirationradar."""
    httpd = server_module.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    port = httpd.server_address[1]
    allowed_hosts = {f"127.0.0.1:{port}", f"localhost:{port}"}
    allowed_origins = {f"http://127.0.0.1:{port}", f"http://localhost:{port}"}
    db_path = tmp_path / "pantry.db"
    with (
        patch.object(server_module, "_ALLOWED_HOSTS", allowed_hosts),
        patch.object(server_module, "_ALLOWED_ORIGINS", allowed_origins),
        patch.object(server_module, "DIGEST_PATH", tmp_path / "last_digest.json"),
        patch.object(server_module.pantry, "connect", lambda *a, **k: connect(db_path)),
    ):
        thread = threading.Thread(target=httpd.serve_forever, daemon=True)
        thread.start()
        try:
            yield port
        finally:
            httpd.shutdown()
            thread.join(timeout=5)
            httpd.server_close()


def _request(port: int, method: str, path: str, body: bytes | None = None, headers: dict | None = None) -> tuple[int, dict]:
    conn = HTTPConnection("127.0.0.1", port, timeout=5)
    try:
        conn.request(method, path, body=body, headers=headers or {})
        resp = conn.getresponse()
        raw = resp.read()
        return resp.status, (json.loads(raw) if raw else {})
    finally:
        conn.close()


def _get(port, path):
    return _request(port, "GET", path)


def _post(port, path, body, headers=None):
    return _request(port, "POST", path, body, headers or {"Content-Type": "application/json"})


def _delete(port, path):
    return _request(port, "DELETE", path)


# -- routing / trust boundary -------------------------------------------------


def test_forbidden_host_rejected(live_server):
    status, payload = _post(live_server, "/api/pantry", b"{}", {"Host": "evil.test"})
    assert status == 403
    assert payload == {"error": "forbidden host/origin"}


def test_unknown_api_route_404(live_server):
    status, payload = _get(live_server, "/api/nope")
    assert status == 404
    assert payload == {"error": "not found"}


def test_static_index_served(live_server):
    conn = HTTPConnection("127.0.0.1", live_server, timeout=5)
    try:
        conn.request("GET", "/")
        resp = conn.getresponse()
        body = resp.read()
    finally:
        conn.close()
    assert resp.status == 200
    assert b"ExpirationRadar" in body


def test_path_traversal_blocked(live_server):
    conn = HTTPConnection("127.0.0.1", live_server, timeout=5)
    try:
        conn.request("GET", "/../pyproject.toml")
        resp = conn.getresponse()
        resp.read()
    finally:
        conn.close()
    assert resp.status == 404


def test_malformed_json_400(live_server):
    status, payload = _post(live_server, "/api/pantry", b"{")
    assert status == 400
    assert payload == {"error": "malformed JSON body"}


# -- /api/scan -----------------------------------------------------------


def _scan_result() -> ScanResult:
    return ScanResult(
        candidates=[ScanCandidate(upc=Field("0123456789012", "BARCODE", 1.0))],
        scanned_at="2026-08-20T10:00:00",
        vision_available=True,
    )


def test_scan_success_returns_scan_result(live_server):
    with patch.object(server_module.scan, "run_scan", return_value=_scan_result()) as mock_scan:
        image_b64 = base64.b64encode(b"fake jpeg bytes").decode()
        status, payload = _post(live_server, "/api/scan", json.dumps({"image_base64": image_b64}).encode())
    assert status == 200
    assert payload["candidates"][0]["upc"]["value"] == "0123456789012"
    assert mock_scan.call_args.args[0] == b"fake jpeg bytes"


def test_scan_missing_image_base64_400(live_server):
    status, payload = _post(live_server, "/api/scan", b"{}")
    assert status == 400
    assert "image_base64" in payload["error"]


def test_scan_invalid_base64_400(live_server):
    status, payload = _post(live_server, "/api/scan", json.dumps({"image_base64": "not base64!!!"}).encode())
    assert status == 400
    assert "base64" in payload["error"]


def test_scan_oversized_body_rejected(live_server):
    conn = HTTPConnection("127.0.0.1", live_server, timeout=5)
    try:
        conn.putrequest("POST", "/api/scan")
        conn.putheader("Content-Type", "application/json")
        conn.putheader("Content-Length", str(server_module._MAX_BODY_BYTES + 1))
        conn.endheaders()
        resp = conn.getresponse()
        payload = json.loads(resp.read())
    finally:
        conn.close()
    assert resp.status == 400
    assert "exceeds" in payload["error"]


# -- /api/scan-receipt ------------------------------------------------------


def _receipt_result() -> ScanResult:
    return ScanResult(
        candidates=[
            ScanCandidate(
                product_name=Field("Whole Milk 1 Gal", "OCR", 0.5),
                expiry_date=Field("2026-08-27", "ESTIMATED", 0.45),
            )
        ],
        scanned_at="2026-08-20T10:00:00",
        vision_available=True,
        warnings=["Expiry dates are estimated from typical shelf life..."],
    )


def test_scan_receipt_success_returns_scan_result(live_server):
    with patch.object(
        server_module.receipt, "parse_receipt", return_value=_receipt_result()
    ) as mock_parse:
        image_b64 = base64.b64encode(b"fake receipt bytes").decode()
        status, payload = _post(
            live_server, "/api/scan-receipt", json.dumps({"image_base64": image_b64}).encode()
        )
    assert status == 200
    assert payload["candidates"][0]["expiry_date"]["source"] == "ESTIMATED"
    assert mock_parse.call_args.args[0] == b"fake receipt bytes"


def test_scan_receipt_missing_image_base64_400(live_server):
    status, payload = _post(live_server, "/api/scan-receipt", b"{}")
    assert status == 400
    assert "image_base64" in payload["error"]


def test_scan_receipt_invalid_base64_400(live_server):
    status, payload = _post(
        live_server, "/api/scan-receipt", json.dumps({"image_base64": "not base64!!!"}).encode()
    )
    assert status == 400
    assert "base64" in payload["error"]


# -- /api/pantry -----------------------------------------------------------


def test_pantry_post_get_consume_delete_roundtrip(live_server):
    status, created = _post(live_server, "/api/pantry", json.dumps({"product_name": "Milk", "expiry_date": "2026-09-01"}).encode())
    assert status == 201
    assert created["product_name"] == "Milk"
    item_id = created["id"]

    status, listing = _get(live_server, "/api/pantry")
    assert status == 200
    assert [i["id"] for i in listing["items"]] == [item_id]

    status, consumed = _post(live_server, f"/api/pantry/{item_id}/consume", None)
    assert status == 200
    assert consumed["consumed_at"] is not None

    status, listing = _get(live_server, "/api/pantry")
    assert listing["items"] == []  # consumed items excluded by default

    status, listing = _get(live_server, "/api/pantry?all=true")
    assert len(listing["items"]) == 1

    status, deleted = _delete(live_server, f"/api/pantry/{item_id}")
    assert status == 200
    assert deleted == {"deleted": True, "id": item_id}

    status, _ = _delete(live_server, f"/api/pantry/{item_id}")
    assert status == 404


def test_pantry_post_missing_product_name_400(live_server):
    status, payload = _post(live_server, "/api/pantry", json.dumps({}).encode())
    assert status == 400
    assert "product_name" in payload["error"]


def test_pantry_post_invalid_expiry_date_400(live_server):
    status, payload = _post(live_server, "/api/pantry", json.dumps({"product_name": "Milk", "expiry_date": "not-a-date"}).encode())
    assert status == 400
    assert "expiry_date" in payload["error"]


def test_pantry_consume_missing_returns_404(live_server):
    status, payload = _post(live_server, "/api/pantry/999/consume", None)
    assert status == 404


def test_pantry_delete_missing_returns_404(live_server):
    status, payload = _delete(live_server, "/api/pantry/999")
    assert status == 404


# -- /api/digest -----------------------------------------------------------


def test_digest_missing_file_returns_empty_digest(live_server):
    status, payload = _get(live_server, "/api/digest")
    assert status == 200
    assert payload == {
        "generated_at": "", "days": 5, "expiring_soon": [], "expired": [],
        "new_recalls": [], "restock_forecasts": [],
    }


def test_digest_custom_days_default_when_missing(live_server):
    status, payload = _get(live_server, "/api/digest?days=10")
    assert status == 200
    assert payload["days"] == 10


def test_digest_reads_existing_file(live_server, tmp_path):
    digest_path = tmp_path / "last_digest.json"
    stored = {
        "generated_at": "2026-08-20T08:30:02",
        "days": 5,
        "expiring_soon": [],
        "expired": [],
        "new_recalls": [],
    }
    digest_path.write_text(json.dumps(stored))
    status, payload = _get(live_server, "/api/digest")
    assert status == 200
    assert payload == stored


# -- /api/recipes: the "Ollama off -> 200, never 503" contract -------------


def test_recipes_available_true_but_empty_when_pantry_has_nothing_expiring(live_server):
    # Real recipes.suggest() on an empty pantry: the chain never even runs,
    # so this is "nothing to cook down", not "unavailable".
    status, payload = _get(live_server, "/api/recipes")
    assert status == 200
    assert payload == {"suggestions": [], "available": True}


def test_recipes_available_false_when_chain_unavailable(live_server):
    # Ollama off / model not pulled / chain failed -> 200, available: false,
    # never a 503, per docs/API.md.
    with patch.object(
        server_module.recipes, "suggest",
        side_effect=server_module.recipes.RecipesUnavailable("Ollama off"),
    ):
        status, payload = _get(live_server, "/api/recipes")
    assert status == 200
    assert payload == {"suggestions": [], "available": False}


def test_recipes_available_true_when_suggest_implemented(live_server):
    fake = [RecipeSuggestion(title="Milk toast", uses=["Milk"], steps="Combine and toast.")]
    with patch.object(server_module.recipes, "suggest", return_value=fake):
        status, payload = _get(live_server, "/api/recipes")
    assert status == 200
    assert payload["available"] is True
    assert payload["suggestions"][0]["title"] == "Milk toast"


# -- /api/pantry/{id}/log-usage -------------------------------------------


def test_log_usage_updates_percent_remaining(live_server):
    _, created = _post(live_server, "/api/pantry", json.dumps({"product_name": "Milk"}).encode())
    item_id = created["id"]
    assert created["percent_remaining"] == 100.0

    status, updated = _post(
        live_server, f"/api/pantry/{item_id}/log-usage",
        json.dumps({"percent_remaining": 40.0}).encode(),
    )
    assert status == 200
    assert updated["percent_remaining"] == 40.0


def test_log_usage_missing_item_404(live_server):
    status, payload = _post(
        live_server, "/api/pantry/999/log-usage", json.dumps({"percent_remaining": 40.0}).encode()
    )
    assert status == 404


def test_log_usage_out_of_range_400(live_server):
    _, created = _post(live_server, "/api/pantry", json.dumps({"product_name": "Milk"}).encode())
    status, payload = _post(
        live_server, f"/api/pantry/{created['id']}/log-usage",
        json.dumps({"percent_remaining": 150.0}).encode(),
    )
    assert status == 400


def test_log_usage_missing_field_400(live_server):
    _, created = _post(live_server, "/api/pantry", json.dumps({"product_name": "Milk"}).encode())
    status, payload = _post(live_server, f"/api/pantry/{created['id']}/log-usage", b"{}")
    assert status == 400
    assert "percent_remaining" in payload["error"]


# -- /api/settings -----------------------------------------------------------


def test_settings_default_household_size(live_server):
    status, payload = _get(live_server, "/api/settings")
    assert status == 200
    assert payload == {"household_size": 1}


def test_settings_post_updates_and_persists(live_server):
    status, payload = _post(live_server, "/api/settings", json.dumps({"household_size": 4}).encode())
    assert status == 200
    assert payload == {"household_size": 4}

    status, payload = _get(live_server, "/api/settings")
    assert status == 200
    assert payload == {"household_size": 4}


def test_settings_post_invalid_400(live_server):
    status, payload = _post(live_server, "/api/settings", json.dumps({"household_size": "many"}).encode())
    assert status == 400

    status, payload = _post(live_server, "/api/settings", json.dumps({"household_size": 0}).encode())
    assert status == 400


# -- expired-item safety_note wiring ----------------------------------------


def test_pantry_get_annotates_safety_note_only_when_expired(live_server):
    _post(live_server, "/api/pantry", json.dumps({"product_name": "Old Milk", "expiry_date": "2000-01-01"}).encode())
    _post(live_server, "/api/pantry", json.dumps({"product_name": "Fresh Milk", "expiry_date": "2099-01-01"}).encode())

    status, listing = _get(live_server, "/api/pantry")
    assert status == 200
    by_name = {i["product_name"]: i for i in listing["items"]}
    assert by_name["Old Milk"]["safety_note"] is not None
    assert by_name["Fresh Milk"]["safety_note"] is None
