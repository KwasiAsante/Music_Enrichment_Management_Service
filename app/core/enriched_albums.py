"""EnrichedAlbumsService — export/import for ``enriched_albums.json``.

``enriched_albums.json`` is the set of MusicBrainz release ids that have
already been run through the enrichment pipeline — ``BeetsEnricher``
consults it to skip albums it's already processed and clears entries out
of it for ``--redo`` (see ``app/core/beets_enricher.py``). This service
adds an export/import surface so the enriched log can be backed up and
restored the same way as the other three per-album state files
(``vgmdb_mapping.json``, ``field_overrides.json``, ``locked_fields.json``)
— the pipeline itself remains the only writer of new entries day to day.
"""

from __future__ import annotations

import logging
from typing import Any

from app.storage.json_store import store

log = logging.getLogger("music-lib-helper.enriched_albums")


class EnrichedAlbumsService:
    """Export/import over ``enriched_albums.json`` (a set of MB release ids)."""

    def export_enriched(self) -> list[str]:
        """Return the raw ``enriched_albums.json`` contents — a sorted
        list of MB release ids. The router wraps this with export
        metadata."""
        return store.enriched_albums.read()

    def import_enriched(
        self,
        incoming: list[Any],
        *,
        mode: str = "merge",
        dry_run: bool = False,
    ) -> dict[str, Any]:
        """Load MB release ids from a previously exported (or
        hand-edited) list and write them into ``enriched_albums.json``.

        ``mode``:

        * ``"merge"``   — union of the current set and ``incoming``.
        * ``"replace"`` — the imported file becomes the entire set; any
          existing id not present in ``incoming`` is dropped.

        Entries that aren't non-empty strings are silently skipped and
        counted in ``skipped_invalid``.

        With ``dry_run``, computes and returns the same stats without
        writing ``enriched_albums.json``.
        """
        if mode not in ("merge", "replace"):
            raise ValueError(f"mode must be 'merge' or 'replace', got {mode!r}")

        current = store.enriched_set()

        validated: set[str] = set()
        skipped_invalid = 0
        for item in incoming:
            if isinstance(item, str) and item.strip():
                validated.add(item.strip())
            else:
                skipped_invalid += 1

        added = len(validated - current)
        unchanged = len(validated & current)

        if mode == "replace":
            removed = len(current - validated)
            new_set = validated
        else:
            removed = 0
            new_set = current | validated

        if not dry_run:
            store.save_enriched_set(new_set)
            log.info(
                "enriched albums import (mode=%s): +%d added, %d removed, "
                "%d skipped, %d total",
                mode, added, removed, skipped_invalid, len(new_set),
            )
        else:
            log.info("enriched albums import (dry run, mode=%s): would be "
                      "+%d added, %d removed, %d skipped",
                      mode, added, removed, skipped_invalid)

        return {
            "mode":            mode,
            "added":           added,
            "unchanged":       unchanged,
            "removed":         removed,
            "skipped_invalid": skipped_invalid,
            "total_after":     len(new_set),
            "dry_run":         dry_run,
        }
