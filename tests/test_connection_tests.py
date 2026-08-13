"""Unit tests for Settings-page connection checks."""

from __future__ import annotations

from unittest.mock import MagicMock

import httpx
import pytest

from app.core import connection_tests


def _response(status_code: int = 200, *, text: str = "", json_data=None) -> MagicMock:
    resp = MagicMock()
    resp.status_code = status_code
    resp.text = text
    resp.json.return_value = json_data or {}
    resp.raise_for_status.side_effect = (
        None if status_code < 400 else httpx.HTTPStatusError(
            "error", request=MagicMock(), response=resp,
        )
    )
    return resp


def test_lidarr_missing_url():
    ok, message = connection_tests.test_lidarr("", "key")
    assert ok is False
    assert "URL" in message


def test_lidarr_success(monkeypatch):
    monkeypatch.setattr(
        httpx, "get",
        lambda *args, **kwargs: _response(json_data={"version": "2.0.0"}),
    )
    ok, message = connection_tests.test_lidarr("http://lidarr", "key")
    assert ok is True
    assert "2.0.0" in message


def test_qbit_login_failure(monkeypatch):
    monkeypatch.setattr(
        httpx, "post",
        lambda *args, **kwargs: _response(text="Fails."),
    )
    ok, message = connection_tests.test_qbit("http://qbit", "admin", "secret")
    assert ok is False
    assert "Login failed" in message


def test_vgmdb_invalid_json(monkeypatch):
    resp = _response()
    resp.json.side_effect = ValueError("bad json")
    monkeypatch.setattr(httpx, "get", lambda *args, **kwargs: resp)

    ok, message = connection_tests.test_vgmdb("http://vgmdb")
    assert ok is False
    assert "invalid JSON" in message
