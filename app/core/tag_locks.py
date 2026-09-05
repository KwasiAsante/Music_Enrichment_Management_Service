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
"""

from __future__ import annotations

import logging
from pathlib import Path

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
