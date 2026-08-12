"""app.api.mapping — CRUD, export/import backup, excluded artists,
skipped albums.
"""

from __future__ import annotations

import json

from fastapi.testclient import TestClient


# ── basic CRUD ───────────────────────────────────────────────────────────
def test_set_and_get_mapping(client: TestClient, auth):
    r = client.put("/api/v1/mapping/mb-rel-1",
                    json={"vgmdb_id": "42", "artist": "X", "album": "Y"}, auth=auth)
    assert r.status_code == 200

    r = client.get("/api/v1/mapping", auth=auth)
    rows = r.json()
    assert len(rows) == 1 and rows[0]["mb_release_id"] == "mb-rel-1"


def test_delete_mapping(client: TestClient, auth):
    client.put("/api/v1/mapping/mb-rel-1", json={"vgmdb_id": "42"}, auth=auth)
    r = client.delete("/api/v1/mapping/mb-rel-1", auth=auth)
    assert r.json()["deleted"] is True

    r = client.delete("/api/v1/mapping/mb-rel-1", auth=auth)
    assert r.json()["deleted"] is False


# ── export / import ──────────────────────────────────────────────────────
def test_export_import_round_trip(client: TestClient, auth, isolated_env):
    from app.storage.json_store import store

    store.vgmdb_mapping.write({
        "mb-rel-1": {"vgmdb_id": "1", "artist": "A", "album": "B", "folder": "", "source": "manual"},
    })

    r = client.get("/api/v1/mapping/export", auth=auth)
    assert r.status_code == 200
    exported = r.json()
    assert exported["count"] == 1

    files = {"file": ("backup.json", json.dumps({
        "mappings": {
            "mb-rel-1": {"vgmdb_id": "999", "artist": "A", "album": "B", "source": "import"},
            "mb-rel-2": {"vgmdb_id": "2", "artist": "C", "album": "D"},
        },
    }), "application/json")}
    r = client.post("/api/v1/mapping/import?mode=merge", files=files, auth=auth)
    result = r.json()
    assert result["added"] == 1 and result["updated"] == 1 and result["total_after"] == 2


def test_import_replace_mode_drops_unlisted_entries(client: TestClient, auth, isolated_env):
    from app.storage.json_store import store

    store.vgmdb_mapping.write({
        "mb-rel-1": {"vgmdb_id": "1", "artist": "A", "album": "B", "folder": "", "source": "manual"},
        "mb-rel-2": {"vgmdb_id": "2", "artist": "C", "album": "D", "folder": "", "source": "manual"},
    })
    files = {"file": ("backup.json", json.dumps({"mb-rel-2": {"vgmdb_id": "2"}}), "application/json")}
    r = client.post("/api/v1/mapping/import?mode=replace", files=files, auth=auth)
    result = r.json()
    assert result["removed"] == 1 and result["total_after"] == 1


def test_import_dry_run_does_not_write(client: TestClient, auth, isolated_env):
    before = client.get("/api/v1/mapping", auth=auth).json()
    files = {"file": ("backup.json", json.dumps({"mappings": {"mb-rel-9": {"vgmdb_id": "1"}}}), "application/json")}
    client.post("/api/v1/mapping/import?mode=merge&dry_run=true", files=files, auth=auth)
    after = client.get("/api/v1/mapping", auth=auth).json()
    assert before == after


def test_import_malformed_json_rejected(client: TestClient, auth):
    files = {"file": ("bad.json", b"not json", "application/json")}
    r = client.post("/api/v1/mapping/import", files=files, auth=auth)
    assert r.status_code == 400


def test_import_skips_invalid_rows(client: TestClient, auth):
    files = {"file": ("mixed.json", json.dumps({
        "good": {"vgmdb_id": "1"}, "bad": {"no_vgmdb": True}, "also_bad": "not a dict",
    }), "application/json")}
    r = client.post("/api/v1/mapping/import?mode=merge", files=files, auth=auth)
    assert r.json()["skipped_invalid"] == 2


# ── excluded artists ──────────────────────────────────────────────────────
def test_excluded_artists_seeded_by_default(client: TestClient, auth):
    r = client.get("/api/v1/mapping/excluded-artists", auth=auth)
    assert "Linkin Park" in r.json()


def test_add_and_remove_excluded_artist(client: TestClient, auth):
    r = client.post("/api/v1/mapping/excluded-artists", json={"artist": "Test Band"}, auth=auth)
    assert r.json()["changed"] is True

    r = client.post("/api/v1/mapping/excluded-artists", json={"artist": "test band"}, auth=auth)
    assert r.json()["changed"] is False  # case-insensitive dedup

    r = client.delete("/api/v1/mapping/excluded-artists/Test%20Band", auth=auth)
    assert r.json()["changed"] is True

    r = client.delete("/api/v1/mapping/excluded-artists/Nobody", auth=auth)
    assert r.json()["changed"] is False


def test_excluded_artist_removed_from_unmapped(client: TestClient, auth, isolated_env):
    from app.storage.json_store import store

    store.album_list.write({
        "Meteora": {"artist": "Linkin Park", "album": "Meteora", "mb_release_id": "", "folder": "Meteora"},
        "Album": {"artist": "Some Artist", "album": "Album", "mb_release_id": "", "folder": "Album"},
    })
    r = client.get("/api/v1/mapping/unmapped", auth=auth)
    artists = [row["artist"] for row in r.json()]
    assert "Linkin Park" not in artists and "Some Artist" in artists

    client.delete("/api/v1/mapping/excluded-artists/Linkin%20Park", auth=auth)
    r = client.get("/api/v1/mapping/unmapped", auth=auth)
    artists = [row["artist"] for row in r.json()]
    assert "Linkin Park" in artists


# ── skipped albums ────────────────────────────────────────────────────────
def test_skip_and_restore(client: TestClient, auth):
    r = client.put("/api/v1/mapping/mb-rel-skip-1",
                    json={"vgmdb_id": "skip", "artist": "X", "album": "Y"}, auth=auth)
    assert r.status_code == 200

    r = client.get("/api/v1/mapping/skipped", auth=auth)
    assert len(r.json()) == 1 and r.json()[0]["mb_release_id"] == "mb-rel-skip-1"

    # a normal mapping shouldn't show up in the skipped list
    client.put("/api/v1/mapping/mb-rel-normal", json={"vgmdb_id": "42"}, auth=auth)
    r = client.get("/api/v1/mapping/skipped", auth=auth)
    assert len(r.json()) == 1

    r = client.delete("/api/v1/mapping/mb-rel-skip-1", auth=auth)
    assert r.json()["deleted"] is True
    r = client.get("/api/v1/mapping/skipped", auth=auth)
    assert len(r.json()) == 0
