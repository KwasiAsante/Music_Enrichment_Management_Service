"""app.api.backup — the full-state export (everything the narrower
mapping-only export at /api/v1/mapping/export doesn't cover).
"""

from __future__ import annotations

import io
import json
import zipfile

from fastapi.testclient import TestClient


def _zip_from_response(r) -> zipfile.ZipFile:
    return zipfile.ZipFile(io.BytesIO(r.content))


def test_backup_requires_auth(client: TestClient):
    assert client.get("/api/v1/backup/export").status_code == 401


def test_backup_contains_expected_files(client: TestClient, auth, isolated_env):
    r = client.get("/api/v1/backup/export", auth=auth)
    assert r.status_code == 200
    assert r.headers["content-type"] == "application/zip"
    assert "attachment; filename=" in r.headers["content-disposition"]
    assert "music-lib-helper-backup-" in r.headers["content-disposition"]

    zf = _zip_from_response(r)
    names = set(zf.namelist())
    expected = {
        "vgmdb_mapping.json", "album_list.json", "enriched_albums.json",
        "mb_artist_cache.json", "excluded_artists.json", "skipped_albums.json",
        "artists_mbids.json", "settings_override.json", "app.db", "manifest.json",
    }
    assert expected.issubset(names), f"missing: {expected - names}"


def test_backup_json_files_have_real_content(client: TestClient, auth, isolated_env):
    from app.storage.json_store import store

    store.vgmdb_mapping.write({"mb-1": {"vgmdb_id": "42", "artist": "X", "album": "Y", "folder": "", "source": "manual"}})
    store.album_list.write({"Album1": {"artist": "X", "album": "Y", "mb_release_id": "mb-1", "folder": "X/Y"}})

    r = client.get("/api/v1/backup/export", auth=auth)
    zf = _zip_from_response(r)

    mapping_data = json.loads(zf.read("vgmdb_mapping.json"))
    assert mapping_data == {"mb-1": {"vgmdb_id": "42", "artist": "X", "album": "Y", "folder": "", "source": "manual"}}

    album_data = json.loads(zf.read("album_list.json"))
    assert "Album1" in album_data


def test_backup_redacts_secret_overrides(client: TestClient, auth, isolated_env):
    from app.core import settings_store

    settings_store.write_overrides({
        "url_base": "/my-prefix",
        "lidarr_api_key": "sk-real-secret-do-not-leak",
        "discord_webhook_artist": "https://discord.com/api/webhooks/real/token",
    })

    r = client.get("/api/v1/backup/export", auth=auth)
    zf = _zip_from_response(r)
    overrides = json.loads(zf.read("settings_override.json"))

    # non-secret value passes through untouched
    assert overrides["url_base"] == "/my-prefix"

    # secret values are never present in the archive at all
    raw_bytes = zf.read("settings_override.json")
    assert b"sk-real-secret-do-not-leak" not in raw_bytes
    assert b"real/token" not in raw_bytes

    # replaced with a was-it-configured marker instead
    assert overrides["lidarr_api_key"] == {"redacted": True, "was_configured": True}
    assert overrides["discord_webhook_artist"] == {"redacted": True, "was_configured": True}


def test_backup_redaction_reports_unconfigured_correctly(client: TestClient, auth, isolated_env):
    from app.core import settings_store

    settings_store.write_overrides({"lidarr_api_key": "PLACEHOLDER_ME"})

    r = client.get("/api/v1/backup/export", auth=auth)
    zf = _zip_from_response(r)
    overrides = json.loads(zf.read("settings_override.json"))
    assert overrides["lidarr_api_key"] == {"redacted": True, "was_configured": False}


def test_backup_manifest_lists_included_files(client: TestClient, auth, isolated_env):
    r = client.get("/api/v1/backup/export", auth=auth)
    zf = _zip_from_response(r)
    manifest = json.loads(zf.read("manifest.json"))

    assert "exported_at" in manifest
    assert "app_version" in manifest
    assert any("vgmdb_mapping.json" in i for i in manifest["included"])
    assert any("app.db" in i for i in manifest["included"])
    assert "redacted" in manifest["note"].lower()


def test_backup_survives_one_unreadable_json_file(client: TestClient, auth, isolated_env, monkeypatch):
    from app.storage.json_store import store

    def raiser():
        raise OSError("disk error")
    monkeypatch.setattr(store.album_list, "read", raiser)

    r = client.get("/api/v1/backup/export", auth=auth)
    assert r.status_code == 200  # whole export must not fail over one bad file

    zf = _zip_from_response(r)
    names = set(zf.namelist())
    assert "album_list.json" not in names  # the broken one is skipped...
    assert "vgmdb_mapping.json" in names   # ...but everything else still made it in

    manifest = json.loads(zf.read("manifest.json"))
    assert any("album_list.json" in w for w in manifest["warnings"])


def test_backup_works_before_any_state_exists(client: TestClient, auth, isolated_env):
    """Freshest possible install: no scans run, no mappings set, db just
    initialised. Should still produce a valid (mostly-empty) backup, not
    error out."""
    r = client.get("/api/v1/backup/export", auth=auth)
    assert r.status_code == 200
    zf = _zip_from_response(r)
    assert json.loads(zf.read("vgmdb_mapping.json")) == {}
    assert json.loads(zf.read("album_list.json")) == {}
