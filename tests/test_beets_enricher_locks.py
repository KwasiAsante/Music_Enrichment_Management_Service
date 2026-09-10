"""app.core.beets_enricher — tag-lock integration.

BeetsEnricher.enrich_album gained a snapshot/restore pair around the beet
import: snapshot every locked field's on-disk value before the import
runs (which is what actually calls beet and could overwrite it), then
restore those values afterward, once the non-Latin-tag fix and field
overrides have also had their turn. This file exercises only that
integration point: snapshot happens before the import, restore happens
after everything else, and the result surfaces as
EnrichAlbumResult.fields_locked. See test_beets_enricher_overrides.py for
the sibling field_overrides integration this mirrors.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import app.core.beets_enricher as be
from app.core.beets_enricher import BeetsEnricher
from app.core.mb_link import LinkCheckResult
from app.storage import db
from app.storage.json_store import store


def _make_enricher(
    field_overrides_return: int = 0, tag_locks_snapshot: dict | None = None, tag_locks_restore: int = 0,
) -> tuple[BeetsEnricher, MagicMock, MagicMock]:
    field_overrides = MagicMock()
    field_overrides.apply_overrides.return_value = field_overrides_return
    tag_locks = MagicMock()
    tag_locks.snapshot.return_value = tag_locks_snapshot or {}
    tag_locks.restore.return_value = tag_locks_restore
    # Per-track locks (title/artist) — defaulted to "nothing locked, nothing
    # restored" so the arithmetic in enrich_album's success path
    # (fields_locked += tag_locks.restore_tracks(...)) stays a plain int,
    # not a MagicMock — see test_beets_enricher_track_locks.py for the
    # dedicated coverage of this pair.
    tag_locks.snapshot_tracks.return_value = {}
    tag_locks.restore_tracks.return_value = 0
    enricher = BeetsEnricher(
        mb=MagicMock(),
        mapper=MagicMock(),
        notifier=MagicMock(send=MagicMock(return_value=True)),
        mb_link=MagicMock(check=MagicMock(return_value=LinkCheckResult(
            has_link=True, vgmdb_url=None, existing_url=None, seed_url=None,
        ))),
        scanner=MagicMock(),
        field_overrides=field_overrides,
        tag_locks=tag_locks,
    )
    return enricher, field_overrides, tag_locks


def _make_album_folder(isolated_env) -> Path:
    folder = isolated_env.music_dir / "Artist" / "Some Artist" / "Some Album"
    folder.mkdir(parents=True)
    return folder


def test_success_path_snapshots_before_import_and_restores_after(isolated_env):
    db.init_db()
    album_folder = _make_album_folder(isolated_env)
    enricher, field_overrides, tag_locks = _make_enricher(
        field_overrides_return=0, tag_locks_snapshot={"genre": "Original Genre"}, tag_locks_restore=1,
    )
    store.vgmdb_mapping.write({
        "mb-1": {"vgmdb_id": "999", "artist": "Some Artist", "album": "Some Album",
                 "folder": "Some Artist/Some Album", "source": "manual"},
    })

    call_order: list[str] = []

    def _fake_snapshot(*_a, **_kw):
        call_order.append("snapshot")
        return {"genre": "Original Genre"}

    def _fake_subprocess_run(*_a, **_kw):
        call_order.append("beet_import")
        return MagicMock(returncode=0, stdout="Match (95.0%)\n", stderr="")

    tag_locks.snapshot.side_effect = _fake_snapshot
    with patch.object(be, "_subprocess_run", side_effect=_fake_subprocess_run):
        info = {"artist": "Some Artist", "album": "Some Album",
                "mb_release_id": "mb-1", "folder": "Some Artist/Some Album"}
        result = enricher.enrich_album(info)

    assert result["ok"] is True
    assert result["fields_locked"] == 1
    # Folder key comparison via Path rather than a literal string — the
    # underlying `str(album_folder.relative_to(artist_root))` uses the
    # OS-native separator, which is backslash on Windows.
    snapshot_args = tag_locks.snapshot.call_args.args
    assert snapshot_args[0] == album_folder
    assert Path(snapshot_args[1]) == Path("Some Artist/Some Album")
    tag_locks.restore.assert_called_once_with(album_folder, {"genre": "Original Genre"})

    # snapshot must happen before beet import runs, not after.
    assert call_order[0] == "snapshot"
    assert "beet_import" in call_order


def test_restore_runs_after_field_overrides_apply(isolated_env):
    """A lock must win over the override escape hatch too — restore has
    to be the last tag write in the success path."""
    db.init_db()
    album_folder = _make_album_folder(isolated_env)
    enricher, field_overrides, tag_locks = _make_enricher(
        field_overrides_return=2, tag_locks_snapshot={"genre": "Original"}, tag_locks_restore=1,
    )
    store.vgmdb_mapping.write({
        "mb-1": {"vgmdb_id": "999", "artist": "Some Artist", "album": "Some Album",
                 "folder": "Some Artist/Some Album", "source": "manual"},
    })

    call_order: list[str] = []

    def _fake_apply_overrides(*_a, **_kw):
        call_order.append("overrides")
        return 2

    def _fake_restore(*_a, **_kw):
        call_order.append("locks")
        return 1

    field_overrides.apply_overrides.side_effect = _fake_apply_overrides
    tag_locks.restore.side_effect = _fake_restore

    with patch.object(be, "_subprocess_run", return_value=MagicMock(
        returncode=0, stdout="Match (95.0%)\n", stderr="",
    )):
        info = {"artist": "Some Artist", "album": "Some Album",
                "mb_release_id": "mb-1", "folder": "Some Artist/Some Album"}
        result = enricher.enrich_album(info)

    assert result["fields_overridden"] == 2
    assert result["fields_locked"] == 1
    assert call_order == ["overrides", "locks"]


def test_already_enriched_short_circuit_does_not_touch_locks(isolated_env):
    """No beet import runs on this path, so there's nothing for a lock
    to protect against — snapshot/restore must be skipped entirely."""
    album_folder = _make_album_folder(isolated_env)
    enricher, field_overrides, tag_locks = _make_enricher(field_overrides_return=0)
    store.save_enriched_set({"mb-1"})

    info = {"artist": "Some Artist", "album": "Some Album",
            "mb_release_id": "mb-1", "folder": "Some Artist/Some Album"}
    result = enricher.enrich_album(info)

    assert result["already_enriched"] is True
    tag_locks.snapshot.assert_not_called()
    tag_locks.restore.assert_not_called()
