"""app.api.enrich — the event-filtering branches of POST /enrich/album
(the ones that don't need beets itself running), plus job status/log
endpoints.

The actual beets import flow (BeetsEnricher.enrich_album's real path) is
intentionally out of scope here — exercising it for real needs a working
`beet` binary and a populated beets library, which is an integration-test
concern, not a unit-test one. What's covered is everything this endpoint
does *before* it would hand off to that: Lidarr's Test-button no-op,
missing mb_release_id, and an unparseable track_paths list.
"""

from __future__ import annotations

import json

from fastapi.testclient import TestClient

from app.storage.json_store import store


def test_test_event_is_ignored(client: TestClient, auth):
    r = client.post("/api/v1/enrich/album", json={"event_type": "Test"}, auth=auth)
    assert r.status_code == 200
    data = r.json()
    assert data["ok"] is True and data["source"] == "event_ignored"


def test_empty_event_type_is_ignored(client: TestClient, auth):
    r = client.post("/api/v1/enrich/album", json={}, auth=auth)
    assert r.json()["source"] == "event_ignored"


def test_non_albumdownload_event_is_ignored(client: TestClient, auth):
    r = client.post("/api/v1/enrich/album", json={"event_type": "Rename"}, auth=auth)
    assert r.json()["source"] == "event_ignored"


def test_albumdownload_without_mb_release_id_fails_cleanly(client: TestClient, auth):
    r = client.post("/api/v1/enrich/album", json={
        "event_type": "AlbumDownload", "artist_name": "X", "album_title": "Y",
    }, auth=auth)
    data = r.json()
    assert data["ok"] is False
    assert data["source"] == "no_mb_release_id"


def test_albumdownload_with_no_track_paths_fails_cleanly(client: TestClient, auth):
    r = client.post("/api/v1/enrich/album", json={
        "event_type": "AlbumDownload", "artist_name": "X", "album_title": "Y",
        "mb_release_id": "abc-123", "track_paths": [],
    }, auth=auth)
    data = r.json()
    assert data["ok"] is False
    assert data["source"] == "folder_not_found"


# ── jobs / log ────────────────────────────────────────────────────────────
def test_enrich_run_returns_job_id_immediately(client: TestClient, auth, monkeypatch):
    # The real background thread invokes BeetsEnricher against the actual
    # `beet` binary — out of scope for a unit test (see module docstring),
    # and worse, a real thread would outlive this test's isolated_env
    # fixture and go on to touch a tmp dir that's since been torn down.
    # Patched to a no-op so only the endpoint's immediate response is
    # under test here.
    import app.api.enrich as enrich_module
    monkeypatch.setattr(enrich_module, "_run_bulk_in_thread", lambda *a, **kw: None)

    r = client.post("/api/v1/enrich/run", json={"artist": [], "album": [], "redo": [], "redo_skipped": False}, auth=auth)
    assert r.status_code == 200
    assert "job_id" in r.json()


def test_get_job_status(client: TestClient, auth, isolated_env):
    from app.storage import db
    job_id = db.create_job("enrich_run")
    db.update_job(job_id, status="running", progress_current=3, progress_total=10)

    r = client.get(f"/api/v1/enrich/jobs/{job_id}", auth=auth)
    assert r.status_code == 200
    assert r.json()["status"] == "running"


def test_get_job_status_404_for_unknown(client: TestClient, auth):
    r = client.get("/api/v1/enrich/jobs/does-not-exist", auth=auth)
    assert r.status_code == 404


# ── post-enrich library scan (issue #5) ─────────────────────────────────────
def test_successful_enrich_triggers_library_scan(client: TestClient, auth, monkeypatch):
    from app.core.beets_enricher import BeetsEnricher
    from app.core.library_scanner import LibraryScanner

    monkeypatch.setattr(
        BeetsEnricher, "enrich_album",
        lambda self, info, **kw: {
            "ok": True, "source": "test", "artist": info["artist"],
            "album": info["album"], "mb_release_id": info["mb_release_id"],
            "message": "ok",
        },
    )
    scan_calls: list[bool] = []
    monkeypatch.setattr(
        LibraryScanner, "scan",
        lambda self, **kw: scan_calls.append(True) or {},
    )

    r = client.post("/api/v1/enrich/album", json={
        "event_type": "AlbumDownload", "artist_name": "X", "album_title": "Y",
        "mb_release_id": "abc-123", "track_paths": ["/music/X/Y/01.flac"],
    }, auth=auth)
    assert r.status_code == 200
    assert scan_calls == [True]


def test_dry_run_enrich_does_not_trigger_library_scan(client: TestClient, auth, monkeypatch):
    from app.core.beets_enricher import BeetsEnricher
    from app.core.library_scanner import LibraryScanner

    monkeypatch.setattr(
        BeetsEnricher, "enrich_album",
        lambda self, info, **kw: {
            "ok": True, "source": "test", "artist": info["artist"],
            "album": info["album"], "mb_release_id": info["mb_release_id"],
            "message": "ok",
        },
    )
    scan_calls: list[bool] = []
    monkeypatch.setattr(
        LibraryScanner, "scan",
        lambda self, **kw: scan_calls.append(True) or {},
    )

    r = client.post("/api/v1/enrich/album?dry_run=true", json={
        "event_type": "AlbumDownload", "artist_name": "X", "album_title": "Y",
        "mb_release_id": "abc-123", "track_paths": ["/music/X/Y/01.flac"],
    }, auth=auth)
    assert r.status_code == 200
    assert scan_calls == []


def test_enrich_log_returns_recent_entries(client: TestClient, auth, isolated_env):
    from app.storage import db
    db.add_activity("enrich", "test enrichment happened", artist="X", album="Y")

    r = client.get("/api/v1/enrich/log", auth=auth)
    assert r.status_code == 200
    assert any(e["message"] == "test enrichment happened" for e in r.json())


# ── export / import ──────────────────────────────────────────────────────
def test_export_import_round_trip(client: TestClient, auth, isolated_env):
    store.save_enriched_set({"mb-rel-1"})

    r = client.get("/api/v1/enrich/export", auth=auth)
    assert r.status_code == 200
    exported = r.json()
    assert exported["count"] == 1

    files = {"file": ("backup.json", json.dumps({
        "enriched_albums": ["mb-rel-1", "mb-rel-2"],
    }), "application/json")}
    r = client.post("/api/v1/enrich/import?mode=merge", files=files, auth=auth)
    result = r.json()
    assert result["added"] == 1 and result["unchanged"] == 1 and result["total_after"] == 2


def test_import_accepts_a_bare_list(client: TestClient, auth, isolated_env):
    files = {"file": ("backup.json", json.dumps(["mb-rel-1", "mb-rel-2"]), "application/json")}
    r = client.post("/api/v1/enrich/import?mode=merge", files=files, auth=auth)
    assert r.json()["total_after"] == 2


def test_import_replace_mode_drops_unlisted_entries(client: TestClient, auth, isolated_env):
    store.save_enriched_set({"mb-rel-1", "mb-rel-2"})
    files = {"file": ("backup.json", json.dumps({"enriched_albums": ["mb-rel-2"]}), "application/json")}
    r = client.post("/api/v1/enrich/import?mode=replace", files=files, auth=auth)
    result = r.json()
    assert result["removed"] == 1 and result["total_after"] == 1


def test_import_dry_run_does_not_write(client: TestClient, auth, isolated_env):
    before = store.enriched_albums.read()
    files = {"file": ("backup.json", json.dumps({"enriched_albums": ["mb-rel-9"]}), "application/json")}
    client.post("/api/v1/enrich/import?mode=merge&dry_run=true", files=files, auth=auth)
    assert store.enriched_albums.read() == before


def test_import_malformed_json_rejected(client: TestClient, auth):
    files = {"file": ("bad.json", b"not json", "application/json")}
    r = client.post("/api/v1/enrich/import", files=files, auth=auth)
    assert r.status_code == 400


def test_import_skips_invalid_entries(client: TestClient, auth):
    files = {"file": ("mixed.json", json.dumps({
        "enriched_albums": ["mb-rel-1", "", 42, None],
    }), "application/json")}
    r = client.post("/api/v1/enrich/import?mode=merge", files=files, auth=auth)
    assert r.json()["skipped_invalid"] == 3
