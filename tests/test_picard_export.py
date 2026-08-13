"""Tests for PicardExporter path resolution."""

from __future__ import annotations

from pathlib import Path

from app.core.picard_export import PicardExporter


def _make_artist_tree(music_dir: Path, artist: str, album: str, disc: str) -> Path:
    disc_dir = (
        music_dir / "synced_music" / "Artist" / artist / album / disc
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

    assert resolved == isolated_env.music_dir / "synced_music" / "Artist" / "Capcom Sound Team"


def test_resolve_artist_folder_from_foreign_host_path(isolated_env) -> None:
    _make_artist_tree(
        isolated_env.music_dir,
        "Capcom Sound Team",
        "DEVIL MAY CRY 5 ORIGINAL SOUNDTRACK",
        "CD 01",
    )

    picard_path = (
        "/storage/synced_music/Artist/Capcom Sound Team/"
        "DEVIL MAY CRY 5 ORIGINAL SOUNDTRACK/CD 01"
    )

    exporter = PicardExporter()
    resolved = exporter._resolve_artist_folder(picard_path)

    assert resolved == isolated_env.music_dir / "synced_music" / "Artist" / "Capcom Sound Team"


def test_resolve_artist_folder_from_bare_artist_name(isolated_env) -> None:
    _make_artist_tree(
        isolated_env.music_dir,
        "Capcom Sound Team",
        "Some Album",
        "CD 01",
    )

    exporter = PicardExporter()
    resolved = exporter._resolve_artist_folder("Capcom Sound Team")

    assert resolved == isolated_env.music_dir / "synced_music" / "Artist" / "Capcom Sound Team"
