"""app.api.field_overrides — /api/v1/overrides/* CRUD, plus the
GET /api/v1/library/albums?q= free-text search this page's search box
relies on.
"""

from __future__ import annotations

from unittest.mock import patch

from fastapi.testclient import TestClient

from app.core.vgmdb_client import VGMDBClient
from app.storage.json_store import store


def _seed() -> None:
    store.album_list.write({
        "TestAlbum": {
            "artist": "Real Band Name", "album": "Test OST",
            "mb_release_id": "mb-1", "folder": "Real Band Name/Test OST",
        },
    })
    store.vgmdb_mapping.write({
        "mb-1": {
            "vgmdb_id": "999", "artist": "Real Band Name", "album": "Test OST",
            "folder": "Real Band Name/Test OST", "source": "manual",
        },
    })


# ── GET /overrides/options ──────────────────────────────────────────────────
def test_options_unknown_folder_404(client: TestClient, auth):
    r = client.get("/api/v1/overrides/options", params={"folder": "Nope/Nope"}, auth=auth)
    assert r.status_code == 404


def test_options_returns_candidates_without_touching_the_network(client: TestClient, auth, isolated_env):
    _seed()
    with patch.object(VGMDBClient, "get_album", return_value={
        "catalog": "ABC-001", "category": "OST",
        "performers": [{"names": {"en": "Real Band Name"}}],
        "composers": [], "arrangers": [], "lyricists": [],
        "publisher": {"names": {"en": "Square Enix"}},
    }):
        r = client.get("/api/v1/overrides/options",
                        params={"folder": "Real Band Name/Test OST"}, auth=auth)
    assert r.status_code == 200
    data = r.json()
    assert data["mapped"] is True
    artist_field = next(f for f in data["fields"] if f["field"] == "artist")
    assert any(c["value"] == "Real Band Name" for c in artist_field["candidates"])


# ── PUT / DELETE /overrides ─────────────────────────────────────────────────
def test_set_get_and_clear_round_trip(client: TestClient, auth, isolated_env):
    _seed()
    with patch.object(VGMDBClient, "get_album", return_value=None):
        r = client.put(
            "/api/v1/overrides", params={"folder": "Real Band Name/Test OST"},
            json={"fields": {"artist": "Real Band Name"}}, auth=auth,
        )
        assert r.status_code == 200
        assert r.json()["fields"] == {"artist": "Real Band Name"}

        r = client.get("/api/v1/overrides/options",
                        params={"folder": "Real Band Name/Test OST"}, auth=auth)
    artist_field = next(f for f in r.json()["fields"] if f["field"] == "artist")
    assert artist_field["override_value"] == "Real Band Name"

    r = client.delete("/api/v1/overrides", params={"folder": "Real Band Name/Test OST"}, auth=auth)
    assert r.json()["deleted"] is True

    r = client.delete("/api/v1/overrides", params={"folder": "Real Band Name/Test OST"}, auth=auth)
    assert r.json()["deleted"] is False


def test_set_overrides_unknown_field_is_400(client: TestClient, auth, isolated_env):
    _seed()
    r = client.put(
        "/api/v1/overrides", params={"folder": "Real Band Name/Test OST"},
        json={"fields": {"bogus": "x"}}, auth=auth,
    )
    assert r.status_code == 400


def test_set_overrides_unknown_album_is_404(client: TestClient, auth):
    r = client.put(
        "/api/v1/overrides", params={"folder": "Nope/Nope"},
        json={"fields": {"artist": "x"}}, auth=auth,
    )
    assert r.status_code == 404


# ── GET /library/albums?q= (this page's search box) ─────────────────────────
def test_library_albums_q_search_ors_across_artist_album_folder(client: TestClient, auth, isolated_env):
    store.album_list.write({
        "A": {"artist": "Real Band Name", "album": "First OST",
              "mb_release_id": "", "folder": "Real Band Name/First OST"},
        "B": {"artist": "Other Artist", "album": "Real Band Name Tribute",
              "mb_release_id": "", "folder": "Other Artist/Tribute"},
        "C": {"artist": "Unrelated", "album": "Nothing",
              "mb_release_id": "", "folder": "Unrelated/Nothing"},
    })
    r = client.get("/api/v1/library/albums", params={"q": "real band name"}, auth=auth)
    folders = {a["folder"] for a in r.json()["albums"]}
    assert folders == {"Real Band Name/First OST", "Other Artist/Tribute"}


def test_field_overrides_page_renders(client: TestClient, auth):
    r = client.get("/overrides", auth=auth)
    assert r.status_code == 200
    assert 'id="search-input"' in r.text
    assert 'id="editor-section"' in r.text
