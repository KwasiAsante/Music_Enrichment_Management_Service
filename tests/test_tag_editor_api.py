"""app.api.tags — /api/v1/tags/* album tag view/edit + lock CRUD."""

from __future__ import annotations

import json
from unittest.mock import patch

from fastapi.testclient import TestClient

import app.core.tag_fields as tf
from app.storage.json_store import store


def _seed(isolated_env) -> None:
    store.album_list.write({
        "TestAlbum": {
            "artist": "Real Band Name", "album": "Test OST",
            "mb_release_id": "mb-1", "folder": "Real Band Name/Test OST",
        },
    })
    album_dir = isolated_env.music_dir / "Artist" / "Real Band Name" / "Test OST"
    album_dir.mkdir(parents=True)
    (album_dir / "01.flac").touch()


# ── GET /tags/album ──────────────────────────────────────────────────────────
def test_get_album_tags_unknown_folder_404(client: TestClient, auth):
    r = client.get("/api/v1/tags/album", params={"folder": "Nope/Nope"}, auth=auth)
    assert r.status_code == 404


def test_get_album_tags_returns_standard_and_custom_fields(client: TestClient, auth, isolated_env):
    _seed(isolated_env)
    with patch.object(tf, "read_album_fields", return_value={
        "genre": "Rock", "MyCustomTag": "Value", "musicbrainz_albumid": "abc-123",
    }):
        r = client.get("/api/v1/tags/album", params={"folder": "Real Band Name/Test OST"}, auth=auth)

    assert r.status_code == 200
    data = r.json()
    by_name = {f["name"]: f for f in data["fields"]}
    assert by_name["genre"]["value"] == "Rock"
    assert by_name["genre"]["source"] == "standard"
    assert by_name["MyCustomTag"]["source"] == "custom"
    assert by_name["musicbrainz_albumid"]["source"] == "musicbrainz"


# ── PUT /tags/album ──────────────────────────────────────────────────────────
def test_put_album_tags_unknown_folder_404(client: TestClient, auth):
    r = client.put(
        "/api/v1/tags/album", params={"folder": "Nope/Nope"},
        json={"fields": {"genre": "Rock"}}, auth=auth,
    )
    assert r.status_code == 404


def test_put_album_tags_writes_and_returns_refreshed_view(client: TestClient, auth, isolated_env):
    _seed(isolated_env)
    with patch.object(tf, "write_album_field", return_value=1):
        r = client.put(
            "/api/v1/tags/album", params={"folder": "Real Band Name/Test OST"},
            json={"fields": {"genre": "Rock", "MyBrandNewTag": "Value"}}, auth=auth,
        )

    assert r.status_code == 200
    data = r.json()
    assert data["files_touched"] == 2
    assert data["tags"]["folder"] == "Real Band Name/Test OST"


def test_put_album_tags_rejects_a_per_track_field(client: TestClient, auth, isolated_env):
    _seed(isolated_env)
    r = client.put(
        "/api/v1/tags/album", params={"folder": "Real Band Name/Test OST"},
        json={"fields": {"title": "New Title"}}, auth=auth,
    )
    assert r.status_code == 400


# ── PUT /tags/locks ───────────────────────────────────────────────────────────
def test_put_lock_toggles_and_persists(client: TestClient, auth, isolated_env):
    _seed(isolated_env)
    r = client.put(
        "/api/v1/tags/locks", params={"folder": "Real Band Name/Test OST"},
        json={"field": "genre", "locked": True}, auth=auth,
    )
    assert r.status_code == 200
    assert r.json()["locked_fields"] == ["genre"]

    r = client.get("/api/v1/tags/album", params={"folder": "Real Band Name/Test OST"}, auth=auth)
    genre_field = next(f for f in r.json()["fields"] if f["name"] == "genre")
    assert genre_field["locked"] is True

    r = client.put(
        "/api/v1/tags/locks", params={"folder": "Real Band Name/Test OST"},
        json={"field": "genre", "locked": False}, auth=auth,
    )
    assert r.json()["locked_fields"] == []


def test_put_lock_supports_a_custom_field_name(client: TestClient, auth, isolated_env):
    _seed(isolated_env)
    r = client.put(
        "/api/v1/tags/locks", params={"folder": "Real Band Name/Test OST"},
        json={"field": "MyCustomTag", "locked": True}, auth=auth,
    )
    assert r.json()["locked_fields"] == ["MyCustomTag"]


def test_put_lock_rejects_locking_a_per_track_field(client: TestClient, auth, isolated_env):
    _seed(isolated_env)
    r = client.put(
        "/api/v1/tags/locks", params={"folder": "Real Band Name/Test OST"},
        json={"field": "title", "locked": True}, auth=auth,
    )
    assert r.status_code == 400


# ── PUT /tags/track-locks ────────────────────────────────────────────────
def test_put_track_lock_toggles_and_persists(client: TestClient, auth, isolated_env):
    _seed(isolated_env)
    r = client.put(
        "/api/v1/tags/track-locks", params={"folder": "Real Band Name/Test OST"},
        json={"field": "title", "locked": True}, auth=auth,
    )
    assert r.status_code == 200
    assert r.json()["track_locks"] == {"title": True, "artist": False}

    r = client.get("/api/v1/tags/album", params={"folder": "Real Band Name/Test OST"}, auth=auth)
    assert r.json()["track_locks"] == {"title": True, "artist": False}

    r = client.put(
        "/api/v1/tags/track-locks", params={"folder": "Real Band Name/Test OST"},
        json={"field": "title", "locked": False}, auth=auth,
    )
    assert r.json()["track_locks"] == {"title": False, "artist": False}


def test_put_track_lock_both_fields_independently(client: TestClient, auth, isolated_env):
    _seed(isolated_env)
    client.put(
        "/api/v1/tags/track-locks", params={"folder": "Real Band Name/Test OST"},
        json={"field": "title", "locked": True}, auth=auth,
    )
    r = client.put(
        "/api/v1/tags/track-locks", params={"folder": "Real Band Name/Test OST"},
        json={"field": "artist", "locked": True}, auth=auth,
    )
    assert r.json()["track_locks"] == {"title": True, "artist": True}


def test_put_track_lock_rejects_a_field_other_than_title_or_artist(client: TestClient, auth, isolated_env):
    _seed(isolated_env)
    r = client.put(
        "/api/v1/tags/track-locks", params={"folder": "Real Band Name/Test OST"},
        json={"field": "genre", "locked": True}, auth=auth,
    )
    assert r.status_code == 400


# ── export / import ──────────────────────────────────────────────────────
def test_export_import_round_trip(client: TestClient, auth, isolated_env):
    store.locked_fields.write({"A/B": ["genre"]})

    r = client.get("/api/v1/tags/locks/export", auth=auth)
    assert r.status_code == 200
    exported = r.json()
    assert exported["count"] == 1

    files = {"file": ("backup.json", json.dumps({
        "locked_fields": {"A/B": ["genre", "artist"], "C/D": ["composer"]},
    }), "application/json")}
    r = client.post("/api/v1/tags/locks/import?mode=merge", files=files, auth=auth)
    result = r.json()
    assert result["added"] == 1 and result["updated"] == 1 and result["total_after"] == 2


def test_import_replace_mode_drops_unlisted_entries(client: TestClient, auth, isolated_env):
    store.locked_fields.write({"A/B": ["genre"], "C/D": ["composer"]})
    files = {"file": ("backup.json", json.dumps({"C/D": ["composer"]}), "application/json")}
    r = client.post("/api/v1/tags/locks/import?mode=replace", files=files, auth=auth)
    result = r.json()
    assert result["removed"] == 1 and result["total_after"] == 1


def test_import_dry_run_does_not_write(client: TestClient, auth, isolated_env):
    before = store.locked_fields.read()
    files = {"file": ("backup.json", json.dumps({"A/B": ["genre"]}), "application/json")}
    client.post("/api/v1/tags/locks/import?mode=merge&dry_run=true", files=files, auth=auth)
    assert store.locked_fields.read() == before


def test_import_malformed_json_rejected(client: TestClient, auth):
    files = {"file": ("bad.json", b"not json", "application/json")}
    r = client.post("/api/v1/tags/locks/import", files=files, auth=auth)
    assert r.status_code == 400


def test_import_drops_per_track_fields_and_skips_now_empty_rows(client: TestClient, auth):
    files = {"file": ("mixed.json", json.dumps({
        "good": ["genre"],
        "only_per_track": ["title"],
        "not_a_list": "nope",
    }), "application/json")}
    r = client.post("/api/v1/tags/locks/import?mode=merge", files=files, auth=auth)
    assert r.json()["skipped_invalid"] == 2
