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


def test_mappings_page_has_bulk_select_ui(client: TestClient, auth, isolated_env):
    from app.storage.json_store import store

    store.album_list.write({
        "Album1": {"artist": "Artist A", "album": "Album 1", "mb_release_id": "mb-1", "folder": "Artist A/Album 1"},
    })

    r = client.get("/mappings", auth=auth)
    assert r.status_code == 200
    text = r.text
    assert 'id="select-all-unmapped"' in text
    assert 'id="bulk-toolbar"' in text
    assert 'id="bulk-skip-btn"' in text
    assert 'id="bulk-exclude-btn"' in text
    assert 'class="js-row-select"' in text


def test_mappings_page_disables_checkbox_for_rows_without_mb_release_id(client: TestClient, auth, isolated_env):
    from app.storage.json_store import store

    store.album_list.write({
        "NoMbId": {"artist": "X", "album": "Y", "mb_release_id": "", "folder": "X/Y"},
    })
    r = client.get("/mappings", auth=auth)
    assert 'class="js-row-select" disabled' in r.text


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


# ── bulk actions ──────────────────────────────────────────────────────────
def test_bulk_skip(client: TestClient, auth):
    r = client.post("/api/v1/mapping/bulk-skip", json={"items": [
        {"mb_release_id": "mb-1", "artist": "Artist A", "album": "Album 1"},
        {"mb_release_id": "mb-2", "artist": "Artist A", "album": "Album 2"},
    ]}, auth=auth)
    assert r.status_code == 200
    data = r.json()
    assert set(data["skipped"]) == {"mb-1", "mb-2"}
    assert data["failed"] == []

    r = client.get("/api/v1/mapping/skipped", auth=auth)
    assert len(r.json()) == 2


def test_bulk_skip_partial_failure_does_not_block_the_rest(client: TestClient, auth):
    r = client.post("/api/v1/mapping/bulk-skip", json={"items": [
        {"mb_release_id": "mb-1", "artist": "A", "album": "B"},
        {"mb_release_id": "", "artist": "Bad"},  # invalid — empty id
        {"mb_release_id": "mb-2", "artist": "C", "album": "D"},
    ]}, auth=auth)
    data = r.json()
    assert set(data["skipped"]) == {"mb-1", "mb-2"}
    assert len(data["failed"]) == 1
    assert data["failed"][0]["mb_release_id"] == ""


def test_bulk_skip_empty_items_is_a_no_op(client: TestClient, auth):
    r = client.post("/api/v1/mapping/bulk-skip", json={"items": []}, auth=auth)
    assert r.status_code == 200
    assert r.json() == {"skipped": [], "failed": []}


def test_bulk_exclude_artists(client: TestClient, auth):
    r = client.post("/api/v1/mapping/bulk-exclude-artists", json={
        "artists": ["Artist X", "Artist Y"],
    }, auth=auth)
    assert r.status_code == 200
    data = r.json()
    assert set(data["added"]) == {"Artist X", "Artist Y"}
    assert data["already_excluded"] == []

    r = client.get("/api/v1/mapping/excluded-artists", auth=auth)
    assert "Artist X" in r.json() and "Artist Y" in r.json()


def test_bulk_exclude_artists_dedupes_case_insensitively(client: TestClient, auth):
    r = client.post("/api/v1/mapping/bulk-exclude-artists", json={
        "artists": ["Artist X", "artist x", "ARTIST X", "  Artist X  "],
    }, auth=auth)
    data = r.json()
    assert data["added"] == ["Artist X"]  # only added once


def test_bulk_exclude_artists_reports_already_excluded_not_as_error(client: TestClient, auth):
    client.post("/api/v1/mapping/bulk-exclude-artists", json={"artists": ["Artist X"]}, auth=auth)

    r = client.post("/api/v1/mapping/bulk-exclude-artists", json={"artists": ["Artist X", "Artist Y"]}, auth=auth)
    data = r.json()
    assert data["added"] == ["Artist Y"]
    assert data["already_excluded"] == ["Artist X"]


def test_bulk_exclude_artists_ignores_blank_entries(client: TestClient, auth):
    r = client.post("/api/v1/mapping/bulk-exclude-artists", json={"artists": ["", "   ", "Real Artist"]}, auth=auth)
    data = r.json()
    assert data["added"] == ["Real Artist"]
