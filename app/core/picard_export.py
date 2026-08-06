"""PicardExporter — ported from ``export_mbids.py``.

Builds and maintains a flat list of every artist in the library together
with their MusicBrainz album-artist id, in the shape::

    [
      {"Artist": "Kenji Kawai",    "MusicBrainzId": "abc-123-..."},
      {"Artist": "Shoji Meguro",   "MusicBrainzId": "def-456-..."},
      ...
    ]

This is what gets uploaded to a private GitHub Gist and consumed by
external tools (and Picard's Post-Tagging Action triggers a refresh of
the just-edited artist via :meth:`export_one`).

Two design notes carried from the original script:

* **Single-artist runs merge.** ``export_one`` reads the existing file,
  upserts the one entry, and writes back. A Picard save of one album
  must not wipe the rest of the catalogue.

* **Output stays a list on disk.** Internally we use a dict keyed by
  artist name for O(1) merge, but the file is always saved as a
  case-insensitively sorted *list* of objects — matching what the gist
  consumers expect.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import httpx

from app.config import settings
from app.core.artist_fixer import (
    _find_first_audio,
    _read_mb_album_artist_id,
)
from app.storage.json_store import store

log = logging.getLogger("music-lib-helper.picard")

GIST_API_BASE   = "https://api.github.com"
GIST_FILENAME   = "artists_mbids.json"
GIST_DESCRIPTION = "MusicBrainz Artist ID export"


class PicardExporter:
    """Extracts MB album-artist ids from on-disk audio tags and persists
    them; optionally syncs to a GitHub Gist.
    """

    def __init__(self) -> None:
        self.artist_root: Path = settings.app_music_dir / "synced_music" / "Artist"

    # ── public: single-artist (the Picard hook) ─────────────────────────
    def export_one(
        self, artist_folder: str | Path, *, dry_run: bool = False,
    ) -> dict[str, Any]:
        """Process one artist folder.

        ``artist_folder`` is whatever Picard's ``%directory%`` token
        expanded to — could be an absolute container path, a host path,
        or just the artist folder name. We try the literal path first,
        then fall back to ``artist_root / basename``.

        Returns a result dict suitable for the API response. Writes the
        merged file and uploads to gist (if a token is configured) unless
        ``dry_run`` is set, in which case nothing is persisted.
        """
        resolved = self._resolve_artist_folder(artist_folder)
        if resolved is None:
            return {
                "ok": False, "artist": Path(str(artist_folder)).name,
                "mb_id": None, "is_new": False, "gist_updated": False,
                "message": f"artist folder not found: {artist_folder}",
            }

        audio_file = _find_first_audio(resolved)
        if audio_file is None:
            return {
                "ok": False, "artist": resolved.name,
                "mb_id": None, "is_new": False, "gist_updated": False,
                "message": "no audio files in artist folder",
            }

        mb_id = _read_mb_album_artist_id(audio_file)
        if not mb_id:
            return {
                "ok": False, "artist": resolved.name,
                "mb_id": None, "is_new": False, "gist_updated": False,
                "message": "no MusicBrainz album-artist id in tags",
            }

        existing = self._load_existing_dict()
        is_new = resolved.name not in existing

        if dry_run:
            gist_updated = False
        else:
            existing[resolved.name] = {"Artist": resolved.name, "MusicBrainzId": mb_id}
            self._save_dict(existing)
            gist_updated = self._upload_to_gist()

        log.info("exported %s → %s (new=%s, gist=%s, dry_run=%s)",
                 resolved.name, mb_id, is_new, gist_updated, dry_run)
        verb = ("would add new entry" if is_new else "would update existing entry") \
            if dry_run else ("added new entry" if is_new else "updated existing entry")
        return {
            "ok": True, "artist": resolved.name, "mb_id": mb_id,
            "is_new": is_new, "gist_updated": gist_updated,
            "message": verb, "dry_run": dry_run,
        }

    # ── public: full-library refresh ────────────────────────────────────
    def export_all(self, *, dry_run: bool = False) -> dict[str, Any]:
        """Walk every artist folder under the music root and rebuild the
        list. *Merges* with the existing file rather than replacing — so
        artists whose folders are temporarily missing are preserved.
        Writes nothing when ``dry_run`` is set.
        """
        if not self.artist_root.exists():
            return {
                "ok": False, "processed": 0, "new": 0, "skipped": 0,
                "gist_updated": False,
                "errors": [f"artist root not found: {self.artist_root}"],
            }

        existing = self._load_existing_dict()
        processed = new = skipped = 0
        errors: list[str] = []

        for folder in sorted(self.artist_root.iterdir()):
            if not folder.is_dir():
                continue
            processed += 1
            audio_file = _find_first_audio(folder)
            if audio_file is None:
                skipped += 1
                errors.append(f"{folder.name}: no audio files")
                continue
            mb_id = _read_mb_album_artist_id(audio_file)
            if not mb_id:
                skipped += 1
                errors.append(f"{folder.name}: no MB album-artist id in tags")
                continue
            if folder.name not in existing:
                new += 1
            existing[folder.name] = {"Artist": folder.name, "MusicBrainzId": mb_id}

        if dry_run:
            gist_updated = False
        else:
            self._save_dict(existing)
            gist_updated = self._upload_to_gist()

        log.info("export-all: processed=%d new=%d skipped=%d gist=%s dry_run=%s",
                 processed, new, skipped, gist_updated, dry_run)
        return {
            "ok": True, "processed": processed, "new": new,
            "skipped": skipped, "gist_updated": gist_updated, "errors": errors,
            "dry_run": dry_run,
        }

    # ── internal ────────────────────────────────────────────────────────
    def _resolve_artist_folder(self, given: str | Path) -> Path | None:
        """Return the on-disk artist folder, trying the literal path first
        then falling back to ``artist_root / basename``."""
        p = Path(str(given))
        if p.is_absolute() and p.exists() and p.is_dir():
            return p
        # Fall back to basename lookup (handles host-path / Windows-path /
        # bare-name inputs uniformly).
        candidate = self.artist_root / p.name
        if candidate.exists() and candidate.is_dir():
            return candidate
        return None

    def _load_existing_dict(self) -> dict[str, dict]:
        """Read artists_mbids.json (a list on disk) into a dict keyed by
        Artist name for merging.
        """
        raw = store.artists_mbids.read()
        if not isinstance(raw, list):
            return {}
        return {e["Artist"]: e for e in raw if isinstance(e, dict) and "Artist" in e}

    def _save_dict(self, entries: dict[str, dict]) -> None:
        """Write the dict back out as a case-insensitively sorted list."""
        sorted_list = [entries[k] for k in sorted(entries.keys(), key=str.lower)]
        store.artists_mbids.write(sorted_list)
        log.info("wrote %d entries to %s",
                 len(sorted_list), store.artists_mbids.path)

    # ── Gist sync ───────────────────────────────────────────────────────
    def _upload_to_gist(self) -> bool:
        """PATCH the configured gist (or POST a new one if ``gist_id`` is
        empty). Returns True on a 2xx response. Skips quietly if no
        github_token is configured.
        """
        token = settings.github_token
        if not token or token == "PLACEHOLDER_ME":
            log.debug("skipping gist upload: no GITHUB_TOKEN configured")
            return False

        content = store.artists_mbids.path.read_text(encoding="utf-8")
        payload = {
            "description": GIST_DESCRIPTION,
            "public":      False,
            "files":       {GIST_FILENAME: {"content": content}},
        }
        headers = {
            "Authorization": f"token {token}",
            "Accept":        "application/vnd.github+json",
        }

        gist_id = (settings.gist_id or "").strip()
        try:
            if gist_id and gist_id != "PLACEHOLDER_ME":
                url = f"{GIST_API_BASE}/gists/{gist_id}"
                r = httpx.patch(url, json=payload, headers=headers, timeout=15.0)
            else:
                url = f"{GIST_API_BASE}/gists"
                r = httpx.post(url, json=payload, headers=headers, timeout=15.0)
        except httpx.HTTPError as exc:
            log.warning("gist upload failed: %s", exc)
            return False

        if r.status_code not in (200, 201):
            log.warning("gist upload returned HTTP %s: %s",
                        r.status_code, r.text[:200])
            return False
        log.info("gist upload ok (HTTP %s)", r.status_code)
        return True
