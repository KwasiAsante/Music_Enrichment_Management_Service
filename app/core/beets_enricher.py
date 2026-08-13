"""BeetsEnricher — ported from ``beets-enrich.py`` + ``on_album_download.py``.

The heart of the helper service. Drives the actual VGMDB tag enrichment
via the ``beet`` CLI, plus everything that happens around it:

* ``enrich_album(info, allow_search=...)`` — single-album flow.
  Resolves a VGMDB id (mapping → vgmdb-prefixed MBID → optional auto-search
  via MBClient/VGMDBMapper), runs ``beet import -S vgmdb:<id>``, parses
  the match percentage from the output, rewrites any non-Latin
  ``albumartist``/``artist`` tags to the English name, updates the
  enriched / skipped logs, sends Discord notifications, and posts a
  MusicBrainz seed URL if MB is missing the VGMDB link.

* ``run_bulk(...)`` — iterates ``album_list.json``, group-filters by
  artist, runs the per-artist *should-enrich?* heuristic (allow-list,
  block-list, then MB country/area/alias fallback), and calls
  ``enrich_album(allow_search=False)`` for each one. Supports
  ``dry_run``, ``--redo`` (clear enriched-log entries to re-process),
  ``--redo-skipped`` (pull entries out of the skipped log), and a
  progress callback so the API can stream updates.

Design notes:

* **Subprocess invocations are isolated.** ``_beet_import`` and
  ``_beet_remove`` are the only places that touch ``subprocess.run``;
  tests monkeypatch the module-level :data:`_subprocess_run` indirection
  so they can drive deterministic responses without a real ``beet``
  install.

* **Dependencies inject through ``__init__``** (LidarrClient pattern).
  No-arg construction picks real ones; tests pass fakes.

* **Two activity-log categories** are emitted: ``enrich`` for normal
  per-album events and ``enrich_summary`` for the end-of-bulk roll-up.
  ``GET /enrich/log`` filters to ``enrich``.
"""

from __future__ import annotations

import logging
import os
import re
import subprocess
import unicodedata
from pathlib import Path
from typing import Any, Callable

from mutagen import File as MutagenFile  # type: ignore[import-untyped]

from app.config import settings
from app.core.library_scanner import LibraryScanner
from app.core.mb_client import MBClient
from app.core.mb_link import MBLinkChecker
from app.core.notifier import Category, Notifier
from app.core.vgmdb_mapper import VGMDBMapper
from app.storage import db
from app.storage.json_store import store

log = logging.getLogger("music-lib-helper.beets_enricher")

AUDIO_EXTENSIONS = {".mp3", ".flac", ".ogg", ".opus", ".m4a", ".aac", ".wav", ".ape"}

# Tag fields beet/VGMplug may have written non-Latin values into.
_ARTIST_TAG_KEYS = (
    "albumartist", "artist", "album_artist",
    "album_artists", "albumartists", "artists", "album artist",
)

# Indirection so tests can monkeypatch the subprocess call without
# replacing the whole module's subprocess import.
_subprocess_run = subprocess.run


# ── parsers (pure functions — easy to unit test) ───────────────────────────
def parse_match_percentage(output: str) -> str:
    """Pull a match-% out of beet's import output, or return ``"unknown"``.

    Skips ``Missing tracks`` lines (which contain misleading percentages)
    and considers three patterns: ``Match (XX.X%)`` (default beets),
    ``Similarity: XX.X%`` (some plugins), and ``similarity: 0.XXX``
    (decimal form).
    """
    clean_lines = [
        line for line in output.splitlines()
        if not line.strip().startswith("Missing tracks")
        and not line.strip().startswith("!")
    ]
    clean = "\n".join(clean_lines)

    m = re.search(r"Match\s*\((\d+(?:\.\d+)?)\s*%\)", clean)
    if m:
        return f"{m.group(1)}%"
    m = re.search(r"[Ss]imilarity[:\s]+(\d+(?:\.\d+)?)\s*%", clean)
    if m:
        return f"{m.group(1)}%"
    m = re.search(r"similarity[:\s]+(0\.\d+)", clean, re.IGNORECASE)
    if m:
        return f"{round(float(m.group(1)) * 100, 1)}%"
    return "unknown"


def parse_skip_reasons(output: str) -> str:
    """Collect ``≠`` mismatch fields and ``Missing tracks`` lines into one
    semicolon-separated reason string."""
    reasons: list[str] = []
    for line in output.splitlines():
        line = line.strip()
        if line.startswith("≠"):
            reasons.append(f"mismatched: {line.lstrip('≠').strip()}")
        if line.startswith("Missing tracks"):
            reasons.append(line)
    return "; ".join(reasons)


_ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")


def strip_ansi(s: str) -> str:
    return _ANSI_RE.sub("", s)


def _has_non_latin(text: str) -> bool:
    """True if any letter character is non-Latin/non-ASCII."""
    for ch in (text or ""):
        cat = unicodedata.category(ch)
        nm = unicodedata.name(ch, "")
        if cat.startswith("L") and "LATIN" not in nm and "COMMON" not in nm:
            return True
    return False


# ── BeetsEnricher ──────────────────────────────────────────────────────────
class BeetsEnricher:
    # Artists explicitly *enriched* without an MB country lookup —
    # confirmed Japanese, or otherwise definitely on VGMDB.
    ENRICH_ARTISTS: frozenset[str] = frozenset({
        "2PM", "ANANT-GARDE EYES", "KANIKAPILA", "SILENT BLACK KITTY",
        "SPYAIR", "SUPER BEAVER", "Sangatsu no Phantasia", "Shiori Tomita",
        "Simon (Tetsuya Kakihara)、Kamina (Katsuyuki Konishi)、Yoko (Marina Inoue)",
        "Sora Amamiya", "Tetsuya Kobayashi", "ViViD", "Yoko (Marina Inoue)",
        "Younha", "pe'zmoku", "Shoji Meguro",
        "Animélangé feat. DJ Mia-chan & Captain Stephan",
        "Hacla Label", "Various Artists", "TCY FORCE & ☆Taku Takahashi",
    })

    JAPANESE_COUNTRIES: frozenset[str] = frozenset({"JP"})
    SKIP_COUNTRIES: frozenset[str] = frozenset({
        "US", "GB", "CA", "AU", "NZ", "DE", "FR", "SE", "NO", "DK", "FI",
    })

    def __init__(
        self,
        mb: MBClient | None = None,
        mapper: VGMDBMapper | None = None,
        notifier: Notifier | None = None,
        mb_link: MBLinkChecker | None = None,
        scanner: LibraryScanner | None = None,
    ) -> None:
        self.mb = mb or MBClient()
        self.mapper = mapper or VGMDBMapper()
        self.notifier = notifier or Notifier()
        self.mb_link = mb_link or MBLinkChecker()
        self.scanner = scanner or LibraryScanner()
        self.artist_root: Path = settings.app_music_dir / "synced_music" / "Artist"

    # ── public: per-album entry point ───────────────────────────────────
    def enrich_album(
        self,
        info: dict[str, Any],
        *,
        allow_search: bool = False,
        dry_run: bool = False,
        seed_category: Category = "mb_seed_dl",
    ) -> dict[str, Any]:
        """Enrich one album. ``info`` shape::

            {
              "artist":         str,
              "album":          str,
              "mb_release_id":  str | None,
              "folder":         str   # relative to artist_root, or absolute
            }

        ``allow_search`` lets the resolver auto-fill the VGMDB id via
        :class:`VGMDBMapper.search` when no mapping exists — used by the
        Lidarr OnAlbumDownload hook, off for bulk.

        Returns a result dict suitable for the API + the activity log.
        """
        artist = info.get("artist") or ""
        album = info.get("album") or ""
        mb_release_id = info.get("mb_release_id") or ""
        album_folder = self._resolve_album_folder(info)

        if album_folder is None or not album_folder.exists():
            return self._fail("folder_not_found", artist, album, mb_release_id,
                              f"album folder not found: {info.get('folder')!r}")

        # ── already-enriched short-circuit ──────────────────────────────
        enriched = store.enriched_set()
        if mb_release_id and mb_release_id in enriched:
            return {
                "ok": True, "already_enriched": True,
                "vgmdb_id": None, "match": None,
                "source": "enriched_log", "message": "already enriched",
                "artist": artist, "album": album, "mb_release_id": mb_release_id,
            }

        # ── resolve VGMDB id ────────────────────────────────────────────
        vgmdb_id, source, search_hint = self._resolve_vgmdb_id(
            info, album_folder, allow_search=allow_search,
        )
        if source == "skip_sentinel":
            return self._record_skip(
                mb_release_id, artist, album, album_folder,
                vgmdb_id="skip", match="N/A",
                reason="explicitly mapped as 'skip' (not on VGMDB)",
            )
        if vgmdb_id is None:
            self.notifier.send(
                "enrich",
                "⚠️ No VGMDB Match Found",
                f"**{artist}** — {album}\nMB release: `{mb_release_id}`",
                success=False,
            )
            return self._record_skip(
                mb_release_id or info.get("folder", "?"),
                artist, album, album_folder,
                vgmdb_id="N/A", match="N/A",
                reason=search_hint or "no VGMDB mapping found",
            )

        log.info("enriching %s — %s with vgmdb:%s (source=%s)",
                 artist, album, vgmdb_id, source)
        if dry_run:
            return {
                "ok": True, "dry_run": True, "vgmdb_id": vgmdb_id,
                "source": source, "match": None,
                "message": "dry run — no beet invoked",
                "artist": artist, "album": album, "mb_release_id": mb_release_id,
            }

        # ── beet remove + import ────────────────────────────────────────
        self._beet_remove(album_folder)
        ok, raw_output = self._beet_import(album_folder, vgmdb_id)
        output = strip_ansi(raw_output)
        match_pct = parse_match_percentage(output)

        # beet exits 0 even when it "Skipping." — detect that explicitly.
        if ok and "Skipping." in output:
            ok = False
            skip_reasons = parse_skip_reasons(output)
            beet_reason = "beet skipped (match below threshold)"
            if skip_reasons:
                beet_reason += f" — {skip_reasons}"
            log.info("  ✗ %s", beet_reason)
            self.notifier.send(
                "enrich",
                "❌ VGMDB Enrichment Failed",
                f"**{artist}** — {album}\nMB release: `{mb_release_id}`\n"
                f"Match: {match_pct}\nReason: {beet_reason}",
                success=False,
            )
            return self._record_skip(
                mb_release_id or info.get("folder", "?"),
                artist, album, album_folder,
                vgmdb_id=vgmdb_id, match=match_pct, reason=beet_reason,
                threshold="strong_rec_thresh: 0.90",
            )

        if not ok:
            beet_reason = self._classify_failure(raw_output)
            self.notifier.send(
                "enrich",
                "❌ VGMDB Enrichment Failed",
                f"**{artist}** — {album}\nMB release: `{mb_release_id}`\n"
                f"Match: {match_pct}\nReason: {beet_reason}",
                success=False,
            )
            return self._record_skip(
                mb_release_id or info.get("folder", "?"),
                artist, album, album_folder,
                vgmdb_id=vgmdb_id, match=match_pct, reason=beet_reason,
            )

        # ── success path ────────────────────────────────────────────────
        tags_fixed = self._fix_non_latin_artist_tags(album_folder, artist)
        if mb_release_id:
            enriched.add(mb_release_id)
            store.save_enriched_set(enriched)

        # Drop the skipped-log entry if this album had previously failed.
        if mb_release_id:
            skipped = store.skipped_albums.read()
            if skipped.pop(mb_release_id, None) is not None:
                store.skipped_albums.write(skipped)

        # Notify + post MB seed URL.
        self.notifier.send(
            "enrich",
            "✅ VGMDB Enrichment Complete",
            f"**{artist}** — {album}\nvgmdb:{vgmdb_id}\nMatch: {match_pct}",
            success=True,
        )
        posted_to = self._post_mb_seed(
            mb_release_id, vgmdb_id, artist, album, category=seed_category,
        )

        db.add_activity(
            "enrich",
            f"enriched vgmdb:{vgmdb_id} ({match_pct})"
            + (f" — fixed {tags_fixed} non-Latin tags" if tags_fixed else ""),
            artist=artist, album=album,
        )

        return {
            "ok": True, "vgmdb_id": vgmdb_id, "source": source,
            "match": match_pct, "tags_fixed": tags_fixed,
            "seed_category": posted_to,
            "message": "enrichment complete",
            "artist": artist, "album": album, "mb_release_id": mb_release_id,
        }

    # ── public: bulk entry point ───────────────────────────────────────
    def run_bulk(
        self,
        *,
        dry_run: bool = False,
        artist_filter: list[str] | None = None,
        album_filters: list[str] | None = None,
        redo: list[str] | None = None,
        redo_skipped: bool = False,
        on_progress: Callable[[int, int, dict], None] | None = None,
    ) -> dict[str, Any]:
        """Iterate album_list and enrich each eligible album."""
        album_list = store.album_list.read()
        mapping = store.vgmdb_mapping.read()
        enriched_log = store.enriched_set()

        # Group by artist folder (first path segment).
        by_artist: dict[str, list[tuple[str, dict]]] = {}
        for folder_name, info in album_list.items():
            a_folder = Path(info.get("folder") or "").parts[0:1]
            key = a_folder[0] if a_folder else (info.get("artist") or "Unknown")
            by_artist.setdefault(key, []).append((folder_name, info))

        if artist_filter:
            wanted = {a.lower() for a in artist_filter}
            by_artist = {k: v for k, v in by_artist.items() if k.lower() in wanted}
            if not by_artist:
                return {
                    "dry_run": dry_run, "total": 0, "enriched": [],
                    "skipped": [], "failed": [], "no_map": [],
                    "error": f"no artists matched filter: {', '.join(artist_filter)}",
                }

        # --redo: clear enriched-log entries so they re-run.
        if redo:
            redo_lc = {a.lower() for a in redo}
            removed = set()
            for _, info in album_list.items():
                mb_id = info.get("mb_release_id") or ""
                if not mb_id or mb_id not in enriched_log:
                    continue
                if redo_lc and not any(q in (info.get("album") or "").lower()
                                       for q in redo_lc):
                    continue
                removed.add(mb_id)
            enriched_log -= removed
            store.save_enriched_set(enriched_log)
            log.info("--redo cleared %d entries from enriched log", len(removed))

        # --redo-skipped: pull eligible entries out of skipped log.
        redo_skip_ids: set[str] = set()
        if redo_skipped:
            skipped_log = store.skipped_albums.read()
            for mb_id, entry in list(skipped_log.items()):
                if entry.get("vgmdb_id") == "skip":
                    continue
                redo_skip_ids.add(mb_id)
                del skipped_log[mb_id]
            store.skipped_albums.write(skipped_log)
            log.info("--redo-skipped pulled %d entries", len(redo_skip_ids))

        # Plan-then-run so progress reports a stable total.
        plan: list[tuple[str, dict, str]] = []   # (artist_folder, info, decision)
        for artist_folder in sorted(by_artist.keys()):
            mb_artist_id = self._get_artist_mb_id(self.artist_root / artist_folder)
            decision, reason = self.should_enrich_artist(artist_folder, mb_artist_id)
            for fn, info in by_artist[artist_folder]:
                if not self._album_matches_filter(info, album_filters):
                    continue
                if redo_skipped and (info.get("mb_release_id") or "") not in redo_skip_ids:
                    continue
                plan.append((artist_folder, info, decision))

        total = len(plan)
        enriched: list[dict] = []
        skipped: list[dict] = []
        failed: list[dict] = []
        no_map: list[dict] = []

        for i, (artist_folder, info, decision) in enumerate(plan, start=1):
            mb_release_id = info.get("mb_release_id") or ""
            if decision == "no":
                skipped.append({"artist": info.get("artist"),
                                "album": info.get("album"),
                                "reason": "western artist (block-list)"})
                if on_progress: on_progress(i, total, info)
                continue
            if decision == "ask":
                skipped.append({"artist": info.get("artist"),
                                "album": info.get("album"),
                                "reason": "uncertain origin (skipped — interactive needed)"})
                if on_progress: on_progress(i, total, info)
                continue

            result = self.enrich_album(
                info,
                allow_search=False,
                dry_run=dry_run,
                seed_category="mb_seed_beets",
            )
            if result.get("ok"):
                if result.get("already_enriched"):
                    # treat as success (no work needed)
                    enriched.append({"artist": info.get("artist"),
                                     "album": info.get("album"),
                                     "already_enriched": True})
                else:
                    enriched.append({"artist": info.get("artist"),
                                     "album": info.get("album"),
                                     "vgmdb_id": result.get("vgmdb_id"),
                                     "match": result.get("match")})
            else:
                # Distinguish "no map" from other failures
                if result.get("source") in (None, "not_found"):
                    no_map.append({"artist": info.get("artist"),
                                   "album": info.get("album"),
                                   "reason": result.get("message")})
                else:
                    failed.append({"artist": info.get("artist"),
                                   "album": info.get("album"),
                                   "match": result.get("match"),
                                   "reason": result.get("message")})

            if on_progress: on_progress(i, total, info)

        summary = {
            "dry_run": dry_run, "total": total,
            "enriched": enriched, "skipped": skipped,
            "failed": failed, "no_map": no_map,
            "error": None,
        }
        db.add_activity(
            "enrich_summary",
            f"bulk: total={total} enriched={len(enriched)} "
            f"skipped={len(skipped)} failed={len(failed)} no_map={len(no_map)}"
            + (" [dry run]" if dry_run else ""),
            level="warning" if (failed or no_map) else "info",
        )
        if not dry_run and (enriched or failed):
            # Refresh album_list so any tag changes propagate.
            try:
                self.scanner.scan(dry_run=False, cleanup=False)
            except Exception as exc:  # noqa: BLE001
                log.warning("post-bulk library scan failed: %s", exc)
        return summary

    # ── Japanese-artist decision ────────────────────────────────────────
    def should_enrich_artist(
        self, artist_name: str, mb_artist_id: str | None,
    ) -> tuple[str, str]:
        """Returns ``(decision, reason)``. ``decision`` is ``"yes" | "no" | "ask"``.

        Mirrors the original ``beets-enrich.py`` policy:
        explicit block/allow lists, then MB country/area, then MB
        ``ja*`` aliases, else ``"ask"`` (which in service mode = skip).
        The block list is ``excluded_artists.json`` — the same list
        editable from the Mappings page's Excluded Artists panel, so
        excluding an artist there also stops bulk enrichment from
        touching them.
        """
        excluded = {a.lower() for a in store.excluded_artists.read()}
        if artist_name.lower() in excluded:
            return "no", "explicitly excluded"
        if artist_name in self.ENRICH_ARTISTS:
            return "yes", ""
        if not mb_artist_id:
            return "ask", "no MusicBrainz artist id in tags"
        data = self.mb.get_artist(mb_artist_id)
        if not data:
            return "ask", f"MB lookup failed for {mb_artist_id}"
        country = data.get("country") or ""
        area_name = ((data.get("area") or {}).get("name")) or ""
        if country in self.JAPANESE_COUNTRIES or area_name == "Japan":
            return "yes", ""
        if country in self.SKIP_COUNTRIES:
            return "no", ""
        for alias in data.get("aliases") or []:
            if (alias.get("locale") or "").startswith("ja"):
                return "yes", ""
        return "ask", (
            f"country={country or 'unknown'} area={area_name or 'unknown'} "
            "— neither Japanese nor a known western locale"
        )

    # ── private: resolve VGMDB id ───────────────────────────────────────
    def _resolve_vgmdb_id(
        self,
        info: dict,
        album_folder: Path,
        *,
        allow_search: bool,
    ) -> tuple[str | None, str, str | None]:
        """Returns ``(vgmdb_id, source, hint)``. ``source`` is the resolution
        path; ``hint`` is a human-readable explanation for failure cases.
        """
        mb_release_id = info.get("mb_release_id") or ""
        mapping = store.vgmdb_mapping.read()

        if mb_release_id in mapping:
            entry = mapping[mb_release_id]
            if entry.get("vgmdb_id") == "skip":
                log.debug("vgmdb resolve: skip sentinel for %s", mb_release_id)
                return None, "skip_sentinel", "explicitly skip-mapped"
            vgmdb_id = entry.get("vgmdb_id")
            log.debug("vgmdb resolve: mapping hit %s → %s", mb_release_id, vgmdb_id)
            return vgmdb_id, "mapping", None

        if mb_release_id.startswith("vgmdb-"):
            vgmdb_id = mb_release_id.removeprefix("vgmdb-")
            log.debug("vgmdb resolve: mb prefix %s → %s", mb_release_id, vgmdb_id)
            return vgmdb_id, "mb_release_id_prefix", None

        if not allow_search:
            log.debug(
                "vgmdb resolve: no mapping for %s (search disabled)", mb_release_id,
            )
            return None, "not_found", "no VGMDB mapping found"

        # On-the-fly resolution (Lidarr hook path).
        try:
            result = self.mapper.search(
                mb_release_id=mb_release_id or None,
                album=info.get("album") or "",
                artist=info.get("artist") or "",
                folder=info.get("folder"),
            )
        except Exception as exc:  # noqa: BLE001
            log.warning("on-the-fly search failed: %s", exc)
            return None, "not_found", f"search error: {exc}"

        if result.get("mb_vgmdb_id"):
            vgmdb_id = result["mb_vgmdb_id"]
            self._save_auto_mapping(info, vgmdb_id, source="mb_url_rel")
            return vgmdb_id, "mb_url_rel", None

        suggested = result.get("suggested")
        if suggested:
            vgmdb_id = suggested["vgmdb_id"]
            src = (
                "search_catalog" if result.get("catalog_hints")
                else "search_barcode" if result.get("barcode_hints")
                else "search_title"
            )
            self._save_auto_mapping(info, vgmdb_id, source=src)
            return vgmdb_id, src, None

        return None, "not_found", "no VGMDB candidates"

    def _save_auto_mapping(self, info: dict, vgmdb_id: str, *, source: str) -> None:
        """Persist a newly-resolved mapping so future runs hit it instantly."""
        mb_release_id = info.get("mb_release_id") or ""
        if not mb_release_id:
            return
        self.mapper.set_mapping(
            mb_release_id=mb_release_id,
            vgmdb_id=str(vgmdb_id),
            artist=info.get("artist") or "",
            album=info.get("album") or "",
            folder=info.get("folder") or "",
            source=source,
        )

    # ── private: subprocess wrappers ────────────────────────────────────
    def _beet_env(self) -> dict[str, str]:
        """Environment for ``beet`` subprocesses.

        Beets reads ``BEETSDIR`` from the OS environment, not from this
        app's pydantic settings — and docker-compose ``env_file`` entries
        can accidentally override the Dockerfile default with a host path
        that doesn't exist inside the container. Always pass the configured
        ``settings.beetsdir`` explicitly.
        """
        env = os.environ.copy()
        env["BEETSDIR"] = str(settings.beetsdir)
        return env

    def _beet_remove(self, album_folder: Path) -> bool:
        cmd = [settings.beet_bin, "remove", "-a", "-f", f"path:{album_folder}"]
        try:
            r = _subprocess_run(
                cmd, capture_output=True, text=True, timeout=30,
                env=self._beet_env(),
            )
            return r.returncode == 0
        except Exception as exc:  # noqa: BLE001
            log.debug("beet remove failed (non-fatal): %s", exc)
            return False

    def _beet_import(
        self, album_folder: Path, vgmdb_id: str,
    ) -> tuple[bool, str]:
        cmd = [
            settings.beet_bin, "import",
            "--nocopy", "--noincremental", "--write",
            "-S", f"vgmdb:{vgmdb_id}",
            str(album_folder),
        ]
        log.debug("beet import: %s", " ".join(cmd))
        try:
            r = _subprocess_run(
                cmd, input="a\n", capture_output=True, text=True,
                timeout=300, env=self._beet_env(),
            )
            output = (r.stdout + r.stderr).strip()
            log.debug(
                "beet import finished (rc=%s, %d bytes output):\n%s",
                r.returncode, len(output), output,
            )
            return r.returncode == 0, output
        except subprocess.TimeoutExpired:
            return False, "timeout"
        except FileNotFoundError as exc:
            return False, (
                f"beet binary not found at {settings.beet_bin!r}: {exc}"
            )
        except Exception as exc:  # noqa: BLE001
            return False, str(exc)

    # ── private: post-import tag fix ────────────────────────────────────
    def _fix_non_latin_artist_tags(
        self, album_folder: Path, english_name: str,
    ) -> int:
        """If beet/VGMplug wrote kanji into ``albumartist``/etc, replace
        with ``english_name``. Returns the number of files modified.
        """
        if not english_name:
            return 0
        fixed = 0
        for audio_path in album_folder.rglob("*"):
            if audio_path.suffix.lower() not in AUDIO_EXTENSIONS:
                continue
            try:
                audio = MutagenFile(str(audio_path), easy=True)
                if not audio or not audio.tags:
                    continue
                changed = False
                for tag in _ARTIST_TAG_KEYS:
                    val = audio.tags.get(tag, [])
                    current = val[0] if isinstance(val, list) and val else str(val or "")
                    if current and _has_non_latin(current):
                        audio.tags[tag] = [english_name]
                        changed = True
                if changed:
                    audio.save()
                    fixed += 1
            except Exception as exc:  # noqa: BLE001
                log.debug("tag fix failed for %s: %s", audio_path, exc)
        if fixed:
            log.info("fixed non-Latin artist tags in %d files → %s",
                     fixed, english_name)
        return fixed

    # ── private: misc helpers ───────────────────────────────────────────
    def _resolve_album_folder(self, info: dict) -> Path | None:
        """Return an absolute path to the album folder, or None if it can't
        be located. Accepts:

        * an absolute path already
        * a path relative to artist_root (the album_list shape)
        * the "Disc N" parent edge case from Lidarr's added-track-paths
        """
        raw = info.get("folder") or ""
        if not raw:
            return None
        p = Path(raw)
        if p.is_absolute():
            if p.exists() and p.is_dir():
                return p
            # The folder may live under artist_root with a different prefix —
            # try basename + parent fallback.
            return None
        candidate = self.artist_root / p
        if candidate.exists() and candidate.is_dir():
            return candidate
        return None

    def _get_artist_mb_id(self, artist_folder: Path) -> str | None:
        """First audio file's MUSICBRAINZ_ALBUMARTISTID, via the artist_fixer
        helper. Kept here as a thin import-style indirection so tests can
        monkeypatch the underlying read separately if they want.
        """
        from app.core.artist_fixer import (  # local import: avoid module cycle
            _find_first_audio, _read_mb_album_artist_id,
        )
        if not artist_folder.exists():
            return None
        audio = _find_first_audio(artist_folder)
        if not audio:
            return None
        return _read_mb_album_artist_id(audio)

    @staticmethod
    def _album_matches_filter(info: dict, filters: list[str] | None) -> bool:
        if not filters:
            return True
        name = (info.get("album") or "").lower()
        folder_album = Path(info.get("folder") or "").name.lower()
        return any(f.lower() in name or f.lower() in folder_album for f in filters)

    @staticmethod
    def _classify_failure(output: str) -> str:
        """Best-guess reason from beet's stderr / stdout when returncode != 0."""
        lower = output.lower()
        if "beet binary not found" in lower or "no such file or directory" in lower:
            return (
                f"beet binary not found at {settings.beet_bin!r} "
                "— check BEET_BIN in .env (use /usr/local/bin/beet in Docker)"
            )
        if "timeout" in lower:
            return "beet import timed out"
        if "no candidates" in lower:
            return "no VGMDB candidates found"
        if (
            "network" in lower or "connection" in lower
            or "network problem" in lower or "http error" in lower
        ):
            return "network error during beet import (is the VGMDB API running?)"
        if "skipping" in lower:
            return "beet skipped (no match above threshold)"
        return "beet import failed or found no suitable match"

    def _post_mb_seed(
        self, mb_release_id: str, vgmdb_id: str,
        artist: str, album: str,
        *, category: Category = "mb_seed_dl",
    ) -> Category | None:
        """If MB has no VGMDB link, post a seed URL to Discord. Returns
        the notifier category that was actually used, or None if MB is
        already linked / mb_release_id is empty.

        ``category`` is the Discord webhook bucket: ``"mb_seed_dl"`` for
        the OnAlbumDownload hook (single-album enrichments triggered by
        Lidarr), ``"mb_seed_beets"`` for bulk re-enrichment runs. The
        notifier falls back gracefully if either webhook is unconfigured.
        """
        if not mb_release_id or mb_release_id.startswith("vgmdb-"):
            return None
        try:
            check = self.mb_link.check(mb_release_id, vgmdb_id=str(vgmdb_id))
        except Exception as exc:  # noqa: BLE001
            log.warning("MB link check failed: %s", exc)
            return None
        if check.has_link:
            return None
        title = "🔗 Submit VGMDB Link to MusicBrainz"
        body = (
            f"**{artist}** — {album}\n"
            f"VGMDB: [{check.vgmdb_url}]({check.vgmdb_url})\n"
            f"[👉 Click to add link in MusicBrainz]({check.seed_url})"
        )
        self.notifier.send(category, title, body, success=True)
        return category

    def _record_skip(
        self,
        mb_id: str,
        artist: str,
        album: str,
        album_folder: Path | None,
        *,
        vgmdb_id: str,
        match: str,
        reason: str,
        threshold: str = "",
    ) -> dict[str, Any]:
        skipped = store.skipped_albums.read()
        skipped[mb_id] = {
            "vgmdb_id":         vgmdb_id,
            "folder":           str(album_folder) if album_folder else "",
            "artist":           artist,
            "album":            album,
            "match_percentage": match,
            "threshold":        threshold,
            "skipped_reason":   reason,
        }
        store.skipped_albums.write(skipped)
        db.add_activity(
            "enrich",
            f"skipped: {reason}",
            level="warning",
            artist=artist, album=album,
        )
        return {
            "ok": False, "vgmdb_id": vgmdb_id if vgmdb_id != "N/A" else None,
            "source": "skipped", "match": match,
            "message": reason,
            "artist": artist, "album": album, "mb_release_id": mb_id,
        }

    def _fail(self, code: str, artist: str, album: str, mb_id: str,
              msg: str) -> dict[str, Any]:
        return {
            "ok": False, "vgmdb_id": None, "source": code,
            "match": None, "message": msg,
            "artist": artist, "album": album, "mb_release_id": mb_id,
        }
