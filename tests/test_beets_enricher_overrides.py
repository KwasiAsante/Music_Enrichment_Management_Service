"""app.core.beets_enricher — field-override integration.

BeetsEnricher.enrich_album gained a post-import (and already-enriched
short-circuit) call into FieldOverrideService.apply_overrides — this
file exercises only that integration point: that it's called with the
right (album_folder, folder-relative-to-artist-root) pair regardless of
whether info["folder"] arrived as a relative path (the bulk/album_list
shape) or an absolute one (the Lidarr OnAlbumDownload shape — see
app/api/enrich.py), and that its return value surfaces as
EnrichAlbumResult.fields_overridden.

Not a full BeetsEnricher unit suite — there isn't one for the rest of
enrich_album's flow yet either; see the module's own docstring on how
subprocess calls are isolated (``_subprocess_run``) for when one gets
written.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import app.core.beets_enricher as be
from app.core.beets_enricher import BeetsEnricher
from app.core.mb_link import LinkCheckResult
from app.storage import db
from app.storage.json_store import store


def _make_enricher(field_overrides_return: int = 0) -> tuple[BeetsEnricher, MagicMock]:
    field_overrides = MagicMock()
    field_overrides.apply_overrides.return_value = field_overrides_return
    enricher = BeetsEnricher(
        mb=MagicMock(),
        mapper=MagicMock(),
        notifier=MagicMock(send=MagicMock(return_value=True)),
        mb_link=MagicMock(check=MagicMock(return_value=LinkCheckResult(
            has_link=True, vgmdb_url=None, existing_url=None, seed_url=None,
        ))),
        scanner=MagicMock(),
        field_overrides=field_overrides,
    )
    return enricher, field_overrides


def _make_album_folder(isolated_env) -> Path:
    folder = isolated_env.music_dir / "synced_music" / "Artist" / "Some Artist" / "Some Album"
    folder.mkdir(parents=True)
    return folder


def test_already_enriched_short_circuit_still_applies_overrides(isolated_env):
    album_folder = _make_album_folder(isolated_env)
    enricher, field_overrides = _make_enricher(field_overrides_return=3)
    store.save_enriched_set({"mb-1"})

    info = {"artist": "Some Artist", "album": "Some Album",
            "mb_release_id": "mb-1", "folder": "Some Artist/Some Album"}
    result = enricher.enrich_album(info)

    assert result["already_enriched"] is True
    assert result["fields_overridden"] == 3
    field_overrides.apply_overrides.assert_called_once_with(album_folder, "Some Artist/Some Album")


def test_success_path_applies_overrides_and_reports_count(isolated_env):
    db.init_db()
    album_folder = _make_album_folder(isolated_env)
    enricher, field_overrides = _make_enricher(field_overrides_return=2)
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

    assert result["ok"] is True
    assert result["fields_overridden"] == 2
    field_overrides.apply_overrides.assert_called_once_with(album_folder, "Some Artist/Some Album")


def test_lidarr_hook_absolute_folder_still_keys_by_relative_path(isolated_env):
    """The Lidarr OnAlbumDownload path (app/api/enrich.py) passes
    info["folder"] as an *absolute* path, unlike bulk's album_list-relative
    form — apply_overrides must be called with the same
    artist-root-relative key either way, or a saved override would never
    match what the Field Overrides page saved it under."""
    album_folder = _make_album_folder(isolated_env)
    enricher, field_overrides = _make_enricher()
    store.save_enriched_set({"mb-1"})

    info = {"artist": "Some Artist", "album": "Some Album",
            "mb_release_id": "mb-1", "folder": str(album_folder)}
    enricher.enrich_album(info)

    field_overrides.apply_overrides.assert_called_once_with(album_folder, "Some Artist/Some Album")
