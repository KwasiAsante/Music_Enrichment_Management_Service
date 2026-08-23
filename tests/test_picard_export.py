"""Tests for PicardExporter path resolution."""

from __future__ import annotations

from pathlib import Path

from app.config import settings
from app.core.picard_export import PicardExporter


def _make_artist_tree(music_dir: Path, artist: str, album: str, disc: str) -> Path:
    disc_dir = (
        music_dir / "Artist" / artist / album / disc
    )
    disc_dir.mkdir(parents=True)
    (disc_dir / "01-track.flac").write_bytes(b"")
    return disc_dir


def test_resolve_artist_folder_from_disc_subfolder(isolated_env) -> None:
    disc_dir = _make_artist_tree(
        isolated_env.music_dir,
        "Capcom Sound Team",
        "DEVIL MAY CRY 5 ORIGINAL SOUNDTRACK",
        "CD 01",
    )

    exporter = PicardExporter()
    resolved = exporter._resolve_artist_folder(disc_dir)

    assert resolved == isolated_env.music_dir / "Artist" / "Capcom Sound Team"


def test_resolve_artist_folder_from_foreign_host_path(isolated_env) -> None:
    _make_artist_tree(
        isolated_env.music_dir,
        "Capcom Sound Team",
        "DEVIL MAY CRY 5 ORIGINAL SOUNDTRACK",
        "CD 01",
    )

    picard_path = (
        "/storage/Artist/Capcom Sound Team/"
        "DEVIL MAY CRY 5 ORIGINAL SOUNDTRACK/CD 01"
    )

    exporter = PicardExporter()
    resolved = exporter._resolve_artist_folder(picard_path)

    assert resolved == isolated_env.music_dir / "Artist" / "Capcom Sound Team"


def test_resolve_artist_folder_from_bare_artist_name(isolated_env) -> None:
    _make_artist_tree(
        isolated_env.music_dir,
        "Capcom Sound Team",
        "Some Album",
        "CD 01",
    )

    exporter = PicardExporter()
    resolved = exporter._resolve_artist_folder("Capcom Sound Team")

    assert resolved == isolated_env.music_dir / "Artist" / "Capcom Sound Team"


def test_resolve_artist_folder_honours_custom_artist_root_subpath(
    isolated_env, monkeypatch,
) -> None:
    """The library layout is configurable via ARTIST_ROOT_SUBPATH — none
    of PicardExporter's path handling should assume "synced_music"."""
    monkeypatch.setattr(settings, "artist_root_subpath", "Library/ByArtist")

    disc_dir = (
        isolated_env.music_dir / "Library" / "ByArtist" / "Capcom Sound Team"
        / "Some Album" / "CD 01"
    )
    disc_dir.mkdir(parents=True)
    (disc_dir / "01-track.flac").write_bytes(b"")

    exporter = PicardExporter()
    assert exporter.artist_root == isolated_env.music_dir / "Library" / "ByArtist"

    resolved = exporter._resolve_artist_folder(disc_dir)
    assert resolved == isolated_env.music_dir / "Library" / "ByArtist" / "Capcom Sound Team"

    # Same foreign-host-path tail matching as the synced_music tests above,
    # just with the configured segment names.
    picard_path = "/storage/Library/ByArtist/Capcom Sound Team/Some Album/CD 01"
    resolved = exporter._resolve_artist_folder(picard_path)
    assert resolved == isolated_env.music_dir / "Library" / "ByArtist" / "Capcom Sound Team"
