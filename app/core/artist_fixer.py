"""ArtistFixer — ported from ``on_artist_add.py`` + ``fix_artist_paths.py``.

Lidarr names new artist folders from the MusicBrainz *primary* name, which
for many Japanese artists is in kanji even when the user's library is
organised romanised. This service finds the correct on-disk folder for an
artist and updates Lidarr's path to match.

Three strategies are tried in order; the first that returns a path wins.

  1. **Mapped track file path** — if Lidarr already knows about any track
     files for the artist, the correct artist folder is just the
     ``ARTIST_ROOT``-depth prefix of any of those paths. (No disk scan
     needed.)

  2. **MB-id → on-disk folder map** — build a map of
     ``musicbrainz_albumartistid`` → artist folder by reading one audio
     file per artist folder under ``ARTIST_ROOT``. If the artist's MB id
     is present in that map, that folder is the correct path.

  3. **MB English alias + folder rename** — if neither of the above
     finds a path, look up the artist's preferred English alias from
     MusicBrainz, rename the kanji-named folder on disk to that alias,
     and use the new path.

Public methods:

* :meth:`fix_one`  — single-artist flow, called by the Lidarr
                     ``OnArtistAdd`` wrapper.
* :meth:`fix_all`  — bulk fix, with ``dry_run`` support.
* :meth:`suggest_all` — read-only listing of non-Latin artists and what
                        we'd do with each (drives ``GET /artist/paths``).
"""

from __future__ import annotations

import logging
import time
import unicodedata
from pathlib import Path
from typing import Any

import httpx
from mutagen import File as MutagenFile  # type: ignore[import-untyped]

from app.config import settings
from app.core.library_scanner import LibraryScanner
from app.core.lidarr_client import LidarrClient
from app.core.mb_client import MBClient
from app.core.notifier import Notifier

log = logging.getLogger("lidarr-helper.artist_fixer")

AUDIO_EXTENSIONS = {".mp3", ".flac", ".ogg", ".opus", ".m4a", ".aac", ".wav", ".ape"}


# ── tag-reading helpers ────────────────────────────────────────────────────
def _decode_value(val: Any) -> str:
    """Coerce a mutagen tag value (often bytes-y) to a clean str."""
    if isinstance(val, bytes):
        return val.decode("utf-8", errors="replace")
    s = str(val)
    if s.startswith("b'") and s.endswith("'"):
        s = s[2:-1]
    elif s.startswith('b"') and s.endswith('"'):
        s = s[2:-1]
    return s


def _read_mb_album_artist_id(filepath: Path) -> str | None:
    """Extract the MusicBrainz album-artist id from an audio file's tags.

    Probes the format-specific keys: Vorbis (FLAC/OGG), ID3 (MP3),
    and iTunes-style M4A. Returns None on any failure.
    """
    try:
        audio = MutagenFile(str(filepath), easy=False)
    except Exception:  # noqa: BLE001 — mutagen can raise many types
        return None
    if audio is None or not getattr(audio, "tags", None):
        return None
    tags = audio.tags

    # Vorbis (FLAC, OGG)
    for key in ("MUSICBRAINZ_ALBUMARTISTID", "musicbrainz_albumartistid"):
        if key in tags:
            val = tags[key]
            return _decode_value(val[0] if isinstance(val, list) else val)

    # ID3 (MP3) — frames use a TXXX prefix and a colon-suffixed description
    for key in list(tags.keys()):
        key_upper = key.upper()
        if "MUSICBRAINZ ALBUM ARTIST ID" in key_upper or \
           "MUSICBRAINZ_ALBUMARTISTID" in key_upper:
            frame = tags[key]
            if hasattr(frame, "text") and frame.text:
                return _decode_value(frame.text[0])
            if isinstance(frame, list) and frame:
                return _decode_value(frame[0])

    # M4A
    itunes_key = "----:com.apple.iTunes:MusicBrainz Album Artist Id"
    if itunes_key in tags:
        val = tags[itunes_key]
        if isinstance(val, list) and val:
            item = val[0]
            if isinstance(item, bytes):
                return item.decode("utf-8", errors="replace")
            if hasattr(item, "value"):
                return _decode_value(item.value)
            return _decode_value(item)

    return None


def _find_first_audio(folder: Path) -> Path | None:
    """Return the first audio file found anywhere under ``folder``, or None."""
    for f in sorted(folder.rglob("*")):
        if f.is_file() and f.suffix.lower() in AUDIO_EXTENSIONS:
            return f
    return None


def is_non_latin(text: str) -> bool:
    """True if any letter character in ``text`` is non-Latin/non-ASCII.

    Mirrors the predicate used by both original scripts.
    """
    for ch in text:
        cat = unicodedata.category(ch)
        nm = unicodedata.name(ch, "")
        if cat.startswith("L") and "LATIN" not in nm and "COMMON" not in nm:
            return True
    return False


# ── ArtistFixer ────────────────────────────────────────────────────────────
class ArtistFixer:
    """Resolves and applies correct on-disk paths for Lidarr artists."""

    def __init__(
        self,
        lidarr: LidarrClient | None = None,
        mb: MBClient | None = None,
        notifier: Notifier | None = None,
        scanner: LibraryScanner | None = None,
    ) -> None:
        self.lidarr = lidarr or LidarrClient()
        self.mb = mb or MBClient()
        self.notifier = notifier or Notifier()
        self.scanner = scanner or LibraryScanner()
        self.artist_root: Path = settings.app_music_dir / "synced_music" / "Artist"

    # ── disk indexing ───────────────────────────────────────────────────
    def _build_mb_id_to_folder_map(self) -> dict[str, str]:
        """Scan the artist root and map ``mb_artist_id`` → folder path."""
        mb_map: dict[str, str] = {}
        if not self.artist_root.exists():
            log.warning("artist root not found: %s", self.artist_root)
            return mb_map
        for artist_folder in sorted(self.artist_root.iterdir()):
            if not artist_folder.is_dir():
                continue
            audio_file = _find_first_audio(artist_folder)
            if audio_file is None:
                continue
            mb_id = _read_mb_album_artist_id(audio_file)
            if mb_id:
                mb_map[mb_id] = str(artist_folder)
        log.info("built mb_id→folder map: %d entries", len(mb_map))
        return mb_map

    # ── strategy chain ──────────────────────────────────────────────────
    def _resolve(
        self,
        artist: dict,
        mb_map: dict[str, str],
        *,
        dry_run: bool,
    ) -> tuple[str | None, str, str | None]:
        """Return ``(correct_path, strategy, mb_alias_used)``.

        ``correct_path`` is None if no strategy worked. ``strategy`` is
        one of ``"mapped_track"``, ``"mb_id_map"``, ``"mb_alias_rename"``,
        or ``"none"``. ``mb_alias_used`` is set only for strategy 3.
        """
        artist_id = artist.get("id")
        mb_id = artist.get("foreignArtistId") or ""
        current_path = artist.get("path") or ""

        # ── 1: derive from an existing mapped track file ────────────────
        try:
            track_files = self.lidarr.get_track_files(artist_id) if artist_id else []
        except httpx.HTTPError as exc:
            log.debug("strategy 1 (trackfile) failed for artist %s: %s", artist_id, exc)
            track_files = []
        if track_files:
            first_track = track_files[0].get("path") or ""
            # Only trust Strategy 1 if the trackfile path is actually under
            # artist_root. Otherwise we'd return a wrong-prefix slice of an
            # unrelated path (e.g. a misconfigured Lidarr that points
            # elsewhere) and silently corrupt the artist record.
            root_str = str(self.artist_root).rstrip("/") + "/"
            if first_track.startswith(root_str):
                parts = Path(first_track).parts
                root_depth = len(self.artist_root.parts)
                if len(parts) > root_depth:
                    correct = str(Path(*parts[: root_depth + 1]))
                    return correct, "mapped_track", None

        # ── 2: mb_id → on-disk folder map ───────────────────────────────
        if mb_id and mb_id in mb_map:
            return mb_map[mb_id], "mb_id_map", None

        # ── 3: MB English alias + rename kanji folder ───────────────────
        if mb_id:
            english = self.mb.english_alias(mb_id)
            if english:
                kanji_folder = self.artist_root / Path(current_path).name
                new_folder = self.artist_root / english
                if kanji_folder.exists():
                    if not new_folder.exists() and not dry_run:
                        try:
                            kanji_folder.rename(new_folder)
                            log.info("renamed %s → %s", kanji_folder.name, english)
                        except OSError as exc:
                            log.warning("rename %s → %s failed: %s",
                                        kanji_folder.name, english, exc)
                            return None, "rename_failed", english
                    return str(new_folder), "mb_alias_rename", english
                log.debug("kanji folder not on disk: %s", kanji_folder)

        return None, "none", None

    # ── public: single-artist fix ───────────────────────────────────────
    def fix_one(
        self,
        artist_id: int | str,
        mb_id: str,
        artist_name: str,
        artist_path: str,
        *,
        trigger_scan: bool = True,
        settle_delay: float = 3.0,
    ) -> dict[str, Any]:
        """Resolve and apply the correct path for a single artist.

        Called by the Lidarr ``OnArtistAdd`` thin wrapper.

        ``settle_delay`` gives Lidarr a moment to finish its own write of
        the new artist before we PUT a path change back to it — the
        original script used 3s; set to 0 in tests.
        """
        log.info("fix-one: artist=%s mb=%s path=%s", artist_name, mb_id, artist_path)
        folder_name = Path(artist_path).name

        if not is_non_latin(folder_name):
            return {
                "ok": True,
                "new_path": None,
                "strategy": "skip_already_latin",
                "mb_alias": None,
                "message": "path is already Latin — nothing to do",
                "discord_sent": False,
            }

        if settle_delay > 0:
            time.sleep(settle_delay)

        mb_map = self._build_mb_id_to_folder_map()

        # Synthesise a Lidarr-shaped record for ``_resolve``; we'll fetch
        # the real one before PUTting.
        fake_artist = {"id": artist_id, "foreignArtistId": mb_id, "path": artist_path}
        correct_path, strategy, alias = self._resolve(fake_artist, mb_map, dry_run=False)

        if correct_path is None:
            sent = self.notifier.send(
                "artist",
                "⚠️ Artist Path Fix Failed",
                f"**{artist_name}**\nCould not determine correct path.\n"
                f"MB ID: `{mb_id}`",
                success=False,
            )
            return {
                "ok": False, "new_path": None, "strategy": strategy,
                "mb_alias": alias,
                "message": "no strategy succeeded — manual intervention needed",
                "discord_sent": sent,
            }

        if correct_path == artist_path:
            return {
                "ok": True, "new_path": None, "strategy": strategy,
                "mb_alias": alias, "message": "path already correct",
                "discord_sent": False,
            }

        # Update Lidarr. Fetch full record first so PUT body has every field.
        try:
            artist = self.lidarr.get_artist(artist_id)
            artist["path"] = correct_path
            self.lidarr.update_artist(artist_id, artist)
        except httpx.HTTPError as exc:
            log.error("Lidarr update for %s failed: %s", artist_name, exc)
            self.notifier.send(
                "artist",
                "⚠️ Artist Path Fix Failed",
                f"**{artist_name}**\nCould not update Lidarr path.\n"
                f"MB ID: `{mb_id}`\nError: `{exc}`",
                success=False,
            )
            return {
                "ok": False, "new_path": correct_path, "strategy": strategy,
                "mb_alias": alias, "message": f"Lidarr update failed: {exc}",
                "discord_sent": False,
            }

        # Notify + refresh the album list now that disk may have changed.
        sent = self.notifier.send(
            "artist",
            "✅ Artist Path Fixed",
            f"**{artist_name}**\n`{folder_name}` → `{Path(correct_path).name}`\n"
            f"_strategy: {strategy}_",
            success=True,
        )
        if trigger_scan:
            try:
                self.scanner.scan(dry_run=False, cleanup=False)
            except Exception as exc:  # noqa: BLE001
                log.warning("post-fix library scan failed: %s", exc)

        return {
            "ok": True, "new_path": correct_path, "strategy": strategy,
            "mb_alias": alias, "message": "path updated",
            "discord_sent": sent,
        }

    # ── public: bulk fix ────────────────────────────────────────────────
    def fix_all(self, *, dry_run: bool = False, trigger_scan: bool = True) -> dict[str, Any]:
        """Fix every Lidarr artist whose folder name is non-Latin.

        Returns an aggregated result dict suitable for the API response.
        """
        log.info("fix-all: dry_run=%s", dry_run)
        try:
            artists = self.lidarr.list_artists()
        except httpx.HTTPError as exc:
            log.error("could not list Lidarr artists: %s", exc)
            return {
                "dry_run": dry_run, "fixed": [], "would_fix": [],
                "not_found": [], "unchanged": [],
                "error": f"could not list Lidarr artists: {exc}",
            }

        problem = [a for a in artists if is_non_latin(Path(a.get("path") or "").name)]
        log.info("found %d non-Latin artists out of %d total",
                 len(problem), len(artists))

        if not problem:
            return {"dry_run": dry_run, "fixed": [], "would_fix": [],
                    "not_found": [], "unchanged": [], "error": None}

        mb_map = self._build_mb_id_to_folder_map()
        fixed:     list[dict] = []
        would_fix: list[dict] = []
        not_found: list[dict] = []
        unchanged: list[str] = []

        for artist in problem:
            name = artist.get("artistName") or ""
            current = artist.get("path") or ""
            correct, strategy, alias = self._resolve(artist, mb_map, dry_run=dry_run)

            if correct is None:
                not_found.append({"artist_name": name, "reason": strategy})
                continue
            if correct == current:
                unchanged.append(name)
                continue

            row = {"artist_name": name, "old_path": current, "new_path": correct,
                   "strategy": strategy, "mb_alias": alias}

            if dry_run:
                would_fix.append(row)
                continue

            try:
                artist["path"] = correct
                self.lidarr.update_artist(artist["id"], artist)
                fixed.append(row)
            except httpx.HTTPError as exc:
                log.warning("Lidarr update for %s failed: %s", name, exc)
                not_found.append({"artist_name": name,
                                  "reason": f"lidarr_update_failed: {exc}"})

        if not dry_run and trigger_scan and (fixed or any(
                r["strategy"] == "mb_alias_rename" for r in fixed)):
            try:
                self.scanner.scan(dry_run=False, cleanup=False)
            except Exception as exc:  # noqa: BLE001
                log.warning("post-fix library scan failed: %s", exc)

        return {"dry_run": dry_run, "fixed": fixed, "would_fix": would_fix,
                "not_found": not_found, "unchanged": unchanged, "error": None}

    # ── public: suggestions only (read-only) ────────────────────────────
    def suggest_all(self) -> list[dict[str, Any]]:
        """Return ``[{artist_id, artist_name, current_path, suggested_path,
        mb_alias, strategy}]`` for every artist with a non-Latin path.

        Read-only: never renames folders, never updates Lidarr. Used by
        ``GET /api/v1/artist/paths``.
        """
        try:
            artists = self.lidarr.list_artists()
        except httpx.HTTPError as exc:
            log.error("could not list Lidarr artists: %s", exc)
            return []

        problem = [a for a in artists if is_non_latin(Path(a.get("path") or "").name)]
        if not problem:
            return []

        mb_map = self._build_mb_id_to_folder_map()
        out: list[dict[str, Any]] = []
        for artist in problem:
            correct, strategy, alias = self._resolve(artist, mb_map, dry_run=True)
            out.append({
                "artist_id":      artist.get("id"),
                "artist_name":    artist.get("artistName") or "",
                "current_path":   artist.get("path") or "",
                "suggested_path": correct,
                "mb_alias":       alias,
                "strategy":       strategy,
            })
        return out
