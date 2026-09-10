"""app.core.tag_locks — per-field locks against enrichment overwriting a
tag, plus the snapshot/restore pair BeetsEnricher calls around a beet
import.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

import app.core.tag_fields as tf
from app.core.tag_locks import TagLockService
from app.storage.json_store import store


# ── CRUD ──────────────────────────────────────────────────────────────────
def test_get_locks_empty_by_default(isolated_env):
    assert TagLockService().get_locks("Some/Folder") == []


def test_set_lock_true_then_false_round_trips(isolated_env):
    svc = TagLockService()
    assert svc.set_lock("Some/Folder", "genre", True) == ["genre"]
    assert svc.get_locks("Some/Folder") == ["genre"]

    assert svc.set_lock("Some/Folder", "genre", False) == []
    assert svc.get_locks("Some/Folder") == []
    # An empty lock list drops the folder entry entirely, mirroring
    # field_overrides.set_overrides' "no fields left -> drop the entry".
    assert "Some/Folder" not in store.locked_fields.read()


def test_set_lock_does_not_duplicate_or_error_when_already_set(isolated_env):
    svc = TagLockService()
    svc.set_lock("Some/Folder", "genre", True)
    assert svc.set_lock("Some/Folder", "genre", True) == ["genre"]


def test_set_lock_supports_a_custom_field_name(isolated_env):
    svc = TagLockService()
    assert svc.set_lock("Some/Folder", "MyCustomTag", True) == ["MyCustomTag"]


def test_set_lock_rejects_locking_a_per_track_field(isolated_env):
    with pytest.raises(ValueError):
        TagLockService().set_lock("Some/Folder", "title", True)
    assert TagLockService().get_locks("Some/Folder") == []


def test_set_lock_unlocking_a_per_track_field_never_raises(isolated_env):
    """Unlocking must always succeed, even for a field that could never
    have been locked in the first place — e.g. cleaning up a stale entry
    from before this guard existed."""
    assert TagLockService().set_lock("Some/Folder", "title", False) == []


# ── snapshot / restore ────────────────────────────────────────────────────
def test_snapshot_is_noop_when_nothing_locked(isolated_env, tmp_path):
    with patch.object(tf, "first_audio_file") as mock_first:
        result = TagLockService().snapshot(tmp_path, "Some/Folder")

    assert result == {}
    mock_first.assert_not_called()


def test_snapshot_reads_locked_field_values_from_first_file(isolated_env, tmp_path):
    svc = TagLockService()
    svc.set_lock("Some/Folder", "genre", True)
    svc.set_lock("Some/Folder", "MyCustomTag", True)

    with patch.object(tf, "first_audio_file", return_value=tmp_path / "01.flac"), \
         patch.object(tf, "read_field_value", side_effect=lambda path, field: {
             "genre": "Rock", "MyCustomTag": "Value",
         }[field]):
        snapshot = svc.snapshot(tmp_path, "Some/Folder")

    assert snapshot == {"genre": "Rock", "MyCustomTag": "Value"}


def test_snapshot_empty_folder_returns_empty(isolated_env, tmp_path):
    svc = TagLockService()
    svc.set_lock("Some/Folder", "genre", True)
    assert svc.snapshot(tmp_path, "Some/Folder") == {}


def test_restore_writes_every_snapshotted_field_back(isolated_env, tmp_path):
    with patch.object(tf, "write_album_field", return_value=2) as mock_write:
        applied = TagLockService().restore(tmp_path, {"genre": "Rock", "label": None})

    assert applied == 4  # 2 + 2
    mock_write.assert_any_call(tmp_path, "genre", "Rock")
    mock_write.assert_any_call(tmp_path, "label", "")


def test_restore_empty_snapshot_is_noop(isolated_env, tmp_path):
    with patch.object(tf, "write_album_field") as mock_write:
        assert TagLockService().restore(tmp_path, {}) == 0
    mock_write.assert_not_called()


def test_restore_skips_a_stale_per_track_lock_instead_of_raising(isolated_env, tmp_path):
    """set_lock refuses to create one of these, but a lock file from
    before that guard existed could still have one — enrichment must
    never crash over it."""
    def _fake_write(album_dir, field, value):  # noqa: ARG001
        if field == "title":
            raise ValueError("'title' is a per-track field")
        return 1

    with patch.object(tf, "write_album_field", side_effect=_fake_write):
        applied = TagLockService().restore(tmp_path, {"title": "Old Title", "genre": "Rock"})

    assert applied == 1


# ── per-track locks (title / track artist) ──────────────────────────────────
def test_get_track_locks_empty_by_default(isolated_env):
    assert TagLockService().get_track_locks("Some/Folder") == []


def test_set_track_lock_true_then_false_round_trips(isolated_env):
    svc = TagLockService()
    assert svc.set_track_lock("Some/Folder", "title", True) == ["title"]
    assert svc.get_track_locks("Some/Folder") == ["title"]

    assert svc.set_track_lock("Some/Folder", "title", False) == []
    assert svc.get_track_locks("Some/Folder") == []
    assert "Some/Folder" not in store.locked_track_fields.read()


def test_set_track_lock_rejects_field_outside_title_or_artist(isolated_env):
    with pytest.raises(ValueError):
        TagLockService().set_track_lock("Some/Folder", "genre", True)
    assert TagLockService().get_track_locks("Some/Folder") == []


def test_set_track_lock_unlocking_an_invalid_field_never_raises(isolated_env):
    assert TagLockService().set_track_lock("Some/Folder", "genre", False) == []


def test_set_track_lock_does_not_duplicate_when_already_set(isolated_env):
    svc = TagLockService()
    svc.set_track_lock("Some/Folder", "artist", True)
    assert svc.set_track_lock("Some/Folder", "artist", True) == ["artist"]


def test_snapshot_tracks_is_noop_when_nothing_locked(isolated_env, tmp_path):
    with patch.object(tf, "read_track_field_by_position") as mock_read:
        result = TagLockService().snapshot_tracks(tmp_path, "Some/Folder")
    assert result == {}
    mock_read.assert_not_called()


def test_snapshot_tracks_reads_locked_fields_by_position(isolated_env, tmp_path):
    svc = TagLockService()
    svc.set_track_lock("Some/Folder", "title", True)

    with patch.object(tf, "read_track_field_by_position",
                       return_value={("1", "1"): "Track One"}) as mock_read:
        snapshot = svc.snapshot_tracks(tmp_path, "Some/Folder")

    mock_read.assert_called_once_with(tmp_path, "title")
    assert snapshot == {"title": {("1", "1"): "Track One"}}


def test_restore_tracks_writes_every_locked_field_by_position(isolated_env, tmp_path):
    with patch.object(tf, "write_track_field_by_position", return_value=3) as mock_write:
        applied = TagLockService().restore_tracks(
            tmp_path, {"title": {("1", "1"): "Track One"}, "artist": {("1", "1"): "Someone"}},
        )
    assert applied == 6  # 3 + 3
    mock_write.assert_any_call(tmp_path, "title", {("1", "1"): "Track One"})
    mock_write.assert_any_call(tmp_path, "artist", {("1", "1"): "Someone"})


def test_restore_tracks_empty_snapshot_is_noop(isolated_env, tmp_path):
    with patch.object(tf, "write_track_field_by_position") as mock_write:
        assert TagLockService().restore_tracks(tmp_path, {}) == 0
    mock_write.assert_not_called()
