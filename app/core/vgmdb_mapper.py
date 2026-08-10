"""VGMDBMapper — ported from ``generate-mappings-template.py`` + ``map-vgmdb.py``.

Two responsibilities:

* **Search** — given an album, find candidate VGMDB ids using the same
  four-step pipeline the original interactive script used:

      0. Auto-mapping: if the MusicBrainz release already has a VGMDB
         URL relationship, that's the id — no search needed.
      1. Catalog hint from audio file tags (fast — no network call).
      2. If no tag catalog, fetch catalog + barcode from MB.
      3. Search VGMDB by catalog → barcode → title (each step only if
         the previous returned nothing).

* **CRUD** over ``vgmdb_mapping.json`` — list, list-unmapped, set, delete,
  plus a filtered view over the "skipped" (``vgmdb_id == "skip"``) subset.
  These are thin wrappers over the JSON storage layer; they exist so the
  API router can stay declarative and the policy (what counts as
  "unmapped", entry shape) lives in one place.

* **CRUD** over ``excluded_artists.json`` — artists purposely excluded
  from "unmapped" listings (Western acts with no VGMDB presence).
  Editable at runtime; also consulted by ``BeetsEnricher`` so an
  excluded artist is skipped by bulk enrichment too.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from mutagen import File as MutagenFile  # type: ignore[import-untyped]

from app.config import settings
from app.core.mb_client import MBClient
from app.core.vgmdb_client import VGMDBClient, VGMDBHint
from app.storage.json_store import store

log = logging.getLogger("music-lib-helper.vgmdb_mapper")

AUDIO_EXTS = {".mp3", ".flac", ".ogg", ".opus", ".m4a", ".aac", ".wav", ".ape"}


# ── helpers ────────────────────────────────────────────────────────────────
def normalize_catalog(s: str | None) -> str:
    """Strip dashes/spaces/tildes and upper-case for comparison."""
    if not s:
        return ""
    return s.upper().replace("-", "").replace(" ", "").replace("~", "")


def is_suggested(
    hint_title: str,
    album_name: str,
    *,
    hint_catalog: str | None = None,
    mb_catalog: str | None = None,
) -> bool:
    """True if a hint is a strong match for an album.

    Priority: normalised catalog match (including prefix overlap in
    either direction) > title substring match. Mirrors the original.
    """
    if hint_catalog and mb_catalog:
        hc = normalize_catalog(hint_catalog)
        mc = normalize_catalog(mb_catalog)
        if hc and mc and (hc == mc or hc.startswith(mc) or mc.startswith(hc)):
            return True
    if not hint_title or not album_name:
        return False
    return (hint_title.lower() in album_name.lower()
            or album_name.lower() in hint_title.lower())


def _read_catalog_from_tags(folder: Path) -> str | None:
    """Read the first catalog (or barcode as fallback) found in any audio
    file under ``folder``. Returns None on failure or if nothing matched.
    Only the first audio file is inspected — same as the original script.
    """
    if not folder.exists():
        return None
    for f in sorted(folder.rglob("*")):
        if f.suffix.lower() not in AUDIO_EXTS:
            continue
        try:
            audio = MutagenFile(str(f), easy=False)
        except Exception:  # noqa: BLE001
            return None
        if not audio or not audio.tags:
            return None
        tags = audio.tags
        # Try CATALOG / CATALOGNUMBER tags first
        for key in list(tags.keys()):
            ku = key.upper()
            if "CATALOGNUMBER" in ku or "CATALOG" in ku:
                val = tags[key]
                cat = _extract_tag_text(val)
                if cat:
                    return cat
        # Fall back to BARCODE
        for key in list(tags.keys()):
            if "BARCODE" in key.upper():
                val = tags[key]
                bc = _extract_tag_text(val)
                if bc:
                    return bc
        return None
    return None


def _extract_tag_text(val: Any) -> str | None:
    """Pull a text string out of a mutagen tag value across formats."""
    if hasattr(val, "text") and val.text:
        return str(val.text[0]).strip()
    if hasattr(val, "__iter__") and not isinstance(val, (str, bytes)):
        items = list(val)
        if items:
            item = items[0]
            if hasattr(item, "value"):
                return str(item.value).strip()
            return str(item).strip()
    if isinstance(val, (str, bytes)):
        return val.decode("utf-8", "replace").strip() if isinstance(val, bytes) else val.strip()
    return None


# ── service ────────────────────────────────────────────────────────────────
class VGMDBMapper:
    """Search VGMDB for albums and manage ``vgmdb_mapping.json`` entries."""

    def __init__(
        self,
        mb: MBClient | None = None,
        vgmdb: VGMDBClient | None = None,
    ) -> None:
        self.mb = mb or MBClient()
        self.vgmdb = vgmdb or VGMDBClient()
        self.artist_root: Path = settings.app_music_dir / "synced_music" / "Artist"

    # ── search pipeline ────────────────────────────────────────────────
    def search(
        self,
        *,
        mb_release_id: str | None,
        album: str,
        artist: str = "",
        folder: str | None = None,
    ) -> dict[str, Any]:
        """Run the full search pipeline for one album.

        Returns::

            {
              "mb_vgmdb_id": str | None,    # set if MB URL relationship found
              "mb_catalog":  str | None,
              "mb_barcode":  str | None,
              "catalog_hints": [VGMDBHint, ...],
              "barcode_hints": [VGMDBHint, ...],
              "title_hints":   [VGMDBHint, ...],
              "suggested": VGMDBHint | None,   # best match across all hints
            }

        Any individual step that returns no hints simply yields an empty
        list; callers (the API, the future Web UI) decide what to render.
        """
        result: dict[str, Any] = {
            "mb_vgmdb_id":   None,
            "mb_catalog":    None,
            "mb_barcode":    None,
            "catalog_hints": [],
            "barcode_hints": [],
            "title_hints":   [],
            "suggested":     None,
        }

        # ── 0: MB URL relationship → auto-map ──────────────────────────
        if mb_release_id and not mb_release_id.startswith("vgmdb-"):
            mb_vgmdb_id = self.mb.release_vgmdb_link(mb_release_id)
            if mb_vgmdb_id:
                log.info("auto-map via MB URL relationship: %s → vgmdb:%s",
                         mb_release_id, mb_vgmdb_id)
                result["mb_vgmdb_id"] = mb_vgmdb_id
                return result

        # ── 1: catalog from audio tags ─────────────────────────────────
        mb_catalog: str | None = None
        if folder:
            folder_path = self.artist_root / folder
            mb_catalog = _read_catalog_from_tags(folder_path)

        # ── 2: catalog + barcode from MB if tags didn't have one ───────
        mb_barcode: str | None = None
        if not mb_catalog and mb_release_id and not mb_release_id.startswith("vgmdb-"):
            mb_catalog, mb_barcode = self.mb.release_catalog_barcode(mb_release_id)

        result["mb_catalog"] = mb_catalog
        result["mb_barcode"] = mb_barcode

        # ── 3a: VGMDB by catalog ───────────────────────────────────────
        if mb_catalog:
            result["catalog_hints"] = self.vgmdb.search_by_catalog(mb_catalog)

        # ── 3b: VGMDB by barcode (only if catalog turned up nothing) ──
        if not result["catalog_hints"] and mb_barcode:
            result["barcode_hints"] = self.vgmdb.search_by_barcode(mb_barcode)

        # ── 3c: VGMDB by title (only if the structured searches failed) ─
        if not result["catalog_hints"] and not result["barcode_hints"]:
            hints = self.vgmdb.search(album)
            # If a free-text search of the full title returns nothing, try
            # the first four words — the original script's fallback for
            # albums with long parenthesised suffixes.
            if not hints:
                hints = self.vgmdb.search(" ".join(album.split()[:4]))
            result["title_hints"] = hints

        # ── pick a "suggested" hint across all three hint lists ────────
        all_hints: list[VGMDBHint] = (
            result["catalog_hints"]
            + result["barcode_hints"]
            + result["title_hints"]
        )
        for h in all_hints:
            if is_suggested(h.get("title", ""), album,
                            hint_catalog=h.get("catalog"),
                            mb_catalog=mb_catalog):
                result["suggested"] = h
                break

        return result

    # ── CRUD over vgmdb_mapping.json ───────────────────────────────────
    def list_mappings(self, artist_filter: str | None = None) -> list[dict]:
        """Return all entries from ``vgmdb_mapping.json`` as a list, each
        annotated with its ``mb_release_id`` key. Optionally filtered by
        artist (case-insensitive substring match)."""
        mapping = store.vgmdb_mapping.read()
        af = artist_filter.lower() if artist_filter else None
        out: list[dict] = []
        for mb_id, entry in mapping.items():
            if af and af not in (entry.get("artist") or "").lower():
                continue
            row = dict(entry)
            row["mb_release_id"] = mb_id
            out.append(row)
        out.sort(key=lambda r: ((r.get("artist") or "").lower(),
                                (r.get("album") or "").lower()))
        return out

    def list_unmapped(
        self,
        artist_filter: str | None = None,
        *,
        skip_western: bool = True,
    ) -> list[dict]:
        """Return album_list entries that have no VGMDB association.

        Mirrors the original ``cmd_unmapped`` / ``get_unmapped`` logic:
        an album counts as mapped if its ``mb_release_id`` is in
        ``vgmdb_mapping.json`` OR is itself a ``vgmdb-`` id. Artists in
        ``excluded_artists.json`` are excluded by default — see
        :meth:`list_excluded_artists`.
        """
        albums = store.album_list.read()
        mapping = store.vgmdb_mapping.read()
        af = artist_filter.lower() if artist_filter else None
        excluded = ({a.lower() for a in store.excluded_artists.read()}
                    if skip_western else set())

        out: list[dict] = []
        for folder_name, info in albums.items():
            artist = info.get("artist") or ""
            if skip_western and artist.lower() in excluded:
                continue
            if af and af not in artist.lower():
                continue
            mb_id = info.get("mb_release_id") or ""
            if mb_id in mapping:
                continue
            if mb_id.startswith("vgmdb-"):
                continue
            out.append({
                "folder_name":   folder_name,
                "artist":        artist,
                "album":         info.get("album") or "",
                "mb_release_id": mb_id or None,
                "folder":        info.get("folder") or "",
            })
        out.sort(key=lambda e: (e["artist"].lower(), e["album"].lower()))
        return out

    def set_mapping(
        self,
        mb_release_id: str,
        vgmdb_id: str,
        *,
        artist: str = "",
        album: str = "",
        folder: str = "",
        source: str = "manual",
        dry_run: bool = False,
    ) -> dict:
        """Insert or update one mapping entry.

        ``vgmdb_id`` may be the string ``"skip"`` — the original scripts
        use that as a sentinel meaning "this album is not on VGMDB; don't
        ask about it again". Empty ``artist``/``album``/``folder`` get
        backfilled from ``album_list.json`` if a row exists for the
        ``mb_release_id``. ``dry_run`` computes and returns the entry
        without writing it to ``vgmdb_mapping.json``.
        """
        if not mb_release_id:
            raise ValueError("mb_release_id is required")
        if not vgmdb_id:
            raise ValueError("vgmdb_id is required (use 'skip' to mark as not-on-VGMDB)")

        # Backfill missing fields from the album list if we can find them.
        if not (artist and album and folder):
            for fname, info in store.album_list.read().items():
                if info.get("mb_release_id") == mb_release_id:
                    artist = artist or (info.get("artist") or "")
                    album = album or (info.get("album") or "")
                    folder = folder or (info.get("folder") or fname)
                    break

        entry = {
            "vgmdb_id": str(vgmdb_id),
            "folder":   folder,
            "artist":   artist,
            "album":    album,
            "source":   source,
        }

        if dry_run:
            log.info("mapping set (dry run): %s → vgmdb:%s (source=%s)",
                     mb_release_id, vgmdb_id, source)
        else:
            mapping = store.vgmdb_mapping.read()
            mapping[mb_release_id] = entry
            store.vgmdb_mapping.write(mapping)
            log.info("mapping set: %s → vgmdb:%s (source=%s)",
                     mb_release_id, vgmdb_id, source)

        out = dict(entry)
        out["mb_release_id"] = mb_release_id
        out["dry_run"] = dry_run
        return out

    def delete_mapping(self, mb_release_id: str, *, dry_run: bool = False) -> bool:
        """Remove a mapping. Returns True if it existed, False otherwise.

        With ``dry_run``, reports whether it *would* be deleted without
        writing.
        """
        mapping = store.vgmdb_mapping.read()
        if mb_release_id not in mapping:
            return False
        if dry_run:
            log.info("mapping delete (dry run): %s would be removed", mb_release_id)
            return True
        del mapping[mb_release_id]
        store.vgmdb_mapping.write(mapping)
        log.info("mapping deleted: %s", mb_release_id)
        return True

    def list_skipped(self, artist_filter: str | None = None) -> list[dict]:
        """Mapping entries explicitly marked ``vgmdb_id == "skip"`` — i.e.
        albums confirmed to have no VGMDB presence, so they're excluded
        from future search/unmapped prompts. A subset of
        :meth:`list_mappings`."""
        return [row for row in self.list_mappings(artist_filter=artist_filter)
                if row.get("vgmdb_id") == "skip"]

    # ── excluded artists (Phase 3 config UI) ────────────────────────────
    def list_excluded_artists(self) -> list[str]:
        """Artists purposely excluded from "unmapped" listings and bulk
        enrichment, case-insensitively sorted."""
        return sorted(store.excluded_artists.read(), key=str.lower)

    def add_excluded_artist(self, artist: str) -> bool:
        """Add an artist to the exclusion list. Returns False (no-op) if
        already present (case-insensitive)."""
        artist = (artist or "").strip()
        if not artist:
            raise ValueError("artist name is required")
        current = store.excluded_artists.read()
        if any(a.lower() == artist.lower() for a in current):
            return False
        current.append(artist)
        store.excluded_artists.write(current)
        log.info("excluded artist added: %s", artist)
        return True

    def remove_excluded_artist(self, artist: str) -> bool:
        """Remove an artist from the exclusion list (case-insensitive).
        Returns True if it was present and removed."""
        current = store.excluded_artists.read()
        new_list = [a for a in current if a.lower() != artist.strip().lower()]
        if len(new_list) == len(current):
            return False
        store.excluded_artists.write(new_list)
        log.info("excluded artist removed: %s", artist)
        return True

    # ── export / import (Phase 3 backup-restore) ───────────────────────
    def export_mappings(self) -> dict[str, dict]:
        """Return the raw ``vgmdb_mapping.json`` contents, keyed by
        ``mb_release_id``. The router wraps this with export metadata."""
        return store.vgmdb_mapping.read()

    def import_mappings(
        self,
        incoming: dict[str, Any],
        *,
        mode: str = "merge",
        dry_run: bool = False,
    ) -> dict[str, Any]:
        """Load mapping entries from a previously exported (or hand-edited)
        dict and write them into ``vgmdb_mapping.json``.

        ``mode``:

        * ``"merge"``   — keep every existing entry; entries present in
          ``incoming`` are added (new ``mb_release_id``) or overwrite the
          existing value (same key, different value).
        * ``"replace"`` — the imported file becomes the entire mapping;
          any existing entry not present in ``incoming`` is dropped.

        Rows in ``incoming`` that aren't objects, or are missing
        ``vgmdb_id``, are silently skipped and counted in
        ``skipped_invalid`` — a partially-hand-edited backup shouldn't
        blow up the whole restore.

        With ``dry_run``, computes and returns the same stats without
        writing ``vgmdb_mapping.json``.
        """
        if mode not in ("merge", "replace"):
            raise ValueError(f"mode must be 'merge' or 'replace', got {mode!r}")

        current = store.vgmdb_mapping.read()

        validated: dict[str, dict] = {}
        skipped_invalid = 0
        for mb_id, entry in incoming.items():
            if not isinstance(entry, dict) or not entry.get("vgmdb_id"):
                skipped_invalid += 1
                continue
            validated[mb_id] = {
                "vgmdb_id": str(entry["vgmdb_id"]),
                "folder":   entry.get("folder", "") or "",
                "artist":   entry.get("artist", "") or "",
                "album":    entry.get("album", "") or "",
                "source":   entry.get("source", "") or "import",
            }

        added = updated = unchanged = 0
        for mb_id, entry in validated.items():
            if mb_id not in current:
                added += 1
            elif current[mb_id] != entry:
                updated += 1
            else:
                unchanged += 1

        if mode == "replace":
            removed = sum(1 for k in current if k not in validated)
            new_mapping = validated
        else:
            removed = 0
            new_mapping = {**current, **validated}

        if not dry_run:
            store.vgmdb_mapping.write(new_mapping)
            log.info(
                "mapping import (mode=%s): +%d added, %d updated, %d removed, "
                "%d skipped, %d total",
                mode, added, updated, removed, skipped_invalid, len(new_mapping),
            )
        else:
            log.info("mapping import (dry run, mode=%s): would be "
                     "+%d added, %d updated, %d removed, %d skipped",
                     mode, added, updated, removed, skipped_invalid)

        return {
            "mode":            mode,
            "added":           added,
            "updated":         updated,
            "unchanged":       unchanged,
            "removed":         removed,
            "skipped_invalid": skipped_invalid,
            "total_after":     len(new_mapping),
            "dry_run":         dry_run,
        }
