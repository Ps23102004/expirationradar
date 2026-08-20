"""Tests for openfda.py — mocked HTTP, no live network.

The load-bearing case is the 404-means-zero-results quirk (§3 step 5 of the
plan / docs/API.md): openFDA returns HTTP 404 with an error-JSON body when a
search matches nothing, and that must come back as `[]`, not an exception.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import Mock, patch

import pytest

from expirationradar.openfda import OpenFDAError, search_by_terms, search_by_upc

FIXTURES = Path(__file__).parent / "fixtures"


def _fixture() -> dict:
    return json.loads((FIXTURES / "openfda_enforcement.json").read_text())


def _resp(status_code: int, json_body: dict, url: str = "https://api.fda.gov/food/enforcement.json?search=x"):
    resp = Mock()
    resp.status_code = status_code
    resp.json.return_value = json_body
    resp.text = json.dumps(json_body)
    resp.url = url
    return resp


@patch("expirationradar.openfda.requests.get")
def test_search_by_upc_matches_fixture(mock_get):
    mock_get.return_value = _resp(200, _fixture())

    matches = search_by_upc("0038000138416")

    assert len(matches) == 1
    m = matches[0]
    assert m.recall_number == "F-0455-2026"
    assert m.status == "Ongoing"
    assert m.classification == "Class I"
    assert m.reason == "Undeclared milk allergen. Product may contain milk not listed on the label."
    assert m.matched_on == "upc"
    # search clause hit the code_info field, per the plan's UPC-first rule.
    assert "code_info" in mock_get.call_args.kwargs["params"]["search"]


@patch("expirationradar.openfda.requests.get")
def test_404_means_no_recall_not_an_error(mock_get):
    """The core quirk: HTTP 404 = zero matches, must return [] and never raise."""
    mock_get.return_value = _resp(404, {"error": {"code": "NOT_FOUND", "message": "No matches found!"}})

    assert search_by_upc("0000000000000") == []
    assert search_by_terms("Nonexistent Product") == []


@patch("expirationradar.openfda.requests.get")
def test_non_404_error_status_raises(mock_get):
    mock_get.return_value = _resp(500, {"error": "boom"})

    with pytest.raises(OpenFDAError):
        search_by_upc("0038000138416")


@patch("expirationradar.openfda.requests.get")
def test_search_by_terms_uses_product_and_brand(mock_get):
    mock_get.return_value = _resp(200, _fixture())

    matches = search_by_terms("Crunchy Oat Granola", brand="Northvale Foods")

    assert len(matches) == 1
    assert matches[0].matched_on == "product_terms"
    clause = mock_get.call_args.kwargs["params"]["search"]
    assert "Crunchy Oat Granola" in clause
    assert "Northvale Foods" in clause


@patch("expirationradar.openfda.requests.get")
def test_ongoing_status_sorts_first(mock_get):
    two_results = _fixture()
    terminated = dict(two_results["results"][0])
    terminated["status"] = "Terminated"
    terminated["recall_number"] = "F-0001-2020"
    two_results["results"] = [terminated, two_results["results"][0]]
    mock_get.return_value = _resp(200, two_results)

    matches = search_by_upc("0038000138416")

    assert matches[0].status == "Ongoing"
    assert matches[1].status == "Terminated"


@patch("expirationradar.openfda.requests.get")
def test_api_key_included_when_env_set(mock_get, monkeypatch):
    monkeypatch.setenv("OPENFDA_API_KEY", "test-key-123")
    mock_get.return_value = _resp(200, {"results": []})

    search_by_upc("0038000138416")

    assert mock_get.call_args.kwargs["params"]["api_key"] == "test-key-123"


@patch("expirationradar.openfda.requests.get")
def test_no_api_key_env_still_works(mock_get, monkeypatch):
    monkeypatch.delenv("OPENFDA_API_KEY", raising=False)
    mock_get.return_value = _resp(200, {"results": []})

    assert search_by_upc("0038000138416") == []
    assert "api_key" not in mock_get.call_args.kwargs["params"]
