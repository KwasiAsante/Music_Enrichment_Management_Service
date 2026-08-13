"""app.api.settings — the most stateful router in the app: view/edit
config, secret masking, the override-file persistence layer, and the
restart endpoint.
"""

from __future__ import annotations

import signal
from unittest.mock import patch

from fastapi.testclient import TestClient


def _fields(response_json) -> dict:
    return {f["key"]: f for g in response_json["groups"] for f in g["fields"]}


# ── GET ──────────────────────────────────────────────────────────────────
def test_settings_requires_auth(client: TestClient):
    assert client.get("/api/v1/settings").status_code == 401


def test_get_reflects_defaults_with_nothing_overridden(client: TestClient, auth):
    r = client.get("/api/v1/settings", auth=auth)
    assert r.status_code == 200
    data = r.json()
    assert data["restart_required"] is False

    fields = _fields(data)
    assert fields["url_base"]["value"] == ""
    assert fields["url_base"]["overridden"] is False


def test_secret_fields_never_echo_value(client: TestClient, auth, monkeypatch):
    monkeypatch.setattr("app.config.settings.lidarr_api_key", "a-real-key-value")
    r = client.get("/api/v1/settings", auth=auth)
    fields = _fields(r.json())
    assert fields["lidarr_api_key"]["value"] is None
    assert fields["lidarr_api_key"]["is_secret"] is True
    assert fields["lidarr_api_key"]["is_set"] is True


def test_secret_field_placeholder_reports_not_set(client: TestClient, auth, monkeypatch):
    monkeypatch.setattr("app.config.settings.lidarr_api_key", "PLACEHOLDER_ME")
    r = client.get("/api/v1/settings", auth=auth)
    fields = _fields(r.json())
    assert fields["lidarr_api_key"]["is_set"] is False


def test_read_only_fields_present_but_not_editable(client: TestClient, auth):
    r = client.get("/api/v1/settings", auth=auth)
    fields = _fields(r.json())
    assert fields["app_data_dir"]["editable"] is False
    assert fields["app_port"]["editable"] is False


# ── PUT ──────────────────────────────────────────────────────────────────
def test_put_saves_a_text_field(client: TestClient, auth):
    r = client.put("/api/v1/settings", json={"values": {"tz": "Europe/London"}}, auth=auth)
    assert r.status_code == 200
    data = r.json()
    assert data["saved"] == ["tz"]
    assert data["restart_required"] is True


def test_put_reflects_pending_state_before_restart(client: TestClient, auth):
    before = _fields(client.get("/api/v1/settings", auth=auth).json())["tz"]["value"]

    client.put("/api/v1/settings", json={"values": {"tz": "Europe/London"}}, auth=auth)

    r = client.get("/api/v1/settings", auth=auth)
    data = r.json()
    fields = _fields(data)
    assert data["restart_required"] is True
    assert fields["tz"]["overridden"] is True
    assert fields["tz"]["restart_pending"] is True
    # live value is unchanged — no restart has happened yet. Compared
    # against the pre-PUT value rather than a hardcoded default, since
    # the default itself depends on ambient environment/.env config this
    # test shouldn't need to assume anything about.
    assert fields["tz"]["value"] == before
    assert fields["tz"]["value"] != "Europe/London"


def test_a_fresh_process_boot_picks_up_the_override(client: TestClient, auth, isolated_env):
    """Simulates what an actual restart does: a brand new Settings()
    construction (this is exactly what app.config.get_settings() does at
    import time) reads the same override file and reflects the change —
    proving the override file is the real mechanism restart relies on,
    not just an in-memory flag."""
    client.put("/api/v1/settings", json={"values": {"tz": "Europe/London"}}, auth=auth)

    from app.config import Settings
    from app.core import settings_store

    fresh = Settings(**settings_store.read_overrides())
    assert fresh.tz == "Europe/London"


def test_put_rejects_read_only_field(client: TestClient, auth):
    r = client.put("/api/v1/settings", json={"values": {"app_port": "9999"}}, auth=auth)
    assert r.status_code == 400


def test_put_rejects_unknown_field(client: TestClient, auth):
    r = client.put("/api/v1/settings", json={"values": {"not_a_real_field": "x"}}, auth=auth)
    assert r.status_code == 400


def test_put_rejects_invalid_value(client: TestClient, auth):
    r = client.put("/api/v1/settings", json={"values": {"disable_web_ui_auth": "not-a-bool-ish-string"}}, auth=auth)
    assert r.status_code == 400


def test_blank_secret_field_left_unchanged(client: TestClient, auth):
    r = client.put("/api/v1/settings", json={"values": {"lidarr_api_key": ""}}, auth=auth)
    assert r.json()["saved"] == []


def test_secret_field_saved_but_never_echoed(client: TestClient, auth):
    r = client.put("/api/v1/settings", json={"values": {"lidarr_api_key": "sk-real-secret-123"}}, auth=auth)
    assert r.json()["saved"] == ["lidarr_api_key"]

    r = client.get("/api/v1/settings", auth=auth)
    fields = _fields(r.json())
    assert fields["lidarr_api_key"]["value"] is None
    assert fields["lidarr_api_key"]["overridden"] is True


def test_no_op_put_reports_no_changes(client: TestClient, auth):
    r = client.put("/api/v1/settings", json={"values": {}}, auth=auth)
    assert r.json()["saved"] == []


def test_url_base_saved_via_settings_gets_normalized(client: TestClient, auth):
    client.put("/api/v1/settings", json={"values": {"url_base": "my-prefix"}}, auth=auth)

    from app.core import settings_store
    overrides = settings_store.read_overrides()
    assert overrides["url_base"] == "/my-prefix"


# ── restart ──────────────────────────────────────────────────────────────
def test_restart_requires_auth(client: TestClient):
    assert client.post("/api/v1/settings/restart").status_code == 401


def test_restart_schedules_delayed_sigterm(client: TestClient, auth):
    import os
    import time
    import app.api.settings as settings_api

    with patch.object(settings_api.os, "kill") as mock_kill:
        r = client.post("/api/v1/settings/restart", auth=auth)
        assert r.status_code == 200
        assert r.json()["status"] == "restarting"
        assert not mock_kill.called  # scheduled, not immediate

        time.sleep(0.8)
        assert mock_kill.called
        args = mock_kill.call_args[0]
        assert args[0] == os.getpid()
        assert args[1] == signal.SIGTERM


# ── test-connection ─────────────────────────────────────────────────────
def test_test_connection_requires_auth(client: TestClient):
    r = client.post("/api/v1/settings/test-connection", json={"service": "lidarr"})
    assert r.status_code == 401


def test_test_connection_rejects_unknown_service(client: TestClient, auth):
    r = client.post(
        "/api/v1/settings/test-connection",
        json={"service": "not-real"},
        auth=auth,
    )
    assert r.status_code == 400


def test_test_connection_lidarr_uses_form_values(client: TestClient, auth, monkeypatch):
    from app.core import connection_tests

    captured: dict[str, str] = {}

    def fake_test(url: str, api_key: str) -> tuple[bool, str]:
        captured["url"] = url
        captured["api_key"] = api_key
        return True, "Connected — Lidarr v1.0."

    monkeypatch.setattr(connection_tests, "test_lidarr", fake_test)

    r = client.post(
        "/api/v1/settings/test-connection",
        json={
            "service": "lidarr",
            "values": {
                "lidarr_url": "http://lidarr.test",
                "lidarr_api_key": "typed-key",
            },
        },
        auth=auth,
    )
    assert r.status_code == 200
    assert r.json() == {"ok": True, "message": "Connected — Lidarr v1.0."}
    assert captured == {"url": "http://lidarr.test", "api_key": "typed-key"}


def test_test_connection_blank_secret_falls_back_to_settings(client: TestClient, auth, monkeypatch):
    from app.core import connection_tests

    captured: dict[str, str] = {}

    def fake_test(url: str, api_key: str) -> tuple[bool, str]:
        captured["url"] = url
        captured["api_key"] = api_key
        return False, "nope"

    monkeypatch.setattr("app.config.settings.lidarr_url", "http://saved.test")
    monkeypatch.setattr("app.config.settings.lidarr_api_key", "saved-key")
    monkeypatch.setattr(connection_tests, "test_lidarr", fake_test)

    r = client.post(
        "/api/v1/settings/test-connection",
        json={"service": "lidarr", "values": {"lidarr_api_key": ""}},
        auth=auth,
    )
    assert r.status_code == 200
    assert captured == {"url": "http://saved.test", "api_key": "saved-key"}
