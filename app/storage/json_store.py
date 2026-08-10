"""JSON state storage layer.

All persistent *domain state* lives in a handful of plain-JSON files in
the data volume (``settings.app_data_dir``):

    album_list.json       {folder_name: {artist, album, mb_release_id, folder}}
    vgmdb_mapping.json    {mb_release_id: {vgmdb_id, folder, artist, album}}
    enriched_albums.json  [mb_release_id, ...]   (a set, stored as a sorted list)
    mb_artist_cache.json  {mb_artist_id: <raw MusicBrainz artist response>}
    skipped_albums.json   {mb_id: {vgmdb_id, folder, artist, album,
                                   match_percentage, threshold, skipped_reason}}
    excluded_artists.json [artist_name, ...]  (Western acts w/ no VGMDB presence)

They stay plain JSON on purpose — the standalone CLI scripts in
``scripts/`` read and write the exact same files, and they're trivial to
inspect or back up.

This module is the single place that touches those files. Every read and
write goes through :class:`JsonFile`, which gives:

* atomic writes (temp file in the same dir, then ``os.replace``), so a
  crash mid-write can never leave a half-written file, and
* a safe default when the file is missing or corrupt, so callers never
  have to special-case "first run".
"""

from __future__ import annotations

import copy
import json
import logging
import os
import tempfile
import threading
from pathlib import Path
from typing import Any

from app.config import settings

log = logging.getLogger("music-lib-helper.storage")

# Seed contents for excluded_artists.json the first time it's read and no
# file exists yet — the same Western-acts list that used to be a hardcoded
# constant in vgmdb_mapper.py / beets_enricher.py. Once the file exists,
# this seed is never consulted again; it only prevents behaviour changing
# on upgrade for anyone who hasn't touched the new artist-exclusion UI yet.
_DEFAULT_EXCLUDED_ARTISTS: list[str] = [
    "Linkin Park", "Thousand Foot Krutch", "Barenaked Ladies",
    "Jeff Williams", "Jeff Williams feat. Casey Lee Williams",
    "Jeff Williams feat. Casey Lee Williams with Alex Abraham",
]


class JsonFile:
    """A single JSON file with atomic writes and a missing-file default.

    :meth:`read` returns a deep copy of ``default`` when the file doesn't
    exist yet (or is corrupt) — never the shared default instance, so
    callers can mutate the result freely without that mutation leaking
    into the next "missing file" read. For most state files ``default``
    is genuinely empty (``{}``/``[]``); ``excluded_artists.json`` is the
    one exception, seeded with real content so first-run behaviour
    matches the old hardcoded constant it replaced.
    """

    def __init__(self, path: Path, default: Any) -> None:
        self.path = Path(path)
        self._default = default
        self._lock = threading.Lock()

    # ── read ────────────────────────────────────────────────────────────
    def read(self) -> Any:
        """Return the parsed contents, or a fresh default if missing/corrupt."""
        if not self.path.exists():
            return self._fresh_default()
        try:
            with open(self.path, encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError) as exc:
            log.warning("could not read %s (%s) — returning default", self.path, exc)
            return self._fresh_default()

    # ── write ───────────────────────────────────────────────────────────
    def write(self, data: Any) -> None:
        """Atomically replace the file's contents with ``data``."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._lock:
            fd, tmp = tempfile.mkstemp(dir=self.path.parent, suffix=".tmp")
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as f:
                    json.dump(data, f, indent=2, ensure_ascii=False)
                    f.flush()
                    os.fsync(f.fileno())
                os.replace(tmp, self.path)
            except BaseException:
                # Never leave a stray temp file behind on failure.
                try:
                    os.unlink(tmp)
                except OSError:
                    pass
                raise

    # ── helpers ─────────────────────────────────────────────────────────
    def _fresh_default(self) -> Any:
        return copy.deepcopy(self._default)


class JsonStore:
    """Facade exposing each state file as a :class:`JsonFile`.

    Import the module-level :data:`store` singleton rather than
    constructing this directly.
    """

    def __init__(self) -> None:
        data_dir = settings.app_data_dir
        self.album_list = JsonFile(settings.album_list_file, {})
        self.vgmdb_mapping = JsonFile(settings.vgmdb_mapping_file, {})
        self.enriched_albums = JsonFile(settings.enriched_albums_file, [])
        self.mb_artist_cache = JsonFile(settings.mb_artist_cache_file, {})
        self.skipped_albums = JsonFile(data_dir / "skipped_albums.json", {})
        # Artists purposely excluded from "unmapped" listings *and* from
        # bulk enrichment — a flat list of exact artist names, editable at
        # runtime via /api/v1/mapping/excluded-artists (see vgmdb_mapper.py).
        self.excluded_artists = JsonFile(
            data_dir / "excluded_artists.json", list(_DEFAULT_EXCLUDED_ARTISTS),
        )
        # artists_mbids.json is a *list* on disk (matches the format the Picard
        # export pipeline uploads to a GitHub Gist), but the exporter treats it
        # as a dict-keyed-by-Artist internally for merging.
        self.artists_mbids = JsonFile(data_dir / "artists_mbids.json", [])

    # ── enriched_albums is logically a set ──────────────────────────────
    # The original beets-enrich.py treats enriched_albums.json as a set of
    # MB release IDs but persists it as a sorted list. These two helpers
    # keep that contract in one place.
    def enriched_set(self) -> set[str]:
        """Return the enriched-album MB release IDs as a set."""
        return set(self.enriched_albums.read())

    def save_enriched_set(self, ids: set[str]) -> None:
        """Persist a set of MB release IDs (written as a sorted list)."""
        self.enriched_albums.write(sorted(ids))


# Module-level singleton — import this everywhere.
store = JsonStore()
