"""app.api.playlist — upload/convert/download flow and format validation."""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from app.config import settings


def _touch(root: Path, *parts: str) -> Path:
    p = root.joinpath(*parts)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.touch()
    return p


def _artist_root(isolated_env) -> Path:
    return isolated_env.music_dir / "Artist"


def test_convert_rejects_unsupported_extension(client: TestClient, auth):
    r = client.post(
        "/api/v1/playlist/convert",
        files={"file": ("playlist.pls", b"whatever", "text/plain")},
        auth=auth,
    )
    assert r.status_code == 400
    assert "unsupported playlist format" in r.json()["detail"]


def test_convert_matches_relocated_entry(client: TestClient, auth, isolated_env):
    root = _artist_root(isolated_env)
    _touch(root, "Artist A (Renamed)", "Album 1", "01 - Song.flac")

    content = b"/old/mount/Artist/Artist A/Album 1/01 - Song.flac\n"
    r = client.post(
        "/api/v1/playlist/convert",
        files={"file": ("old.m3u", content, "audio/x-mpegurl")},
        auth=auth,
    )
    assert r.status_code == 200
    data = r.json()
    assert data["total"] == 1
    assert data["matched"] == 1
    assert data["unmatched"] == 0
    assert data["entries"][0]["method"] == "relocated"
    assert data["entries"][0]["resolved_path"] == "Artist A (Renamed)/Album 1/01 - Song.flac"
    assert "Artist A (Renamed)" in data["playlist"]
    assert data["job_id"]


def test_convert_reports_unmatched_without_failing(client: TestClient, auth, isolated_env):
    root = _artist_root(isolated_env)
    _touch(root, "Artist A", "Album 1", "01.flac")

    content = b"/old/Nowhere/Nothing/99 - Gone.flac\n"
    r = client.post(
        "/api/v1/playlist/convert",
        files={"file": ("old.m3u8", content, "audio/x-mpegurl")},
        auth=auth,
    )
    assert r.status_code == 200
    data = r.json()
    assert data["matched"] == 0
    assert data["unmatched"] == 1
    assert data["entries"][0]["method"] == "unmatched"
    assert "# UNMATCHED:" in data["playlist"]


def test_convert_rejects_non_utf8_content(client: TestClient, auth):
    r = client.post(
        "/api/v1/playlist/convert",
        files={"file": ("bad.m3u", b"\xff\xfe\x00\xff not utf8", "text/plain")},
        auth=auth,
    )
    assert r.status_code == 400


def test_convert_includes_artist_root_and_extinf(client: TestClient, auth, isolated_env):
    root = _artist_root(isolated_env)
    _touch(root, "Artist A", "Album 1", "01 - Song.flac")

    content = (
        b"#EXTINF:100,Artist A - Song\n"
        b"Artist A/Album 1/01 - Song.flac\n"
    )
    r = client.post(
        "/api/v1/playlist/convert",
        files={"file": ("old.m3u", content, "audio/x-mpegurl")},
        auth=auth,
    )
    data = r.json()
    assert data["artist_root"] == str(root)
    assert data["entries"][0]["extinf"] == "#EXTINF:100,Artist A - Song"


# ── GET /playlist/album-tracks ──────────────────────────────────────────────

def test_album_tracks_lists_audio_files_sorted(client: TestClient, auth, isolated_env):
    root = _artist_root(isolated_env)
    _touch(root, "Artist A", "Album 1", "02 - B.flac")
    _touch(root, "Artist A", "Album 1", "01 - A.flac")
    (root / "Artist A" / "Album 1" / "cover.jpg").touch()

    r = client.get(
        "/api/v1/playlist/album-tracks",
        params={"folder": "Artist A/Album 1"},
        auth=auth,
    )
    assert r.status_code == 200
    data = r.json()
    assert data["folder"] == "Artist A/Album 1"
    assert [t["filename"] for t in data["tracks"]] == ["01 - A.flac", "02 - B.flac"]
    assert data["tracks"][0]["path"] == "Artist A/Album 1/01 - A.flac"
    assert data["tracks"][0]["relative_path"] == "01 - A.flac"
    assert data["suggested_path"] is None


def test_album_tracks_recurses_into_disc_subfolders(client: TestClient, auth, isolated_env):
    # Real-world regression: the album folder itself had no files, only
    # "Disc 1"/"Disc 2" subfolders — a non-recursive listing came back
    # empty and the manual-match picker had nothing to show.
    root = _artist_root(isolated_env)
    _touch(root, "Artist A", "Original Soundtrack", "Disc 1", "09 - Intense Battle.mp3")
    _touch(root, "Artist A", "Original Soundtrack", "Disc 2", "01 - Training.mp3")

    r = client.get(
        "/api/v1/playlist/album-tracks",
        params={"folder": "Artist A/Original Soundtrack"},
        auth=auth,
    )
    data = r.json()
    assert [t["relative_path"] for t in data["tracks"]] == [
        "Disc 1/09 - Intense Battle.mp3", "Disc 2/01 - Training.mp3",
    ]
    assert data["tracks"][0]["path"] == "Artist A/Original Soundtrack/Disc 1/09 - Intense Battle.mp3"


def test_album_tracks_suggests_a_match_for_original_path(client: TestClient, auth, isolated_env):
    root = _artist_root(isolated_env)
    _touch(root, "Artist A", "Album 1", "01 - Retitled.flac")

    r = client.get(
        "/api/v1/playlist/album-tracks",
        params={"folder": "Artist A/Album 1", "original_path": "old/somewhere/01.flac"},
        auth=auth,
    )
    data = r.json()
    assert data["suggested_path"] == "Artist A/Album 1/01 - Retitled.flac"


def test_album_tracks_no_suggestion_when_nothing_plausible(client: TestClient, auth, isolated_env):
    root = _artist_root(isolated_env)
    _touch(root, "Artist A", "Album 1", "01 - Unrelated.flac")

    r = client.get(
        "/api/v1/playlist/album-tracks",
        params={"folder": "Artist A/Album 1", "original_path": "old/somewhere/Completely Different Title.flac"},
        auth=auth,
    )
    assert r.json()["suggested_path"] is None


def test_album_tracks_rejects_path_traversal(client: TestClient, auth, isolated_env):
    r = client.get(
        "/api/v1/playlist/album-tracks",
        params={"folder": "../../etc"},
        auth=auth,
    )
    assert r.status_code == 400


def test_album_tracks_missing_folder_is_empty_list(client: TestClient, auth, isolated_env):
    r = client.get(
        "/api/v1/playlist/album-tracks",
        params={"folder": "Nope/Nope"},
        auth=auth,
    )
    assert r.status_code == 200
    assert r.json()["tracks"] == []


# ── POST /playlist/export-navidrome ──────────────────────────────────────────

def test_export_navidrome_rejects_when_unconfigured(client: TestClient, auth, isolated_env):
    r = client.post(
        "/api/v1/playlist/export-navidrome",
        json={"filename": "old.m3u", "entries": []},
        auth=auth,
    )
    assert r.status_code == 400
    assert "not configured" in r.json()["detail"]


def test_export_navidrome_rejects_unsupported_extension(
    client: TestClient, auth, isolated_env, monkeypatch,
):
    monkeypatch.setattr(settings, "navidrome_playlist_dir", str(isolated_env.music_dir / "Playlists"))
    r = client.post(
        "/api/v1/playlist/export-navidrome",
        json={"filename": "old.pls", "entries": []},
        auth=auth,
    )
    assert r.status_code == 400
    assert "unsupported playlist format" in r.json()["detail"]


def test_export_navidrome_writes_navidrome_mount_paths_and_creates_dir(
    client: TestClient, auth, isolated_env, monkeypatch,
):
    root = _artist_root(isolated_env)
    _touch(root, "Artist A", "Album 1", "01 - Song.flac")

    playlist_dir = isolated_env.music_dir / "synced_music" / "Playlists"
    monkeypatch.setattr(settings, "navidrome_playlist_dir", str(playlist_dir))
    assert not playlist_dir.exists()

    r = client.post(
        "/api/v1/playlist/export-navidrome",
        json={
            "filename": "old.m3u",
            "entries": [
                {
                    "original_path": "/old/Song.flac",
                    "extinf": "#EXTINF:100,Artist A - Song",
                    "resolved_path": "Artist A/Album 1/01 - Song.flac",
                },
                {"original_path": "/old/Gone.flac", "extinf": None, "resolved_path": None},
            ],
        },
        auth=auth,
    )
    assert r.status_code == 200
    data = r.json()
    assert data["total"] == 2
    assert data["written"] == 1
    assert data["path"] == str(playlist_dir / "old.m3u")

    # Default navidrome_music_path_prefix is "/music" — the entry is
    # written as Navidrome's own container-absolute path, not a path
    # relative to the playlist file (confirmed necessary against a real
    # instance — see app.core.playlist_converter.navidrome_absolute_path).
    written = (playlist_dir / "old.m3u").read_text()
    assert written.splitlines() == [
        "#EXTM3U",
        "#EXTINF:100,Artist A - Song",
        "/music/Artist/Artist A/Album 1/01 - Song.flac",
        "# UNMATCHED: /old/Gone.flac",
    ]


def test_export_navidrome_uses_custom_music_path_prefix(
    client: TestClient, auth, isolated_env, monkeypatch,
):
    root = _artist_root(isolated_env)
    _touch(root, "Artist A", "Album 1", "01 - Song.flac")

    playlist_dir = isolated_env.music_dir / "synced_music" / "Playlists"
    monkeypatch.setattr(settings, "navidrome_playlist_dir", str(playlist_dir))
    monkeypatch.setattr(settings, "navidrome_music_path_prefix", "/data/music")

    r = client.post(
        "/api/v1/playlist/export-navidrome",
        json={
            "filename": "old.m3u",
            "entries": [
                {"original_path": "/old/Song.flac", "extinf": None,
                 "resolved_path": "Artist A/Album 1/01 - Song.flac"},
            ],
        },
        auth=auth,
    )
    assert r.status_code == 200
    written = (playlist_dir / "old.m3u").read_text()
    assert "/data/music/Artist/Artist A/Album 1/01 - Song.flac" in written


def test_export_navidrome_resolves_relative_dir_against_app_music_dir(
    client: TestClient, auth, isolated_env, monkeypatch,
):
    # Regression: navidrome_playlist_dir must track app_music_dir rather
    # than being a fixed absolute path, so the same setting value works
    # whether app_music_dir is the Docker container's /music or a
    # different override in a local debug config.
    root = _artist_root(isolated_env)
    _touch(root, "Artist A", "Album 1", "01 - Song.flac")

    monkeypatch.setattr(settings, "navidrome_playlist_dir", "synced_music/Playlists")

    r = client.post(
        "/api/v1/playlist/export-navidrome",
        json={
            "filename": "old.m3u",
            "entries": [
                {"original_path": "/old/Song.flac", "extinf": None,
                 "resolved_path": "Artist A/Album 1/01 - Song.flac"},
            ],
        },
        auth=auth,
    )
    assert r.status_code == 200
    expected_dir = isolated_env.music_dir / "synced_music" / "Playlists"
    assert r.json()["path"] == str(expected_dir / "old.m3u")
    assert (expected_dir / "old.m3u").exists()


def test_export_navidrome_ignores_path_components_in_filename(
    client: TestClient, auth, isolated_env, monkeypatch,
):
    playlist_dir = isolated_env.music_dir / "synced_music" / "Playlists"
    monkeypatch.setattr(settings, "navidrome_playlist_dir", str(playlist_dir))

    r = client.post(
        "/api/v1/playlist/export-navidrome",
        json={"filename": "../../etc/evil.m3u", "entries": []},
        auth=auth,
    )
    assert r.status_code == 200
    assert r.json()["path"] == str(playlist_dir / "evil.m3u")
    assert (playlist_dir / "evil.m3u").exists()
