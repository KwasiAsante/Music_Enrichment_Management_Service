"""app.core.beets_enricher — per-track lock (title/artist) integration.

Mirrors test_beets_enricher_locks.py's coverage of the album-wide
snapshot/restore pair, but for TagLockService.snapshot_tracks/
restore_tracks — the two calls should sit right alongside the existing
ones (snapshot before beet import, restore after field overrides apply),
and their restore count folds into the same EnrichAlbumResult.fields_locked
total.
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
    track_snapshot: dict | None = None, track_restore: int = 0,
) -> tuple[BeetsEnricher, MagicMock]:
    tag_locks = MagicMock()
    tag_locks.snapshot.return_value = {}
    tag_locks.restore.return_value = 0
    tag_locks.snapshot_tracks.return_value = track_snapshot or {}
    tag_locks.restore_tracks.return_value = track_restore
    enricher = BeetsEnricher(
        mb=MagicMock(),
        mapper=MagicMock(),
        notifier=MagicMock(send=MagicMock(return_value=True)),
        mb_link=MagicMock(check=MagicMock(return_value=LinkCheckResult(
            has_link=True, vgmdb_url=None, existing_url=None, seed_url=None,
        ))),
        scanner=MagicMock(),
        field_overrides=MagicMock(apply_overrides=MagicMock(return_value=0)),
        tag_locks=tag_locks,
    )
    return enricher, tag_locks


def _make_album_folder(isolated_env) -> Path:
    folder = isolated_env.music_dir / "Artist" / "Some Artist" / "Some Album"
    folder.mkdir(parents=True)
    return folder


def test_success_path_snapshots_tracks_before_import_and_restores_after(isolated_env):
    db.init_db()
    album_folder = _make_album_folder(isolated_env)
    enricher, tag_locks = _make_enricher(
        track_snapshot={"title": {("1", "1"): "Original Title"}}, track_restore=1,
    )
    store.vgmdb_mapping.write({
        "mb-1": {"vgmdb_id": "999", "artist": "Some Artist", "album": "Some Album",
                 "folder": "Some Artist/Some Album", "source": "manual"},
    })

    call_order: list[str] = []
    tag_locks.snapshot_tracks.side_effect = lambda *a, **kw: (
        call_order.append("snapshot_tracks") or {"title": {("1", "1"): "Original Title"}}
    )

    def _fake_subprocess_run(*_a, **_kw):
        call_order.append("beet_import")
        return MagicMock(returncode=0, stdout="Match (95.0%)\n", stderr="")

    with patch.object(be, "_subprocess_run", side_effect=_fake_subprocess_run):
        info = {"artist": "Some Artist", "album": "Some Album",
                "mb_release_id": "mb-1", "folder": "Some Artist/Some Album"}
        result = enricher.enrich_album(info)

    assert result["ok"] is True
    assert result["fields_locked"] == 1

    snapshot_args = tag_locks.snapshot_tracks.call_args.args
    assert snapshot_args[0] == album_folder
    assert Path(snapshot_args[1]) == Path("Some Artist/Some Album")
    tag_locks.restore_tracks.assert_called_once_with(
        album_folder, {"title": {("1", "1"): "Original Title"}},
    )

    assert call_order[0] == "snapshot_tracks"
    assert "beet_import" in call_order


def test_field_and_track_lock_restore_counts_both_fold_into_fields_locked(isolated_env):
    db.init_db()
    album_folder = _make_album_folder(isolated_env)
    tag_locks = MagicMock()
    tag_locks.snapshot.return_value = {"genre": "Rock"}
    tag_locks.restore.return_value = 2
    tag_locks.snapshot_tracks.return_value = {"title": {("1", "1"): "Old"}}
    tag_locks.restore_tracks.return_value = 1
    enricher = BeetsEnricher(
        mb=MagicMock(),
        mapper=MagicMock(),
        notifier=MagicMock(send=MagicMock(return_value=True)),
        mb_link=MagicMock(check=MagicMock(return_value=LinkCheckResult(
            has_link=True, vgmdb_url=None, existing_url=None, seed_url=None,
        ))),
        scanner=MagicMock(),
        field_overrides=MagicMock(apply_overrides=MagicMock(return_value=0)),
        tag_locks=tag_locks,
    )
    store.vgmdb_mapping.write({
        "mb-1": {"vgmdb_id": "999", "artist": "Some Artist", "album": "Some Album",
                 "folder": "Some Artist/Some Album", "source": "manual"},
    })

    with patch.object(be, "_subprocess_run", return_value=MagicMock(
        returncode=0, stdout="Match (95.0%)\n", stderr="",
    )):
        info = {"artist": "Some Artist", "album": "Some Album",
                "mb_release_id": "mb-1", "folder": "Some Artist/Some Album"}
        result = enricher.enrich_album(info)

    assert result["fields_locked"] == 3  # 2 (album-wide) + 1 (per-track)


def test_already_enriched_short_circuit_does_not_touch_track_locks(isolated_env):
    """No beet import runs on this path — nothing for a per-track lock to
    protect against, so snapshot_tracks/restore_tracks must be skipped."""
    _make_album_folder(isolated_env)
    enricher, tag_locks = _make_enricher()
    store.save_enriched_set({"mb-1"})

    info = {"artist": "Some Artist", "album": "Some Album",
            "mb_release_id": "mb-1", "folder": "Some Artist/Some Album"}
    result = enricher.enrich_album(info)

    assert result["already_enriched"] is True
    tag_locks.snapshot_tracks.assert_not_called()
    tag_locks.restore_tracks.assert_not_called()
