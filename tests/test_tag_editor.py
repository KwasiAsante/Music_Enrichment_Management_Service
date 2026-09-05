"""app.core.tag_editor — album-level tag view/edit, including custom
tags, with immediate (not deferred-to-next-enrichment) writes.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

import app.core.tag_fields as tf
from app.core.tag_editor import TagEditorService
from app.storage.json_store import store


def _seed_album(isolated_env) -> None:
    store.album_list.write({
        "TestAlbum": {
            "artist": "Real Band Name", "album": "Test OST",
            "mb_release_id": "mb-1", "folder": "Real Band Name/Test OST",
        },
    })
    album_dir = isolated_env.music_dir / "Artist" / "Real Band Name" / "Test OST"
    album_dir.mkdir(parents=True)
    (album_dir / "01.flac").touch()


# ── get_album_tags ────────────────────────────────────────────────────────
def test_get_album_tags_unknown_folder_raises(isolated_env):
    with pytest.raises(ValueError):
        TagEditorService().get_album_tags("Nope/Nope")


def test_get_album_tags_marks_source_and_locked(isolated_env):
    _seed_album(isolated_env)
    with patch.object(tf, "read_album_fields", return_value={
        "genre": "Rock", "artist": None, "MyCustomTag": "Value",
        "musicbrainz_albumid": "abc-123", "vgmdb_id": "999",
    }), patch("app.core.tag_editor.TagLockService") as mock_lock_cls:
        mock_lock_cls.return_value.get_locks.return_value = ["genre"]
        view = TagEditorService().get_album_tags("Real Band Name/Test OST")

    assert view["artist"] == "Real Band Name"
    assert view["album"] == "Test OST"

    by_name = {f["name"]: f for f in view["fields"]}
    assert by_name["genre"] == {"name": "genre", "value": "Rock", "locked": True, "source": "standard"}
    assert by_name["MyCustomTag"]["source"] == "custom"
    assert by_name["MyCustomTag"]["locked"] is False
    assert by_name["musicbrainz_albumid"]["source"] == "musicbrainz"
    assert by_name["vgmdb_id"]["source"] == "vgmdb"


def test_get_album_tags_warns_when_no_audio_files_readable(isolated_env):
    store.album_list.write({
        "TestAlbum": {
            "artist": "A", "album": "B",
            "mb_release_id": "", "folder": "A/B",
        },
    })
    (isolated_env.music_dir / "Artist" / "A" / "B").mkdir(parents=True)

    view = TagEditorService().get_album_tags("A/B")
    assert any("Could not read tags" in w for w in view["warnings"])


# ── update_album_fields ───────────────────────────────────────────────────
def test_update_album_fields_unknown_folder_raises(isolated_env):
    with pytest.raises(ValueError):
        TagEditorService().update_album_fields("Nope/Nope", {"genre": "Rock"})


def test_update_album_fields_writes_via_tag_fields(isolated_env):
    _seed_album(isolated_env)
    with patch.object(tf, "write_album_field", return_value=1) as mock_write:
        touched = TagEditorService().update_album_fields(
            "Real Band Name/Test OST", {"genre": "Rock", "MyCustomTag": "Value"},
        )

    assert touched == 2
    album_dir = isolated_env.music_dir / "Artist" / "Real Band Name" / "Test OST"
    mock_write.assert_any_call(album_dir, "genre", "Rock")
    mock_write.assert_any_call(album_dir, "MyCustomTag", "Value")


def test_update_album_fields_strips_and_treats_blank_as_delete(isolated_env):
    _seed_album(isolated_env)
    album_dir = isolated_env.music_dir / "Artist" / "Real Band Name" / "Test OST"
    with patch.object(tf, "write_album_field", return_value=1) as mock_write:
        TagEditorService().update_album_fields("Real Band Name/Test OST", {"genre": "  "})

    mock_write.assert_called_once_with(album_dir, "genre", "")
