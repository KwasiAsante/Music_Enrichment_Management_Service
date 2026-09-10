"""TagLockService — per-field locks against enrichment overwriting a tag.

A lock is just a field name (standard or custom) recorded per album
folder in ``locked_fields.json``. :meth:`app.core.beets_enricher.
BeetsEnricher.enrich_album` calls :meth:`snapshot` right before running
``beet import`` and :meth:`restore` right after (after the non-Latin-tag
fix and :mod:`app.core.field_overrides`'s apply step), so a locked tag
survives the whole enrichment pipeline untouched — a lock wins over both
beets' own import and the field-override escape hatch.

Storage mirrors ``field_overrides.json``'s shape/CRUD conventions:
``{folder: [field_name, ...]}``.

``get_track_locks``/``set_track_lock``/``snapshot_tracks``/``restore_tracks``
are a parallel, separately-stored pair of locks (``locked_track_fields.json``,
same shape) for ``title`` and track-level ``artist`` — the two per-track
identity fields the rest of this module refuses to touch (see
``tag_fields.PER_TRACK_ONLY_KEYS``). Unlike the field locks above, these
don't broadcast one value to every file: each track keeps its own value,
matched before/after enrichment by ``(disc, track)`` position rather than
file path, since beets may rename files based on the newly-written title.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from app.core import tag_fields
from app.storage.json_store import store

log = logging.getLogger("music-lib-helper.tag_locks")


class TagLockService:
    def get_locks(self, folder: str) -> list[str]:
        return list(store.locked_fields.read().get(folder, []))

    def set_lock(self, folder: str, field: str, locked: bool) -> list[str]:
        if locked and field in tag_fields.PER_TRACK_ONLY_KEYS:
            raise ValueError(f"{field!r} is a per-track field — it can't be locked album-wide")

        all_locks = store.locked_fields.read()
        fields = list(all_locks.get(folder, []))
        if locked and field not in fields:
            fields.append(field)
        elif not locked and field in fields:
            fields.remove(field)

        if fields:
            all_locks[folder] = fields
        else:
            all_locks.pop(folder, None)
        store.locked_fields.write(all_locks)
        log.info("tag lock %s for %r on %s", "set" if locked else "cleared", field, folder)
        return fields

    # ── enforcement (called by BeetsEnricher) ───────────────────────────
    def snapshot(self, album_dir: Path, folder: str) -> dict[str, str | None]:
        """Current on-disk value of every locked field for ``folder``,
        read *before* enrichment writes anything. Empty (no file I/O
        beyond one JSON read) when nothing is locked for this folder."""
        fields = self.get_locks(folder)
        if not fields:
            return {}
        first_file = tag_fields.first_audio_file(album_dir)
        if first_file is None:
            return {}
        return {field: tag_fields.read_field_value(first_file, field) for field in fields}

    def restore(self, album_dir: Path, snapshot: dict[str, str | None]) -> int:
        """Re-write every snapshotted field back across the album.
        Returns the number of file writes applied.

        ``set_lock`` already refuses to lock a per-track field, so this
        shouldn't normally see one — but enrichment must never crash
        over a stale/hand-edited lock entry, so a rejected field is
        logged and skipped rather than raised.
        """
        applied = 0
        for field, value in snapshot.items():
            try:
                applied += tag_fields.write_album_field(album_dir, field, value or "")
            except ValueError as exc:
                log.warning("skipping restore of locked field %r on %s: %s", field, album_dir, exc)
        if snapshot:
            log.info("restored %d locked field write(s) in %s: %s",
                      applied, album_dir, list(snapshot.keys()))
        return applied

    # ── per-track locks (title / track artist) ──────────────────────────
    def get_track_locks(self, folder: str) -> list[str]:
        return list(store.locked_track_fields.read().get(folder, []))

    def set_track_lock(self, folder: str, field: str, locked: bool) -> list[str]:
        """Lock or unlock ``title`` or ``artist`` album-wide, but unlike
        :meth:`set_lock`, each track keeps its own value rather than all
        tracks sharing one — see :meth:`snapshot_tracks`/:meth:`restore_tracks`."""
        if locked and field not in tag_fields.TRACK_LOCKABLE_FIELDS:
            raise ValueError(
                f"{field!r} isn't a track-lockable field — only "
                f"{tag_fields.TRACK_LOCKABLE_FIELDS} can be locked per-track",
            )

        all_locks = store.locked_track_fields.read()
        fields = list(all_locks.get(folder, []))
        if locked and field not in fields:
            fields.append(field)
        elif not locked and field in fields:
            fields.remove(field)

        if fields:
            all_locks[folder] = fields
        else:
            all_locks.pop(folder, None)
        store.locked_track_fields.write(all_locks)
        log.info("track lock %s for %r on %s", "set" if locked else "cleared", field, folder)
        return fields

    def snapshot_tracks(
        self, album_dir: Path, folder: str,
    ) -> dict[str, dict[tuple[str, str], str | None]]:
        """Current per-track value of every locked track field for
        ``folder``, read before enrichment writes anything. Empty when
        nothing is track-locked for this folder."""
        fields = self.get_track_locks(folder)
        return {
            field: tag_fields.read_track_field_by_position(album_dir, field)
            for field in fields
        }

    def restore_tracks(
        self, album_dir: Path, snapshot: dict[str, dict[tuple[str, str], str | None]],
    ) -> int:
        """Re-write each locked track field back to its own track, matched
        by ``(disc, track)`` position rather than file path or order —
        see :func:`app.core.tag_fields.write_track_field_by_position`.
        Returns the number of file writes applied."""
        applied = 0
        for field, values in snapshot.items():
            applied += tag_fields.write_track_field_by_position(album_dir, field, values)
        if snapshot:
            log.info("restored %d per-track locked field write(s) in %s: %s",
                      applied, album_dir, list(snapshot.keys()))
        return applied

    # ── export / import (backup-restore) ────────────────────────────────
    def export_locks(self) -> dict[str, list[str]]:
        """Return the raw ``locked_fields.json`` contents, keyed by
        folder. The router wraps this with export metadata."""
        return store.locked_fields.read()

    def import_locks(
        self,
        incoming: dict[str, Any],
        *,
        mode: str = "merge",
        dry_run: bool = False,
    ) -> dict[str, Any]:
        """Load lock entries from a previously exported (or hand-edited)
        dict and write them into ``locked_fields.json``.

        ``mode``:

        * ``"merge"``   — keep every existing entry; entries present in
          ``incoming`` are added (new folder) or overwrite the existing
          value (same folder, different field list).
        * ``"replace"`` — the imported file becomes the entire set of
          locks; any existing entry not present in ``incoming`` is
          dropped.

        A row in ``incoming`` must be a list of field-name strings; a
        per-track-only field (see ``tag_fields.PER_TRACK_ONLY_KEYS``) is
        dropped from that list rather than failing the whole row — the
        same rule :meth:`set_lock` enforces, just applied best-effort
        here. Rows that aren't lists, or end up with no fields left
        after that filter, are counted in ``skipped_invalid``.

        With ``dry_run``, computes and returns the same stats without
        writing ``locked_fields.json``.
        """
        if mode not in ("merge", "replace"):
            raise ValueError(f"mode must be 'merge' or 'replace', got {mode!r}")

        current = store.locked_fields.read()

        validated: dict[str, list[str]] = {}
        skipped_invalid = 0
        for folder, fields in incoming.items():
            if not isinstance(fields, list):
                skipped_invalid += 1
                continue
            cleaned = sorted({
                str(f).strip() for f in fields
                if str(f).strip() and str(f).strip() not in tag_fields.PER_TRACK_ONLY_KEYS
            })
            if not cleaned:
                skipped_invalid += 1
                continue
            validated[folder] = cleaned

        added = updated = unchanged = 0
        for folder, fields in validated.items():
            if folder not in current:
                added += 1
            elif sorted(current[folder]) != fields:
                updated += 1
            else:
                unchanged += 1

        if mode == "replace":
            removed = sum(1 for k in current if k not in validated)
            new_locks = validated
        else:
            removed = 0
            new_locks = {**current, **validated}

        if not dry_run:
            store.locked_fields.write(new_locks)
            log.info(
                "tag locks import (mode=%s): +%d added, %d updated, %d removed, "
                "%d skipped, %d total",
                mode, added, updated, removed, skipped_invalid, len(new_locks),
            )
        else:
            log.info("tag locks import (dry run, mode=%s): would be "
                      "+%d added, %d updated, %d removed, %d skipped",
                      mode, added, updated, removed, skipped_invalid)

        return {
            "mode":            mode,
            "added":           added,
            "updated":         updated,
            "unchanged":       unchanged,
            "removed":         removed,
            "skipped_invalid": skipped_invalid,
            "total_after":     len(new_locks),
            "dry_run":         dry_run,
        }
