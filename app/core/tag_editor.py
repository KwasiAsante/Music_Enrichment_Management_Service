"""TagEditorService — view and immediately edit one album's tags.

Unlike :mod:`app.core.field_overrides` (which only takes effect the next
time an album is re-enriched), a save here writes the file(s) right away.
Covers both the standard field set and any custom tag already on the
files, and lets a brand-new custom tag be created just by naming it in
the write request — see :mod:`app.core.tag_fields` for how a field name
maps to on-disk tag key(s).
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from app.config import settings
from app.core import tag_fields
from app.core.tag_locks import TagLockService
from app.storage.json_store import store

log = logging.getLogger("music-lib-helper.tag_editor")


class TagEditorService:
    def __init__(self, tag_locks: TagLockService | None = None) -> None:
        self.tag_locks = tag_locks or TagLockService()
        self.artist_root: Path = settings.artist_root

    def _find_album(self, folder: str) -> dict[str, Any] | None:
        """Same folder-identity lookup as
        :meth:`app.core.field_overrides.FieldOverrideService._find_album`."""
        for folder_name, info in store.album_list.read().items():
            if (info.get("folder") or folder_name) == folder:
                return {**info, "folder": info.get("folder") or folder_name}
        return None

    def get_album_tags(self, folder: str) -> dict[str, Any]:
        """Every standard field plus every custom tag currently on the
        album, each with its current value and whether it's locked.
        Raises ``ValueError`` if ``folder`` doesn't match any known
        album."""
        info = self._find_album(folder)
        if info is None:
            raise ValueError(f"no album found with folder {folder!r}")

        album_dir = self.artist_root / folder
        values = tag_fields.read_album_fields(album_dir)
        locked = set(self.tag_locks.get_locks(folder))

        fields = [
            {
                "name": name,
                "value": value,
                "locked": name in locked,
                "source": tag_fields.classify_tag_source(name),
            }
            for name, value in values.items()
        ]

        warnings: list[str] = []
        if not tag_fields.iter_audio_files(album_dir):
            warnings.append("Could not read tags from any audio file in this folder.")

        return {
            "folder": folder,
            "artist": info.get("artist") or "",
            "album": info.get("album") or "",
            "fields": fields,
            "warnings": warnings,
        }

    def update_album_fields(self, folder: str, fields: dict[str, str]) -> int:
        """Write (or, for a value of ``""``, delete) every given field
        across the album's audio files right now. Returns the number of
        file writes applied across all fields."""
        info = self._find_album(folder)
        if info is None:
            raise ValueError(f"no album found with folder {folder!r}")

        album_dir = self.artist_root / folder
        touched = 0
        for field, value in fields.items():
            touched += tag_fields.write_album_field(album_dir, field, (value or "").strip())

        if fields:
            log.info("tag editor updated %s on %s", list(fields.keys()), folder)
        return touched
