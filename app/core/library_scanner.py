"""LibraryScanner — ported from ``scan-library.py``.

Walks the music library, rebuilds ``album_list.json``, and (optionally)
removes empty or audio-less folders.

Differences from the original CLI script, and why:

* **Paths come from settings.** The original hardcoded
  ``/home/kwasi/Music/...`` and ``~/scripts/arr-scripts/lidarr/...``.
  Here the artist root is derived from ``settings.app_music_dir`` (the
  ``/music`` bind mount) and the album list is read/written through the
  JSON storage layer (``/data`` volume).

* **Cleanup is opt-in and never interactive.** The original prompted at
  the terminal before each deletion. A service has no terminal, and
  deleting folders is destructive, so :meth:`scan` takes
  ``cleanup=False`` by default — the caller must explicitly opt in. When
  ``cleanup=True`` it deletes without prompting; ``PermissionError`` /
  ``OSError`` are still caught per-folder and reported in the result
  (rather than aborting the scan) so an unexpectedly read-only mount or
  a locked file degrades gracefully.

* **Returns a result dict instead of printing.** Progress lines still go
  to the module logger, but the structured result is what the API
  router and the scheduler consume.
"""

from __future__ import annotations

import logging
import shutil
from pathlib import Path
from typing import Any

from mutagen import File as MutagenFile  # type: ignore[import-untyped]

from app.config import settings
from app.storage.json_store import store

log = logging.getLogger("lidarr-helper.scanner")

AUDIO_EXTS: set[str] = {
    ".mp3", ".flac", ".ogg", ".opus", ".m4a", ".aac", ".wav", ".ape",
}

# Tag keys to probe for the MusicBrainz release id and names. mutagen's
# ``easy=True`` mode normalises most of these, but case still varies
# across formats so we check both.
_MB_RELEASE_KEYS = ("musicbrainz_albumid", "MUSICBRAINZ_ALBUMID")


def _first(value: Any, fallback: str) -> str:
    """Coerce a mutagen tag value (often a one-element list) to a string."""
    if isinstance(value, list):
        return str(value[0]) if value else fallback
    if value is None:
        return fallback
    return str(value)


class LibraryScanner:
    """Scans the music library and rebuilds the album list.

    Construct once and call :meth:`scan`. Stateless between calls — all
    persistent state goes through :data:`app.storage.json_store.store`.
    """

    def __init__(self) -> None:
        # Matches `directory: /music/synced_music/Artist` in config.yaml.
        self.artist_root: Path = settings.app_music_dir / "synced_music" / "Artist"

    # ── folder predicates ───────────────────────────────────────────────
    @staticmethod
    def _has_audio(folder: Path) -> bool:
        """True if the folder contains at least one audio file at any depth."""
        return any(
            f.suffix.lower() in AUDIO_EXTS
            for f in folder.rglob("*")
            if f.is_file()
        )

    @staticmethod
    def _is_empty(folder: Path) -> bool:
        """True if the folder contains no files at all (subdirs ignored)."""
        return not any(f.is_file() for f in folder.rglob("*"))

    @staticmethod
    def _get_tags(filepath: Path) -> dict:
        """Read easy-mode tags from an audio file; empty dict on any failure."""
        try:
            audio = MutagenFile(str(filepath), easy=True)
            if audio and audio.tags:
                return dict(audio.tags)
        except Exception as exc:  # noqa: BLE001 — mutagen raises many types
            log.debug("could not read tags from %s: %s", filepath, exc)
        return {}

    # ── cleanup pass ────────────────────────────────────────────────────
    def _cleanup_folders(self, dry_run: bool) -> dict:
        """Remove empty / audio-less album folders and emptied artist folders.

        Non-interactive. On a read-only mount every deletion will raise;
        those are caught per-folder and surfaced in the ``errors`` list so
        the scan still completes.
        """
        album_removed = 0
        artist_removed = 0
        removed: list[str] = []
        errors: list[str] = []
        label = "[DRY RUN] would delete" if dry_run else "deleted"

        if not self.artist_root.exists():
            return {
                "album_folders_removed": 0,
                "artist_folders_removed": 0,
                "removed": [],
                "errors": [f"artist root not found: {self.artist_root}"],
            }

        for artist_folder in sorted(self.artist_root.iterdir()):
            if not artist_folder.is_dir():
                continue

            for album_folder in sorted(artist_folder.iterdir()):
                if not album_folder.is_dir():
                    continue

                if self._is_empty(album_folder):
                    reason = "empty (no files)"
                elif not self._has_audio(album_folder):
                    reason = "no audio files"
                else:
                    continue

                rel = album_folder.relative_to(self.artist_root)
                log.info("%s: %s (%s)", label, rel, reason)
                if not dry_run:
                    try:
                        shutil.rmtree(album_folder)
                    except (PermissionError, OSError) as exc:
                        msg = f"could not delete {rel}: {exc}"
                        log.warning(msg)
                        errors.append(msg)
                        continue
                album_removed += 1
                removed.append(str(rel))

            # Remove the artist folder if it's now empty.
            try:
                if artist_folder.is_dir() and not any(artist_folder.iterdir()):
                    log.info("%s: %s/ (artist folder now empty)", label, artist_folder.name)
                    if not dry_run:
                        artist_folder.rmdir()
                    artist_removed += 1
                    removed.append(f"{artist_folder.name}/")
            except (PermissionError, OSError) as exc:
                msg = f"could not delete artist folder {artist_folder.name}/: {exc}"
                log.warning(msg)
                errors.append(msg)

        return {
            "album_folders_removed": album_removed,
            "artist_folders_removed": artist_removed,
            "removed": removed,
            "errors": errors,
        }

    # ── scan pass ───────────────────────────────────────────────────────
    def scan(self, dry_run: bool = False, cleanup: bool = False) -> dict:
        """Scan the library and rebuild ``album_list.json``.

        Args:
            dry_run: If True, do not delete folders and do not write
                ``album_list.json`` — just report what would change.
            cleanup: If True, run the (non-interactive) cleanup pass
                first. Off by default because folder deletion is
                destructive — the caller must explicitly opt in. See the
                module docstring.

        Returns:
            A result dict::

                {
                    "dry_run": bool,
                    "total": int,            # albums found
                    "new": int,              # not present in previous album_list
                    "with_mb_id": int,
                    "without_mb_id": int,
                    "new_albums": [{"artist": str, "album": str}, ...],
                    "cleanup": {...} or None,   # _cleanup_folders() result
                    "errors": [str, ...],
                }
        """
        errors: list[str] = []
        cleanup_result: dict | None = None

        if cleanup:
            log.info("running cleanup pass (dry_run=%s)", dry_run)
            cleanup_result = self._cleanup_folders(dry_run)
            errors.extend(cleanup_result["errors"])

        if not self.artist_root.exists():
            errors.append(f"artist root not found: {self.artist_root}")
            return {
                "dry_run": dry_run,
                "total": 0,
                "new": 0,
                "with_mb_id": 0,
                "without_mb_id": 0,
                "new_albums": [],
                "cleanup": cleanup_result,
                "errors": errors,
            }

        log.info("scanning library at %s", self.artist_root)

        # Previous album list — used to count what's new since last scan.
        existing = store.album_list.read()

        albums: dict[str, dict] = {}
        new_albums: list[dict] = []

        for artist_folder in sorted(self.artist_root.iterdir()):
            if not artist_folder.is_dir():
                continue
            for album_folder in sorted(artist_folder.iterdir()):
                if not album_folder.is_dir():
                    continue

                # First audio file anywhere under the album folder.
                audio_file: Path | None = None
                for f in sorted(album_folder.rglob("*")):
                    if f.suffix.lower() in AUDIO_EXTS:
                        audio_file = f
                        break
                if audio_file is None:
                    continue

                tags = self._get_tags(audio_file)
                folder_name = album_folder.name

                mb_id: str | None = None
                for key in _MB_RELEASE_KEYS:
                    val = tags.get(key)
                    if val:
                        mb_id = _first(val, "")
                        break

                album_name = _first(tags.get("album"), folder_name)
                artist_name = _first(tags.get("albumartist"), artist_folder.name)

                entry = {
                    "artist": artist_name,
                    "album": album_name,
                    "mb_release_id": mb_id,
                    "folder": str(album_folder.relative_to(self.artist_root)),
                }
                albums[folder_name] = entry

                if folder_name not in existing:
                    new_albums.append({"artist": artist_name, "album": album_name})
                    log.info("  + %s — %s", artist_name, album_name)

        if not dry_run:
            store.album_list.write(albums)
            log.info("wrote %d albums to %s", len(albums), settings.album_list_file)

        total = len(albums)
        with_mb = sum(1 for a in albums.values() if a["mb_release_id"])

        result = {
            "dry_run": dry_run,
            "total": total,
            "new": len(new_albums),
            "with_mb_id": with_mb,
            "without_mb_id": total - with_mb,
            "new_albums": new_albums,
            "cleanup": cleanup_result,
            "errors": errors,
        }
        log.info(
            "scan complete: total=%d new=%d with_mb=%d without_mb=%d%s",
            total, len(new_albums), with_mb, total - with_mb,
            " (dry run — album_list.json not written)" if dry_run else "",
        )
        return result
