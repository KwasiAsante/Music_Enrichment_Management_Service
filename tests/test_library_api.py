"""app.api.library — filtering, grouping, cover art, and the album
detail endpoint.
"""

from __future__ import annotations

from fastapi.testclient import TestClient


def _seed_albums(store):
    store.album_list.write({
        "Album1": {"artist": "Artist A", "album": "Album 1", "mb_release_id": "mb-1", "folder": "Artist A/Album 1"},
        "Album2": {"artist": "Artist A", "album": "Album 2", "mb_release_id": "mb-2", "folder": "Artist A/Album 2"},
        "Album3": {"artist": "Artist B", "album": "Album 1", "mb_release_id": "mb-3", "folder": "Artist B/Album 1"},
        "Album4": {"artist": "Artist C", "album": "Album 1", "mb_release_id": "", "folder": "Artist C/Album 1"},
    })
    store.vgmdb_mapping.write({
        "mb-1": {"vgmdb_id": "1", "artist": "Artist A", "album": "Album 1", "folder": "", "source": "manual"},
        "mb-2": {"vgmdb_id": "2", "artist": "Artist A", "album": "Album 2", "folder": "", "source": "search_catalog"},
        "mb-3": {"vgmdb_id": "3", "artist": "Artist B", "album": "Album 1", "folder": "", "source": "import"},
    })


def test_source_filter_manual(client: TestClient, auth, isolated_env):
    from app.storage.json_store import store
    _seed_albums(store)
    r = client.get("/api/v1/library/albums", params={"source": "manual"}, auth=auth)
    data = r.json()
    assert data["total"] == 1 and data["albums"][0]["mb_release_id"] == "mb-1"


def test_source_filter_auto_matches_search_prefixed(client: TestClient, auth, isolated_env):
    from app.storage.json_store import store
    _seed_albums(store)
    r = client.get("/api/v1/library/albums", params={"source": "auto"}, auth=auth)
    data = r.json()
    assert data["total"] == 1 and data["albums"][0]["mb_release_id"] == "mb-2"


def test_folder_filter_matches_folder_path(client: TestClient, auth, isolated_env):
    from app.storage.json_store import store
    _seed_albums(store)
    r = client.get("/api/v1/library/albums", params={"folder": "Artist B"}, auth=auth)
    assert r.json()["total"] == 1


def test_folder_filter_matches_album_name(client: TestClient, auth, isolated_env):
    from app.storage.json_store import store
    _seed_albums(store)
    r = client.get("/api/v1/library/albums", params={"folder": "Album 2"}, auth=auth)
    data = r.json()
    assert data["total"] == 1
    assert data["albums"][0]["mb_release_id"] == "mb-2"


def test_unmapped_filter_has_no_mapping_source(client: TestClient, auth, isolated_env):
    from app.storage.json_store import store
    _seed_albums(store)
    r = client.get("/api/v1/library/albums", params={"unmapped": "true"}, auth=auth)
    data = r.json()
    assert data["total"] == 1
    assert data["albums"][0]["mapping_source"] is None


def test_grouped_view_buckets_by_artist(client: TestClient, auth, isolated_env):
    from app.storage.json_store import store
    _seed_albums(store)
    r = client.get("/api/v1/library/albums/grouped", auth=auth)
    data = r.json()
    assert data["total"] == 4 and data["total_artists"] == 3
    by_artist = {g["artist"]: g["count"] for g in data["groups"]}
    assert by_artist["Artist A"] == 2
    assert by_artist["Artist B"] == 1


def test_grouped_view_respects_filters(client: TestClient, auth, isolated_env):
    from app.storage.json_store import store
    _seed_albums(store)
    r = client.get("/api/v1/library/albums/grouped", params={"source": "manual"}, auth=auth)
    data = r.json()
    assert data["total"] == 1 and data["total_artists"] == 1


# ── cover art ────────────────────────────────────────────────────────────
def test_art_endpoint_front_and_back(client: TestClient, auth, isolated_env):
    music_dir = isolated_env.music_dir
    album_dir = music_dir / "synced_music" / "Artist" / "Test Artist" / "Test Album"
    album_dir.mkdir(parents=True)
    (album_dir / "cover.jpg").write_bytes(b"FRONTFILE")
    (album_dir / "back.png").write_bytes(b"BACKFILE")

    r = client.get("/api/v1/library/art", params={"folder": "Test Artist/Test Album", "side": "front"}, auth=auth)
    assert r.status_code == 200 and r.content == b"FRONTFILE"

    r = client.get("/api/v1/library/art", params={"folder": "Test Artist/Test Album", "side": "back"}, auth=auth)
    assert r.status_code == 200 and r.content == b"BACKFILE"


def test_art_endpoint_404_when_missing(client: TestClient, auth, isolated_env):
    r = client.get("/api/v1/library/art", params={"folder": "Nope/Nope"}, auth=auth)
    assert r.status_code == 404


def test_art_endpoint_rejects_path_traversal(client: TestClient, auth, isolated_env):
    r = client.get("/api/v1/library/art", params={"folder": "../../../../etc/passwd"}, auth=auth)
    assert r.status_code == 400


# ── album detail ─────────────────────────────────────────────────────────
def test_album_detail_endpoint(client: TestClient, auth, isolated_env):
    from app.storage.json_store import store
    from app.core.vgmdb_client import VGMDBClient
    from app.core.mb_client import MBClient
    from unittest.mock import patch

    store.album_list.write({
        "Album1": {"artist": "Test Artist", "album": "Test Album", "mb_release_id": "mb-1", "folder": "Test Artist/Test Album"},
    })
    store.vgmdb_mapping.write({
        "mb-1": {"vgmdb_id": "555", "artist": "Test Artist", "album": "Test Album", "folder": "", "source": "manual"},
    })

    # Mocked so this test is fast and deterministic, not dependent on any
    # real network reachability — the aggregator's own resilience to a
    # real VGMDB/MB outage is covered separately in test_album_details.py.
    with patch.object(VGMDBClient, "get_album", return_value=None), \
         patch.object(MBClient, "get_release", return_value=None):
        r = client.get("/api/v1/library/albums/detail", params={"folder": "Test Artist/Test Album"}, auth=auth)

    assert r.status_code == 200
    data = r.json()
    assert data["artist"] == "Test Artist"
    assert data["vgmdb_id"] == "555"
    assert data["mapped"] is True


def test_album_detail_404_for_unknown_folder(client: TestClient, auth, isolated_env):
    r = client.get("/api/v1/library/albums/detail", params={"folder": "Nope/Nope"}, auth=auth)
    assert r.status_code == 404


def test_library_api_intentionally_open_no_auth_required(client: TestClient):
    # /api/v1/* (except /api/v1/settings/*) is deliberately unauthenticated
    # — see README's "Auth" section for why (Lidarr/Picard scripts call
    # these directly with no browser involved and can't answer a login
    # prompt). This isn't an oversight; asserting it stays true on
    # purpose so a future change can't silently lock those integrations
    # out — or silently remove the protection from settings.
    r = client.get("/api/v1/library/albums")
    assert r.status_code == 200


def test_settings_api_does_require_auth(client: TestClient):
    r = client.get("/api/v1/settings")
    assert r.status_code == 401


# ── Library page bulk-select UI ─────────────────────────────────────────────
def test_library_page_has_bulk_select_ui(client: TestClient, auth, isolated_env):
    from app.storage.json_store import store

    store.album_list.write({
        "Album1": {"artist": "Artist A", "album": "Album 1", "mb_release_id": "mb-1", "folder": "Artist A/Album 1"},
        "Album2": {"artist": "Artist B", "album": "Album 2", "mb_release_id": "", "folder": "Artist B/Album 2"},
    })

    r = client.get("/library", auth=auth)
    assert r.status_code == 200
    text = r.text
    assert 'id="select-all-library"' in text
    assert 'id="bulk-toolbar"' in text
    assert 'id="bulk-reenrich-btn"' in text
    assert 'id="bulk-exclude-btn"' in text
    assert text.count('class="js-row-select"') == 2
    assert 'data-folder="Artist A/Album 1"' in text
    # every checkbox needs the stopPropagation guard, since .result-card
    # is an <a> here (unlike Mappings' plain <div> rows) — without it,
    # checking a box would also navigate away.
    assert text.count('onclick="event.stopPropagation()"') == 2


def test_library_skipped_view_has_no_bulk_select_ui(client: TestClient, auth):
    r = client.get("/library?view=skipped", auth=auth)
    assert 'id="select-all-library"' not in r.text
    assert 'class="js-row-select"' not in r.text
