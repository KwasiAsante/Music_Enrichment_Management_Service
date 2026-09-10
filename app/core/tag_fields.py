"""Shared standard-and-custom tag read/write for one album folder.

Both :class:`app.core.tag_editor.TagEditorService` (view/edit) and
:class:`app.core.tag_locks.TagLockService` (protect from enrichment) need
the exact same answer to "what tag key(s) does field X map to, and how do
I read/write it on one file" — this module is the one place that answers
it, so those two callers can't drift apart.

Fields fall into two groups:

* **Standard** fields (``STANDARD_FIELDS``) — the same small set
  :mod:`app.core.field_overrides` already writes (artist, composer,
  performer, arranger, lyricist, genre, label, catalog), plus ``album``
  and ``date``. Read/written via mutagen's "easy" interface only, with
  the same best-effort per-key-per-file tolerance ``apply_overrides``
  already has — a key a format doesn't support is silently skipped, not
  redirected anywhere else, so a standard field always lives in the same
  physical tag the rest of the enrichment pipeline expects.
* **Everything else found on the files**, *provided* it has the same
  value on every track (see ``read_album_fields``) — this module only
  ever writes a field to *every* file in the album, so a value that
  genuinely varies per track (or a well-known per-track identity key
  like ``title``) can never be safely exposed for editing here without
  silently overwriting every track with one value. Vorbis/APEv2 formats
  accept an arbitrary key directly through the easy interface; ID3/MP4
  formats fall back to :mod:`app.core.custom_tags`' raw ``TXXX``/
  freeform-atom handling for those. ``classify_tag_source`` further
  splits this group into ``musicbrainz``/``vgmdb``/``custom`` purely for
  display — a MusicBrainz identifier written by Picard/beets is exactly
  as editable and lockable as a genuinely custom tag, just grouped
  separately in the editor so the two aren't mixed together.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from mutagen import File as MutagenFile  # type: ignore[import-untyped]

from app.core import custom_tags
from app.core.cover_art import AUDIO_EXTS
from app.core.field_overrides import FIELD_TAG_KEYS

log = logging.getLogger("music-lib-helper.tag_fields")

STANDARD_FIELD_TAG_KEYS: dict[str, tuple[str, ...]] = {
    **FIELD_TAG_KEYS,
    "album": ("album",),
    "date": ("date",),
}
STANDARD_FIELDS: tuple[str, ...] = tuple(STANDARD_FIELD_TAG_KEYS.keys())

_KNOWN_TAG_KEYS: frozenset[str] = frozenset(
    key for keys in STANDARD_FIELD_TAG_KEYS.values() for key in keys
)

# Per-track identity/position keys — never treated as an editable/lockable
# "custom" field, no matter how uniform they happen to look on a given
# album, since a single-value write would overwrite every track's own
# value. There's no per-track editor in this app (yet) to route these to
# instead, so they're simply excluded. Includes the MusicBrainz per-
# recording identifiers Picard/beets commonly write (a library tagged by
# Picard before adoption of this tool will already carry these) — each
# one is specific to a single track/recording/work, never the album.
PER_TRACK_ONLY_KEYS: frozenset[str] = frozenset({
    "title", "tracknumber", "discnumber",
    "tracktotal", "disctotal", "totaltracks", "totaldiscs",
    "musicbrainz_trackid", "musicbrainz_releasetrackid", "musicbrainz_workid",
})

# Of PER_TRACK_ONLY_KEYS above, these two are still useful to protect
# from enrichment even though their value legitimately differs from
# track to track — unlike every other field this module handles,
# "locking" one of these doesn't mean "keep one value album-wide" (that
# would overwrite every track with track 1's title), it means "keep each
# track's own value". See app.core.tag_locks.TagLockService's
# snapshot_tracks/restore_tracks, the only caller allowed to read/write
# these per-track rather than per-album. "artist" here is deliberately
# just the plain ARTIST tag, not the full ARTIST_TAG_KEYS list
# STANDARD_FIELD_TAG_KEYS uses for the albumwide "artist" field — locking
# track artist must never touch albumartist, a separate album-wide value.
TRACK_LOCK_TAG_KEYS: dict[str, tuple[str, ...]] = {
    "title": ("title",),
    "artist": ("artist",),
}
TRACK_LOCKABLE_FIELDS: tuple[str, ...] = tuple(TRACK_LOCK_TAG_KEYS.keys())

_TAG_SOURCE_PREFIXES: tuple[tuple[str, str], ...] = (
    ("musicbrainz", "musicbrainz"),
    ("vgmdb", "vgmdb"),
)


def classify_tag_source(name: str) -> str:
    """``'standard' | 'musicbrainz' | 'vgmdb' | 'custom'`` — purely a
    display grouping for the tag editor so identifiers an enrichment
    source wrote (MusicBrainz via Picard/beets' ``mbsync``, or a future
    VGMDB-namespaced tag) aren't mixed in among genuinely arbitrary
    custom tags. Every group is equally viewable/editable/lockable —
    this doesn't gate behavior, only how the editor groups field rows.
    """
    if name in STANDARD_FIELD_TAG_KEYS:
        return "standard"
    normalized = "".join(ch for ch in name.lower() if ch.isalnum())
    for prefix, source in _TAG_SOURCE_PREFIXES:
        if normalized.startswith(prefix):
            return source
    return "custom"


def iter_audio_files(album_dir: Path) -> list[Path]:
    if not album_dir.exists():
        return []
    try:
        return sorted(
            f for f in album_dir.rglob("*")
            if f.is_file() and f.suffix.lower() in AUDIO_EXTS
        )
    except OSError as exc:
        log.warning("could not walk %s: %s", album_dir, exc)
        return []


def first_audio_file(album_dir: Path) -> Path | None:
    files = iter_audio_files(album_dir)
    return files[0] if files else None


def _first_value(tags: Any, *keys: str) -> str | None:
    for key in keys:
        val = tags.get(key)
        if val:
            return val[0] if isinstance(val, list) else str(val)
    return None


def read_field_value(path: Path, field: str) -> str | None:
    """Current value of one field (standard or custom) on one file."""
    try:
        audio = MutagenFile(str(path), easy=True)
    except Exception as exc:  # noqa: BLE001
        log.debug("could not read %s: %s", path, exc)
        return None
    if audio is None:
        return None

    # `or {}`, not a falsy check on `audio.tags` itself — an *empty but
    # present* easy-tags dict is common for a file whose only tag is a
    # custom one (a lone ID3 TXXX frame isn't a registered EasyID3 key,
    # so the easy dict has nothing in it), and that must still fall
    # through to the custom_tags check below rather than short-circuit.
    tags = audio.tags or {}
    if field in STANDARD_FIELD_TAG_KEYS:
        return _first_value(tags, *STANDARD_FIELD_TAG_KEYS[field])

    value = _first_value(tags, field)
    if value is not None:
        return value
    return custom_tags.read_custom_tags(path).get(field)


def read_album_fields(album_dir: Path) -> dict[str, str | None]:
    """Every standard field (always present, ``None`` if absent, read
    from the first file — the same "first file is representative"
    convention
    :meth:`app.core.field_overrides.FieldOverrideService._read_current_tags`
    uses) plus every *custom* tag that has the exact same value on every
    file in the album.

    A custom key is scanned across *all* files, not just the first,
    specifically to catch the case a single-file read can't: a per-track
    value (say, someone tagged every track with its own freeform note)
    that happens to look like album-wide metadata from track one alone.
    Surfacing that here for editing would silently overwrite every
    track with whichever value got saved — see ``write_album_field`` — so
    a key that varies across files, or is a known per-track identity key
    (``PER_TRACK_ONLY_KEYS``), is left out of the result entirely rather
    than guessed at.
    """
    fields: dict[str, str | None] = dict.fromkeys(STANDARD_FIELDS)
    custom_candidates: dict[str, set[str | None]] = {}
    seen_first_file = False

    for f in iter_audio_files(album_dir):
        try:
            audio = MutagenFile(str(f), easy=True)
        except Exception as exc:  # noqa: BLE001
            log.debug("could not read %s: %s", f, exc)
            continue
        if audio is None:
            continue

        # See read_field_value's comment — an empty-but-present tags
        # dict must still reach the custom-tag checks below.
        tags = audio.tags or {}

        if not seen_first_file:
            seen_first_file = True
            for field, keys in STANDARD_FIELD_TAG_KEYS.items():
                fields[field] = _first_value(tags, *keys)

        for key in tags.keys():
            if key in _KNOWN_TAG_KEYS or key in fields or key in PER_TRACK_ONLY_KEYS:
                continue
            custom_candidates.setdefault(key, set()).add(_first_value(tags, key))

        for key, value in custom_tags.read_custom_tags(f).items():
            if key in fields or key in PER_TRACK_ONLY_KEYS:
                continue
            custom_candidates.setdefault(key, set()).add(value)

    for key, values in custom_candidates.items():
        if len(values) == 1:
            fields[key] = next(iter(values))
        # else: this key's value differs from track to track — not
        # exposed here at all, see the docstring above.

    return fields


def write_album_field(album_dir: Path, field: str, value: str) -> int:
    """Write (or, for ``value == ""``, delete) one field across every
    audio file in the album. Returns the number of files touched.

    Raises ``ValueError`` for a known per-track identity key (``title``
    and friends) — writing those album-wide would overwrite every
    track's own value with one shared string. This is enforced here
    (not just left to the caller) since :class:`app.core.tag_locks.
    TagLockService` writes through this same function.
    """
    if field in PER_TRACK_ONLY_KEYS:
        raise ValueError(f"{field!r} is a per-track field — it can't be edited or locked album-wide")

    is_standard = field in STANDARD_FIELD_TAG_KEYS
    keys = STANDARD_FIELD_TAG_KEYS.get(field, (field,))
    touched = 0
    for f in iter_audio_files(album_dir):
        if _write_field_to_file(f, field, keys, value, is_standard=is_standard):
            touched += 1
    return touched


def _write_field_to_file(
    path: Path, field: str, keys: tuple[str, ...], value: str, *, is_standard: bool,
) -> bool:
    try:
        audio = MutagenFile(str(path), easy=True)
        if not audio or audio.tags is None:
            return False
    except Exception as exc:  # noqa: BLE001
        log.debug("could not open %s: %s", path, exc)
        return False

    changed = False
    for key in keys:
        try:
            if value:
                audio.tags[key] = [value]
                changed = True
            elif key in audio.tags:
                del audio.tags[key]
                changed = True
        except Exception as exc:  # noqa: BLE001 — format may not support this key
            log.debug("could not set %s on %s: %s", key, path, exc)

    if changed:
        try:
            audio.save()
            return True
        except Exception as exc:  # noqa: BLE001
            log.debug("could not save %s: %s", path, exc)
            return False

    if not is_standard:
        if value:
            return custom_tags.write_custom_tag(path, field, value)
        return custom_tags.delete_custom_tag(path, field)

    return False


def _track_position_key(tags: Any) -> tuple[str, str]:
    """(discnumber, tracknumber) for one file's tags — used to match a
    track up before and after enrichment even if beets renames the file
    itself based on the newly-written title."""
    return (_first_value(tags, "discnumber") or "1", _first_value(tags, "tracknumber") or "")


def read_track_field_by_position(album_dir: Path, field: str) -> dict[tuple[str, str], str | None]:
    """Current value of one track-level lockable field (``title`` or
    ``artist``, see ``TRACK_LOCK_TAG_KEYS``) on every file in the album,
    keyed by ``(discnumber, tracknumber)`` rather than file path — each
    track keeps its own value, unlike ``read_album_fields``."""
    keys = TRACK_LOCK_TAG_KEYS[field]
    values: dict[tuple[str, str], str | None] = {}
    for f in iter_audio_files(album_dir):
        try:
            audio = MutagenFile(str(f), easy=True)
        except Exception as exc:  # noqa: BLE001
            log.debug("could not read %s: %s", f, exc)
            continue
        if audio is None:
            continue
        tags = audio.tags or {}
        values[_track_position_key(tags)] = _first_value(tags, *keys)
    return values


def write_track_field_by_position(
    album_dir: Path, field: str, values: dict[tuple[str, str], str | None],
) -> int:
    """Write each track's own snapshotted value of a track-level field
    (see ``read_track_field_by_position``) back to whichever file now
    holds that ``(discnumber, tracknumber)`` position — never broadcasting
    one value to every track like ``write_album_field`` does. Returns the
    number of files touched."""
    keys = TRACK_LOCK_TAG_KEYS[field]
    touched = 0
    for f in iter_audio_files(album_dir):
        try:
            audio = MutagenFile(str(f), easy=True)
            if not audio or audio.tags is None:
                continue
        except Exception as exc:  # noqa: BLE001
            log.debug("could not open %s: %s", f, exc)
            continue
        value = values.get(_track_position_key(audio.tags))
        if value is None:
            continue
        if _write_field_to_file(f, field, keys, value, is_standard=True):
            touched += 1
    return touched
