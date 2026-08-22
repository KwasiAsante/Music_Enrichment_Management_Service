"""FieldOverrideService — per-album manual overrides for which VGMDB
credit/value gets written to which tag field.

Exists because VGMDB's own credit-role priority (see
``app/beets_plugins/VGMplug.py``'s ``artist-priority`` config) is a
*global* setting shared by every album — it can't know that one
particular soundtrack credits a session performer as "composer" while
MusicBrainz already has the real band name. This service lets a person
search for one album, see every value VGMDB actually offers (per credit
role, per language) side by side with what's currently on disk, and pin
specific field -> value choices that survive future enrichment.

Overrides are stored in ``field_overrides.json``, keyed by the album's
*folder* (the same stable identifier :mod:`app.core.album_details` uses
for ``AlbumDetail.folder``) rather than ``mb_release_id`` — not every
album has an MB release id, but every album has a folder.

Applying an override is a post-import tag-write step in
:class:`BeetsEnricher.enrich_album` (mirroring how it already fixes
non-Latin artist tags after a beet import) — see :meth:`apply_overrides`.
Saving an override here never touches a file; it only takes effect the
next time that album is (re-)enriched.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Any

from mutagen import File as MutagenFile  # type: ignore[import-untyped]

from app.config import settings
from app.core.vgmdb_client import VGMDBClient
from app.storage.json_store import store

log = logging.getLogger("music-lib-helper.field_overrides")

AUDIO_EXTS = {".mp3", ".flac", ".ogg", ".opus", ".m4a", ".aac", ".wav", ".ape"}

_LANG_PRIORITY = ("en", "ja-latn", "ja")
_LANG_LABELS = {"en": "English", "ja-latn": "Romaji", "ja": "Japanese"}
_LIST_SPLIT_RE = re.compile(r"[;,/]")

# Every tag-key spelling a tagger might use for "the album's artist" —
# both singular (albumartist/artist) and plural (some Picard-tagged
# FLACs carry ARTISTS/ALBUMARTISTS vorbis comments alongside the
# singular ones for multi-value artist credits), plus the space/
# underscore variants. app/core/beets_enricher.py's non-Latin-tag fix
# imports this constant too, so the two "which keys count as the artist
# tag" lists can't drift apart.
ARTIST_TAG_KEYS: tuple[str, ...] = (
    "albumartist", "artist", "album_artist",
    "album_artists", "albumartists", "artists", "album artist",
)

# Each overridable field maps to the mutagen "easy"-interface tag key(s)
# actually written to the files. Writing to a key a given format doesn't
# support (e.g. the plural forms on an MP3's restricted EasyID3 key set)
# is caught and skipped per-key in apply_overrides() below, so listing
# extra candidate keys per field is always safe.
FIELD_TAG_KEYS: dict[str, tuple[str, ...]] = {
    "artist":    ARTIST_TAG_KEYS,
    "composer":  ("composer",),
    "performer": ("performer",),
    "arranger":  ("arranger",),
    "lyricist":  ("lyricist",),
    "genre":     ("genre",),
    "label":     ("organization",),
    "catalog":   ("catalognumber",),
}
OVERRIDE_FIELDS: tuple[str, ...] = tuple(FIELD_TAG_KEYS.keys())

# Which VGMDB credit-role lists feed candidate values for which field —
# "artist" draws from all three, since any of them might be the person
# or band MusicBrainz already names as the release artist.
_ROLE_SOURCE_FIELDS: dict[str, tuple[str, ...]] = {
    "artist":    ("composers", "performers", "arrangers"),
    "composer":  ("composers",),
    "performer": ("performers",),
    "arranger":  ("arrangers",),
    "lyricist":  ("lyricists",),
}
_ROLE_LABELS = {
    "composers": "Composer", "performers": "Performer", "arrangers": "Arranger",
}


def _pick_lang(names: dict) -> str | None:
    for lang in _LANG_PRIORITY:
        if names.get(lang):
            return names[lang]
    return next(iter(names.values()), None) if names else None


def _split_list(value: Any) -> list[str]:
    if not value:
        return []
    if isinstance(value, list):
        return [str(v).strip() for v in value if str(v).strip()]
    return [p.strip() for p in _LIST_SPLIT_RE.split(str(value)) if p.strip()]


class FieldOverrideService:
    """Search-adjacent lookup + CRUD over ``field_overrides.json``, plus
    the post-import tag-write step. See module docstring."""

    def __init__(self, vgmdb: VGMDBClient | None = None) -> None:
        self.vgmdb = vgmdb or VGMDBClient()
        self.artist_root: Path = settings.app_music_dir / "synced_music" / "Artist"

    # ── lookup ───────────────────────────────────────────────────────────
    def _find_album(self, folder: str) -> dict[str, Any] | None:
        """Find the ``album_list.json`` entry for ``folder``, mirroring
        the folder-identity rule ``app/api/library.py``'s
        ``_filtered_entries`` uses: ``info["folder"]`` if set, else the
        dict key itself."""
        for folder_name, info in store.album_list.read().items():
            if (info.get("folder") or folder_name) == folder:
                return {**info, "folder": info.get("folder") or folder_name}
        return None

    def _resolve_vgmdb_id(self, info: dict) -> str | None:
        mb_id = info.get("mb_release_id") or ""
        mapping = store.vgmdb_mapping.read()
        if mb_id in mapping:
            vid = mapping[mb_id].get("vgmdb_id")
            return vid if vid and vid != "skip" else None
        if mb_id.startswith("vgmdb-"):
            return mb_id.removeprefix("vgmdb-")
        return None

    def _read_current_tags(self, album_dir: Path) -> dict[str, str]:
        """First audio file's relevant tag values — same "first file is
        representative" convention as ``vgmdb_mapper._read_catalog_from_tags``."""
        if not album_dir.exists():
            return {}
        for f in sorted(album_dir.rglob("*")):
            if f.suffix.lower() not in AUDIO_EXTS:
                continue
            try:
                audio = MutagenFile(str(f), easy=True)
            except Exception:  # noqa: BLE001
                return {}
            if not audio or not audio.tags:
                return {}
            tags = audio.tags

            def first(*keys: str) -> str | None:
                for key in keys:
                    val = tags.get(key)
                    if val:
                        return val[0] if isinstance(val, list) else str(val)
                return None

            return {
                "artist":    first(*ARTIST_TAG_KEYS),
                "composer":  first("composer"),
                "performer": first("performer"),
                "arranger":  first("arranger"),
                "lyricist":  first("lyricist"),
                "genre":     first("genre"),
                "label":     first("organization"),
                "catalog":   first("catalognumber"),
            }
        return {}

    # ── options (read side) ─────────────────────────────────────────────
    def get_options(self, folder: str) -> dict[str, Any]:
        """Build the field/value picker for one album. Returns a plain
        dict shaped like :class:`app.models.field_override.AlbumFieldOptions`.

        Never raises on a VGMDB/tag-read failure — those show up as
        ``warnings`` instead, same convention as
        :func:`app.core.album_details.get_album_detail`. Raises
        ``ValueError`` only if ``folder`` doesn't match any known album.
        """
        info = self._find_album(folder)
        if info is None:
            raise ValueError(f"no album found with folder {folder!r}")

        warnings: list[str] = []
        current_tags = self._read_current_tags(self.artist_root / folder)
        if not current_tags:
            warnings.append("Could not read tags from any audio file in this folder.")

        vgmdb_id = self._resolve_vgmdb_id(info)
        vgmdb_data: dict | None = None
        if vgmdb_id:
            try:
                vgmdb_data = self.vgmdb.get_album(vgmdb_id)
            except Exception as exc:  # noqa: BLE001
                log.warning("vgmdb fetch failed for %s: %s", vgmdb_id, exc)
            if vgmdb_data is None:
                warnings.append("VGMDB lookup failed or returned nothing.")
        else:
            warnings.append(
                "This album has no VGMDB mapping yet — map it on the Mappings "
                "page first to get VGMDB candidate values here.",
            )

        saved_fields = store.field_overrides.read().get(folder, {}).get("fields", {})

        fields = [
            {
                "field": field,
                "current_value": current_tags.get(field),
                "override_value": saved_fields.get(field),
                "candidates": self._candidates_for(field, vgmdb_data),
            }
            for field in OVERRIDE_FIELDS
        ]

        return {
            "folder": folder,
            "artist": info.get("artist") or "",
            "album": info.get("album") or "",
            "mb_release_id": info.get("mb_release_id") or None,
            "vgmdb_id": vgmdb_id,
            "mapped": vgmdb_id is not None,
            "fields": fields,
            "warnings": warnings,
        }

    def _candidates_for(self, field: str, vgmdb_data: dict | None) -> list[dict]:
        out: list[dict] = []
        seen: set[str] = set()

        def add(value: str | None, label: str) -> None:
            if not value:
                return
            value = value.strip()
            key = value.lower()
            if not value or key in seen:
                return
            seen.add(key)
            out.append({"value": value, "label": label})

        if not vgmdb_data:
            return out

        for role in _ROLE_SOURCE_FIELDS.get(field, ()):
            role_label = _ROLE_LABELS.get(role, role.capitalize())
            for person in vgmdb_data.get(role) or []:
                names = person.get("names") or {}
                for lang in _LANG_PRIORITY:
                    if names.get(lang):
                        add(names[lang], f"{role_label} ({_LANG_LABELS[lang]})")
                for lang, name in names.items():
                    if lang not in _LANG_PRIORITY:
                        add(name, f"{role_label} ({lang})")

        if field == "genre":
            for genre in _split_list(vgmdb_data.get("category")):
                add(genre, "VGMDB category")

        if field == "label":
            pub = vgmdb_data.get("publisher") or vgmdb_data.get("distributor") or {}
            names = pub.get("names") or {}
            for lang in _LANG_PRIORITY:
                if names.get(lang):
                    add(names[lang], f"VGMDB label ({_LANG_LABELS[lang]})")

        if field == "catalog":
            add(vgmdb_data.get("catalog"), "VGMDB catalog")

        return out

    # ── write side (CRUD) ────────────────────────────────────────────────
    def set_overrides(self, folder: str, fields: dict[str, str]) -> dict[str, Any]:
        """Merge ``fields`` into the saved override for ``folder``. A
        field mapped to an empty string clears that one field rather
        than saving an empty override; if the merge leaves no fields at
        all, the whole entry is dropped.
        """
        unknown = set(fields) - set(OVERRIDE_FIELDS)
        if unknown:
            raise ValueError(f"unknown override field(s): {', '.join(sorted(unknown))}")

        info = self._find_album(folder)
        if info is None:
            raise ValueError(f"no album found with folder {folder!r}")

        all_overrides = store.field_overrides.read()
        entry = all_overrides.get(folder) or {
            "mb_release_id": info.get("mb_release_id") or None,
            "artist": info.get("artist") or "",
            "album": info.get("album") or "",
            "fields": {},
        }
        current_fields = dict(entry.get("fields", {}))
        for field, value in fields.items():
            value = (value or "").strip()
            if value:
                current_fields[field] = value
            else:
                current_fields.pop(field, None)
        entry["fields"] = current_fields

        if current_fields:
            all_overrides[folder] = entry
        else:
            all_overrides.pop(folder, None)
        store.field_overrides.write(all_overrides)
        log.info("field overrides set for %s: %s", folder, current_fields)

        return {"folder": folder, **entry}

    def delete_overrides(self, folder: str) -> bool:
        """Remove every override for ``folder``. Returns True if an
        entry existed."""
        all_overrides = store.field_overrides.read()
        if folder not in all_overrides:
            return False
        del all_overrides[folder]
        store.field_overrides.write(all_overrides)
        log.info("field overrides cleared for %s", folder)
        return True

    # ── apply (write side, called post beet-import) ─────────────────────
    def apply_overrides(self, album_folder: Path, folder: str) -> int:
        """Write every saved override for ``folder`` into the audio
        files' tags. Returns the number of files modified.

        Best-effort and per-file/per-key, same convention as
        ``BeetsEnricher._fix_non_latin_artist_tags``: a tag key a given
        format doesn't support (e.g. ``composer`` on an M4A's restricted
        easy-tag set) is skipped for that file rather than failing the
        whole album. A no-op (returns 0) when nothing is saved for this
        folder.
        """
        entry = store.field_overrides.read().get(folder)
        fields = entry.get("fields") if entry else None
        if not fields:
            return 0

        fixed = 0
        for audio_path in album_folder.rglob("*"):
            if audio_path.suffix.lower() not in AUDIO_EXTS:
                continue
            try:
                audio = MutagenFile(str(audio_path), easy=True)
                if not audio or audio.tags is None:
                    continue
                changed = False
                for field, value in fields.items():
                    for tag_key in FIELD_TAG_KEYS[field]:
                        try:
                            audio.tags[tag_key] = [value]
                            changed = True
                        except Exception as exc:  # noqa: BLE001 — format may not support this key
                            log.debug("could not set %s on %s: %s", tag_key, audio_path, exc)
                if changed:
                    audio.save()
                    fixed += 1
            except Exception as exc:  # noqa: BLE001
                log.debug("override apply failed for %s: %s", audio_path, exc)

        if fixed:
            log.info("applied field overrides to %d file(s) in %s: %s",
                      fixed, album_folder, list(fields.keys()))
        return fixed
