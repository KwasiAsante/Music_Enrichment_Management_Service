"""app.core.vgmdb_client — search, get_album, and the User-Agent header.

test_every_request_sends_browser_user_agent is a direct regression test
for a real bug found against a live deployment: requests without a
browser-spoofing User-Agent were getting 403'd by the (self-hosted)
vgmdb-api instance for the /album/{id} endpoint specifically. Fixed by
matching the same User-Agent app/beets_plugins/VGMplug.py already sends
successfully.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import httpx
import pytest

from app.core.vgmdb_client import VGMDBClient, _HEADERS


@pytest.fixture
def client(isolated_env) -> VGMDBClient:
    return VGMDBClient()


def _fake_response(json_data):
    resp = MagicMock()
    resp.json.return_value = json_data
    resp.raise_for_status.return_value = None
    return resp


def test_every_request_sends_browser_user_agent(client):
    assert "Mozilla" in _HEADERS["User-Agent"]

    with patch("httpx.get", return_value=_fake_response({"link": "album/1"})) as mock_get:
        client.get_album("1")
        assert mock_get.call_args.kwargs["headers"] == _HEADERS

    with patch("httpx.get", return_value=_fake_response({"results": {"albums": []}})) as mock_get:
        client.search("query")
        assert mock_get.call_args.kwargs["headers"] == _HEADERS


def test_get_album_skip_sentinel_and_empty_short_circuit(client):
    with patch("httpx.get") as mock_get:
        assert client.get_album("skip") is None
        assert client.get_album("") is None
        assert client.get_album(None) is None
        mock_get.assert_not_called()


def test_get_album_network_failure_returns_none(client):
    with patch("httpx.get", side_effect=httpx.ConnectError("boom")):
        assert client.get_album("123") is None


def test_get_album_http_error_returns_none(client):
    resp = MagicMock()
    resp.raise_for_status.side_effect = httpx.HTTPStatusError(
        "403", request=MagicMock(), response=MagicMock(status_code=403),
    )
    with patch("httpx.get", return_value=resp):
        assert client.get_album("123") is None


def test_get_album_invalid_json_returns_none(client):
    resp = MagicMock()
    resp.raise_for_status.return_value = None
    resp.json.side_effect = ValueError("bad json")
    with patch("httpx.get", return_value=resp):
        assert client.get_album("123") is None


def test_search_normalises_results(client):
    raw = {
        "results": {"albums": [
            {"link": "album/999", "titles": {"en": "English Title"}, "catalog": "ABC-1"},
        ]},
    }
    with patch("httpx.get", return_value=_fake_response(raw)):
        hints = client.search("foo")

    assert hints == [{"vgmdb_id": "999", "title": "English Title", "catalog": "ABC-1", "barcode": "", "date": ""}]


def test_search_by_barcode_prefers_exact_match(client):
    raw = {
        "results": {"albums": [
            {"link": "album/1", "titles": {"en": "Wrong"}, "barcode": "0000000000000"},
            {"link": "album/2", "titles": {"en": "Right"}, "barcode": "1234567890123"},
        ]},
    }
    with patch("httpx.get", return_value=_fake_response(raw)):
        hints = client.search_by_barcode("1234567890123")

    assert len(hints) == 1
    assert hints[0]["vgmdb_id"] == "2"


def test_empty_query_short_circuits_without_request(client):
    with patch("httpx.get") as mock_get:
        assert client.search("") == []
        assert client.search_by_catalog("") == []
        assert client.search_by_barcode("") == []
        mock_get.assert_not_called()
