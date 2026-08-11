"""Builds the full detail view for one album — tags read straight from
the audio files (authoritative, since it's what's actually on disk),
supplemented by VGMDB and/or MusicBrainz for anything the tags don't
carry (extra credit roles beyond a plain "composer" tag, community
tags/genres, a description) when the album is mapped to either.

Every external call (VGMDB, MusicBrainz, reading a possibly-corrupt tag)
is wrapped so this module never raises — on any failure it falls back to
whatever partial data is available and records a human-readable note in
``AlbumDetail.warnings``. A detail page with a few blank sections is a
fine outcome; a 500 because one track's tags were corrupt is not.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Any

from mutagen import File as MutagenFile  # type: ignore[import-untyped]

from app.core.cover_art import AUDIO_EXTS, find_album_art
from app.core.mb_client import MBClient
from app.core.vgmdb_client import VGMDBClient
from app.models.album_detail import AlbumArt, AlbumDetail, TrackDetail

log = logging.getLogger("music-lib-helper.album_details")

_LIST_SPLIT_RE = re.compile(r"[;,/]")
_LANG_PRIORITY = ("en", "ja-latn", "ja")


def get_album_detail(
    *,
    folder: str,
    album_dir: Path,
    artist: str,
    album: str,
    mb_release_id: str | None,
    vgmdb_id: str | None,
    mapped: bool,
    enriched: bool,
) -> AlbumDetail:
    """Build the full detail view for one album."""
    warnings: list[str] = []
    sources: list[str] = []

    raw_tracks = _read_tag_tracks(album_dir)
    if raw_tracks:
        sources.append("tags")
    else:
        warnings.append("Could not read track tags from any audio file in this folder.")

    art_found = find_album_art(album_dir)

    genres = _dedup_list(g for t in raw_tracks for g in _split_list_field(t.get("genre")))
    composers = _dedup_list(t["composer"] for t in raw_tracks if t.get("composer"))
    year = next((t["date"][:4] for t in raw_tracks if t.get("date")), None)

    performers: list[str] = []
    arrangers: list[str] = []
    lyricists: list[str] = []
    label: str | None = None
    catalog: str | None = None
    media_format: str | None = None
    description: str | None = None

    if vgmdb_id and vgmdb_id != "skip":
        try:
            vgmdb_data = VGMDBClient().get_album(vgmdb_id)
        except Exception as exc:  # noqa: BLE001 — never let a detail page 500
            log.warning("vgmdb supplement failed for %s: %s", vgmdb_id, exc)
            vgmdb_data = None

        if vgmdb_data:
            try:
                sources.append("vgmdb")
                if not composers:
                    composers = _vgmdb_person_names(vgmdb_data.get("composers"))
                performers = _vgmdb_person_names(vgmdb_data.get("performers"))
                arrangers = _vgmdb_person_names(vgmdb_data.get("arrangers"))
                lyricists = _vgmdb_person_names(vgmdb_data.get("lyricists"))
                if not genres:
                    genres = _split_list_field(vgmdb_data.get("category"))
                if not label:
                    pub = vgmdb_data.get("publisher") or vgmdb_data.get("distributor") or {}
                    label = _pick_lang(pub.get("names") or {})
                catalog = catalog or vgmdb_data.get("catalog")
                media_format = media_format or vgmdb_data.get("media_format")
                description = vgmdb_data.get("notes") or vgmdb_data.get("description") or None
                if not year:
                    release_date = vgmdb_data.get("release_date") or ""
                    year = release_date[:4] if release_date else None
                if not raw_tracks:
                    raw_tracks = _tracks_from_vgmdb(vgmdb_data)
                    if raw_tracks:
                        warnings.append("Track list filled in from VGMDB — no readable tags were found in the files.")
            except Exception as exc:  # noqa: BLE001
                log.warning("vgmdb supplement parse failed for %s: %s", vgmdb_id, exc)
                warnings.append("VGMDB data was present but couldn't be fully parsed.")
        else:
            warnings.append("VGMDB lookup failed or returned nothing.")

    if mb_release_id and not mb_release_id.startswith("vgmdb-"):
        try:
            mb_data = MBClient().get_release(
                mb_release_id, includes="genres+tags+recordings+labels",
            )
        except Exception as exc:  # noqa: BLE001
            log.warning("musicbrainz supplement failed for %s: %s", mb_release_id, exc)
            mb_data = None

        if mb_data:
            try:
                sources.append("musicbrainz")
                mb_genres = [g["name"] for g in (mb_data.get("genres") or []) if g.get("name")]
                mb_tags = [t["name"] for t in (mb_data.get("tags") or []) if t.get("name")]
                genres = _dedup_list([*genres, *mb_genres, *mb_tags])
                if not label:
                    label_info = mb_data.get("label-info") or []
                    if label_info:
                        label = ((label_info[0].get("label") or {}).get("name")) or label
                        catalog = catalog or label_info[0].get("catalog-number")
                if not raw_tracks:
                    raw_tracks = _tracks_from_mb(mb_data)
                    if raw_tracks:
                        warnings.append("Track list filled in from MusicBrainz — no readable tags were found in the files.")
            except Exception as exc:  # noqa: BLE001
                log.warning("musicbrainz supplement parse failed for %s: %s", mb_release_id, exc)
                warnings.append("MusicBrainz data was present but couldn't be fully parsed.")
        else:
            warnings.append("MusicBrainz lookup failed or returned nothing.")

    tracks = [
        TrackDetail(
            disc=t.get("disc") or 1,
            track=t.get("track") or 0,
            title=t.get("title") or "?",
            duration_seconds=t.get("duration_seconds"),
            composer=t.get("composer"),
        )
        for t in raw_tracks
    ]
    disc_count = max((t.disc for t in tracks), default=1)
    total_duration = sum((t.duration_seconds or 0.0) for t in tracks) or None

    return AlbumDetail(
        folder=folder,
        artist=artist,
        album=album,
        mb_release_id=mb_release_id,
        vgmdb_id=vgmdb_id,
        mapped=mapped,
        enriched=enriched,
        year=year,
        label=label,
        catalog=catalog,
        media_format=media_format,
        genres=genres,
        composers=composers,
        performers=performers,
        arrangers=arrangers,
        lyricists=lyricists,
        description=description,
        disc_count=disc_count,
        track_count=len(tracks),
        total_duration_seconds=total_duration,
        tracks=tracks,
        art=AlbumArt(front="front" in art_found, back="back" in art_found),
        sources=sources,
        warnings=warnings,
    )


# ── tag reading ──────────────────────────────────────────────────────────
def _read_tag_tracks(album_dir: Path) -> list[dict[str, Any]]:
    """Read every audio file in the folder via mutagen's "easy" tag
    interface and return raw per-track dicts, sorted by (disc, track).
    Skips any individual file that can't be opened rather than failing
    the whole album.
    """
    try:
        files = sorted(
            f for f in album_dir.rglob("*")
            if f.is_file() and f.suffix.lower() in AUDIO_EXTS
        )
    except OSError as exc:
        log.warning("could not walk %s: %s", album_dir, exc)
        return []

    tracks: list[dict[str, Any]] = []
    for f in files:
        try:
            audio = MutagenFile(str(f), easy=True)
        except Exception as exc:  # noqa: BLE001 — mutagen raises many types
            log.debug("could not read tags from %s: %s", f, exc)
            continue
        if audio is None:
            continue

        def first(key: str) -> str | None:
            values = audio.get(key) if hasattr(audio, "get") else None
            return values[0] if values else None

        disc = _safe_int((first("discnumber") or "1").split("/")[0]) or 1
        track = _safe_int((first("tracknumber") or "0").split("/")[0]) or 0
        duration = None
        info = getattr(audio, "info", None)
        if info is not None:
            duration = getattr(info, "length", None)

        tracks.append({
            "disc": disc,
            "track": track,
            "title": first("title") or f.stem,
            "composer": first("composer"),
            "genre": first("genre"),
            "date": first("date"),
            "duration_seconds": duration,
        })

    tracks.sort(key=lambda t: (t["disc"], t["track"]))
    return tracks


# ── VGMDB tracklist fallback (only used when the files had no tags at
#    all — matches the disc/track structure app/beets_plugins/VGMplug.py
#    already parses from the same API) ──────────────────────────────────
def _tracks_from_vgmdb(vgmdb_data: dict) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for disc_index, disc in enumerate(vgmdb_data.get("discs") or [], start=1):
        for track_index, track in enumerate(disc.get("tracks") or [], start=1):
            names = track.get("names") or {}
            title = _pick_lang(names) or "?"
            duration = _parse_mmss(track.get("track_length"))
            out.append({
                "disc": disc_index, "track": track_index,
                "title": title, "duration_seconds": duration, "composer": None,
            })
    return out


# ── MusicBrainz tracklist fallback ──────────────────────────────────────
def _tracks_from_mb(mb_data: dict) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for disc_index, medium in enumerate(mb_data.get("media") or [], start=1):
        for track in medium.get("tracks") or []:
            recording = track.get("recording") or {}
            title = track.get("title") or recording.get("title") or "?"
            track_num = _safe_int(track.get("number")) or _safe_int(track.get("position")) or 0
            length_ms = track.get("length") or recording.get("length")
            duration = (length_ms / 1000.0) if isinstance(length_ms, (int, float)) else None
            out.append({
                "disc": disc_index, "track": track_num,
                "title": title, "duration_seconds": duration, "composer": None,
            })
    return out


# ── small shared helpers ────────────────────────────────────────────────
def _safe_int(value: Any) -> int | None:
    try:
        return int(str(value).strip())
    except (ValueError, TypeError):
        return None


def _split_list_field(value: Any) -> list[str]:
    """Split a semicolon/comma/slash-separated tag or category value
    into a clean list. Already-a-list values pass through unchanged."""
    if not value:
        return []
    if isinstance(value, list):
        return [str(v).strip() for v in value if str(v).strip()]
    return [p.strip() for p in _LIST_SPLIT_RE.split(str(value)) if p.strip()]


def _dedup_list(items: Any) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for item in items:
        if not item:
            continue
        key = item.strip().lower()
        if key and key not in seen:
            seen.add(key)
            out.append(item.strip())
    return out


def _pick_lang(names: dict) -> str | None:
    """Pick the best available string from a VGMDB ``names`` object —
    English -> romanised Japanese -> Japanese -> whatever's first."""
    for lang in _LANG_PRIORITY:
        if names.get(lang):
            return names[lang]
    return next(iter(names.values()), None) if names else None


def _vgmdb_person_names(people: list[dict] | None) -> list[str]:
    """Flatten a VGMDB credit-role list (e.g. all composers) into a
    plain list of display names, one per person."""
    out = []
    for person in people or []:
        name = _pick_lang(person.get("names") or {})
        if name:
            out.append(name)
    return out


def _parse_mmss(value: str | None) -> float | None:
    """Parse VGMDB's ``"M:SS"`` track-length strings; ``"Unknown"`` (or
    anything unparseable) becomes ``None``."""
    if not value or value == "Unknown":
        return None
    parts = value.split(":")
    if len(parts) != 2:
        return None
    try:
        return float(60 * int(parts[0]) + int(parts[1]))
    except ValueError:
        return None
