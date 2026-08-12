"""app.api.artist — event-filtering no-ops and the read-only suggestions
list. The real fix_one()/fix_all() filesystem-renaming paths are
integration-test territory (need real non-Latin-named folders on disk to
assert anything meaningful about) and are out of scope here, same
reasoning as test_enrich_api.py.
"""

from __future__ import annotations

from fastapi.testclient import TestClient


def _payload(**overrides):
    base = {"artist_id": 1, "mb_id": "abc-123", "artist_name": "X", "artist_path": "/music/X"}
    base.update(overrides)
    return base


def test_test_event_is_ignored(client: TestClient, auth):
    r = client.post("/api/v1/artist/fix-path", json=_payload(event_type="Test"), auth=auth)
    assert r.status_code == 200
    data = r.json()
    assert data["ok"] is True and data["strategy"] == "event_ignored"


def test_empty_event_type_is_ignored(client: TestClient, auth):
    r = client.post("/api/v1/artist/fix-path", json=_payload(), auth=auth)
    assert r.json()["strategy"] == "event_ignored"


def test_lowercase_test_is_not_recognised(client: TestClient, auth):
    # Deliberately asymmetric with enrich_album's case-insensitive check —
    # this router matches "Test" exactly. A lowercase "test" event type
    # falls through to the "not ArtistAdd" branch instead, which is still
    # a no-op, just via a different message — pinning this down so a
    # future refactor that "fixes" the casing to match enrich doesn't
    # silently change behavior unnoticed.
    r = client.post("/api/v1/artist/fix-path", json=_payload(event_type="test"), auth=auth)
    data = r.json()
    assert data["ok"] is True and data["strategy"] == "event_ignored"
    assert "is not ArtistAdd" in data["message"]


def test_non_artistadd_event_is_ignored(client: TestClient, auth):
    r = client.post("/api/v1/artist/fix-path", json=_payload(event_type="Rename"), auth=auth)
    assert r.json()["strategy"] == "event_ignored"


def test_paths_endpoint_empty_library(client: TestClient, auth, isolated_env, monkeypatch):
    # ArtistFixer.suggest_all() calls Lidarr's REST API for the artist
    # list (not a local filesystem scan, despite the endpoint name) — an
    # unmocked LidarrClient.list_artists() here would attempt a real
    # network call to whatever LIDARR_URL resolves to and hang on a slow
    # TCP timeout rather than fail fast.
    import app.core.lidarr_client as lidarr_module
    monkeypatch.setattr(lidarr_module.LidarrClient, "list_artists", lambda self: [])

    r = client.get("/api/v1/artist/paths", auth=auth)
    assert r.status_code == 200
    assert r.json() == []
