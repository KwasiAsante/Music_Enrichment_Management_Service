"""app.config.Settings — url_base normalization and the PLACEHOLDER_ME
secret-detection convention.
"""

from __future__ import annotations

import pytest

from app.config import Settings


@pytest.mark.parametrize("raw,expected", [
    ("", ""),
    ("/", ""),
    ("music-helper", "/music-helper"),
    ("/music-helper", "/music-helper"),
    ("/music-helper/", "/music-helper"),
    ("music-helper/", "/music-helper"),
    ("  /music-helper  ", "/music-helper"),
    ("/a/b", "/a/b"),
])
def test_url_base_normalization(raw, expected, monkeypatch):
    monkeypatch.setenv("URL_BASE", raw)
    monkeypatch.setenv("WEB_UI_PASS", "x")
    assert Settings().url_base == expected


def test_placeholder_fields_flags_unset_secrets(monkeypatch):
    monkeypatch.setenv("LIDARR_API_KEY", "PLACEHOLDER_ME")
    monkeypatch.setenv("WEB_UI_PASS", "a-real-password")
    s = Settings()
    placeholders = s.placeholder_fields()
    assert "lidarr_api_key" in placeholders
    assert "web_ui_pass" not in placeholders


def test_placeholder_fields_empty_when_all_configured(monkeypatch):
    for key in ["LIDARR_API_KEY", "PROWLARR_API_KEY", "QBIT_PASS",
                "WEB_UI_PASS", "GITHUB_TOKEN", "GIST_ID"]:
        monkeypatch.setenv(key, "a-real-value")
    assert Settings().placeholder_fields() == []
