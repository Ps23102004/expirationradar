"""Tests for openfoodfacts.py — mocked HTTP, no live network.

The load-bearing case: `lookup()` never raises. A miss, a network error, or a
malformed response all come back as a clean `None` ("unresolved") so scan.py
can fall back to OCR-derived naming without wrapping the call in try/except.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import Mock, patch

import requests

from expirationradar.openfoodfacts import lookup

FIXTURES = Path(__file__).parent / "fixtures"


def _fixture() -> dict:
    return json.loads((FIXTURES / "openfoodfacts_product.json").read_text())


def _resp(status_code: int, json_body):
    resp = Mock()
    resp.status_code = status_code
    resp.json.return_value = json_body
    return resp


@patch("expirationradar.openfoodfacts.requests.get")
def test_lookup_hit_matches_fixture(mock_get):
    mock_get.return_value = _resp(200, _fixture())

    result = lookup("0038000138416")

    assert result == {
        "product_name": "Crunchy Oat Granola",
        "brand": "Northvale Foods",
        "upc": "0038000138416",
    }


@patch("expirationradar.openfoodfacts.requests.get")
def test_lookup_miss_status_zero_returns_none(mock_get):
    mock_get.return_value = _resp(200, {"code": "0000000000000", "status": 0, "status_verbose": "product not found"})

    assert lookup("0000000000000") is None


@patch("expirationradar.openfoodfacts.requests.get")
def test_lookup_non_200_returns_none_not_raise(mock_get):
    mock_get.return_value = _resp(503, {})

    assert lookup("0038000138416") is None


@patch("expirationradar.openfoodfacts.requests.get")
def test_lookup_network_error_returns_none_not_raise(mock_get):
    mock_get.side_effect = requests.exceptions.ConnectionError("network down")

    assert lookup("0038000138416") is None


@patch("expirationradar.openfoodfacts.requests.get")
def test_lookup_malformed_json_returns_none(mock_get):
    resp = Mock()
    resp.status_code = 200
    resp.json.side_effect = ValueError("not json")
    mock_get.return_value = resp

    assert lookup("0038000138416") is None


@patch("expirationradar.openfoodfacts.requests.get")
def test_lookup_takes_first_brand_only(mock_get):
    body = _fixture()
    body["product"]["brands"] = "Northvale Foods, Second Brand"
    mock_get.return_value = _resp(200, body)

    result = lookup("0038000138416")

    assert result["brand"] == "Northvale Foods"
