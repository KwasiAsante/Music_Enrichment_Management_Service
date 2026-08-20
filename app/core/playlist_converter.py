"""PlaylistConverter — remaps ``.m3u``/``.m3u8`` entries to the current
on-disk library layout.

## Why not MB track/release IDs — and where tags *do* help

The obvious-looking approach is "read the MusicBrainz track id off each
playlist entry and look it up in the current library" — the same idea
:class:`~app.core.library_scanner.LibraryScanner` uses for albums. It
doesn't work here: that requires reading tags *from the playlist entry's
file*, and the entry is (by definition, for every broken entry) a path
that no longer resolves — there's nothing at the old path to read tags
from. Unlike the artist/album folder renames :class:`ArtistFixer
<app.core.artist_fixer.ArtistFixer>` and the VGMDB/beets pipeline
perform, nothing in this codebase renames or moves individual *track*
files — only their parent folders change. So the track's filename
survives a reorg even when its full path doesn't, which makes the
filename the one reliable signal a broken entry's *old* side carries.

The *candidate* side is a different story: those files still exist, so
their embedded title tag is available and is a real signal a bare
filename comparison misses — a track tagged accurately but filed under
a generic name ("01.mp3", a catalog number, ...) won't match on
filename alone. Every title comparison in the fuzzy paths below (see
``_title_match_ratio`` and ``resolve_within_album``) checks a
candidate's tag title alongside its filename-derived one and takes
whichever scores higher — cheap here because it only runs against an
already filename-narrowed handful of candidates, not the whole library
(reading tags for every file up front just to build the candidate pool
would be the expensive, whole-library version of this and isn't done).

## Matching strategy, per entry

1. **Unchanged** — the path resolves as-is (relative to the current
   artist root, or via its last three components against the current
   layout). Nothing moved.
2. **Renumbered** — the entry's *album folder* still resolves (only the
   track filename changed), and exactly one audio file in that folder
   starts with the same leading track number. Covers the single most
   common case for this codebase's own pipeline: beets/Picard rewriting
   "01.flac" to "01 - Title.flac" in place without touching the folder.
3. **Relocated** — no file at the old path, but exactly one audio file
   anywhere in the library shares its filename (case-insensitive). A
   folder rename, not a file rename.
4. **Fuzzy**, two sub-cases:
   * Exact filename found in more than one album — folder-name
     similarity picks which one (disambiguation; the filename match
     itself is already exact).
   * No exact filename anywhere — compared by *title* (filename minus
     extension and any leading track number) rather than the raw
     filename, since fixed boilerplate ("NN - ", the extension) makes
     two unrelated same-shaped filenames score close to a genuine
     match. A near-exact title match is trusted alone; a merely-decent
     one additionally needs the old entry's folder name to resemble the
     candidate's current folder — see ``_fuzzy_candidate_score``.
5. **Unmatched** — nothing cleared the bar. Logged and left out of the
   written playlist rather than failing the whole conversion (see
   module docstring goal in the tracking issue).
"""

from __future__ import annotations

import difflib
import logging
import re
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from mutagen import File as MutagenFile  # type: ignore[import-untyped]

from app.config import settings
from app.core.library_scanner import AUDIO_EXTS

log = logging.getLogger("music-lib-helper.playlist_converter")

_LEADING_TRACK_NO_RE = re.compile(r"^\s*(\d{1,3})")

SUPPORTED_SUFFIXES = {".m3u", ".m3u8"}

# Candidate-generation cutoff for difflib.get_close_matches against raw
# filenames — deliberately loose, since the real accept/reject decision
# happens afterward in _title_match_ratio(). Just needs to be low enough
# not to exclude genuine matches with a short title (a track-number
# prefix on a short title dominates a raw-filename ratio more than on a
# long one).
_FUZZY_CUTOFF = 0.5

_LEADING_TRACK_PREFIX_RE = re.compile(r"^\s*\d{1,3}\s*[-._]*\s*")

# Real title-preserving renames (a bare "Title.mp3" export gaining a
# "NN - " track-number prefix) keep the *title* text identical or
# near-identical once that prefix is stripped from both sides — so this
# can be a fairly high bar. Comparing raw filenames instead doesn't work:
# fixed-shape boilerplate ("NN - ", the extension) makes two *unrelated*
# same-shaped filenames (e.g. "99 - Gone.flac" vs "01 - Song.flac", ratio
# 0.71) score close to genuine matches (e.g. "Skyreach.mp3" vs
# "01 - Skyreach.mp3", ratio 0.83) — too narrow a gap to threshold
# reliably. Titles alone don't have that problem: "gone" vs "song" is
# 0.5, "skyreach" vs "skyreach" is 1.0.
_TITLE_MATCH_CUTOFF = 0.65

# At/above this title ratio, the title alone is trusted with no further
# corroboration — same word, punctuation/whitespace noise only.
_TITLE_EXACT_CUTOFF = 0.9

# Between _TITLE_MATCH_CUTOFF and _TITLE_EXACT_CUTOFF, title similarity
# by itself isn't discriminating enough on its own: two genuinely
# different tracks' titles can land at the same ratio as a real
# alternate-title/retranslation rename of the same track (real-world
# case: "Innen"/"LINE" — different songs — and "Shikoutazer"/"Shcowtaser"
# — the same one, an alternate romanization — both score 0.667). The old
# entry's folder context is the tiebreaker: junk for a lot of real
# playlists (see _resolve_entry step 4's docstring), but when it *does*
# still resemble the candidate's current folder — a whole soundtrack
# compilation staying under one release, just with minor punctuation
# drift ("Original Soundtrack 02" vs "Original Soundtrack 2") — that's
# real corroborating evidence, not noise.
#
# The comparison uses the last _FOLDER_CONTEXT_DEPTH directory
# components, not just the immediate parent — a single generic disc/
# volume label ("Disc 1", "Disc 2") is shared by many unrelated
# multi-disc albums and would otherwise pass this bar on its own
# (real-world case: "Innen" wrongly matched into a different multi-disc
# soundtrack that also happened to have a "Disc 1"/"Disc 2" split).
_FOLDER_CONTEXT_DEPTH = 2
_TITLE_FOLDER_CORROBORATION_CUTOFF = 0.75

# Fallback when neither side has title text left after stripping (bare
# numbered filenames like "01.flac"/"07.flac") — track numbers alone
# carry no identity, so two different numbers must never count as a
# match. Requires near-exact agreement on the raw filename instead.
_BARE_BASENAME_MATCH_CUTOFF = 0.9


@dataclass
class PlaylistEntry:
    """One resolved (or unresolved) playlist entry."""

    line_no: int
    original_path: str
    extinf: str | None
    resolved_path: str | None  # posix, relative to the artist root
    method: str  # "unchanged" | "renumbered" | "relocated" | "fuzzy" | "unmatched"


@dataclass
class ConversionResult:
    entries: list[PlaylistEntry]
    playlist_text: str
    total: int
    matched: int
    unmatched: int


class _PassthroughLine(str):
    """A comment/blank line preserved verbatim in the output."""


def parse_playlist(text: str) -> list[_PassthroughLine | tuple[str | None, str]]:
    """Parse M3U/M3U8 text into an ordered list of items.

    Each item is either a :class:`_PassthroughLine` (comment or blank
    line, copied to the output unchanged) or a ``(extinf, path)`` tuple
    for a track entry, where ``extinf`` is the raw ``#EXTINF:...`` line
    immediately preceding it (without the trailing newline), or ``None``.
    """
    items: list[_PassthroughLine | tuple[str | None, str]] = []
    pending_extinf: str | None = None

    for raw_line in text.splitlines():
        line = raw_line.strip("﻿").rstrip("\r")
        stripped = line.strip()

        if not stripped:
            items.append(_PassthroughLine(raw_line))
            continue

        if stripped.startswith("#EXTINF:"):
            pending_extinf = stripped
            continue

        if stripped.startswith("#"):
            items.append(_PassthroughLine(raw_line))
            continue

        items.append((pending_extinf, stripped))
        pending_extinf = None

    return items


class _LibraryIndex:
    """A live filename index over the current library, built once per
    conversion run. Not cached across runs — the whole point of this
    tool is to run it right after a reorg, so a stale index would defeat
    the purpose."""

    def __init__(self, artist_root: Path) -> None:
        self.artist_root = artist_root
        self.by_basename: dict[str, list[Path]] = {}
        self._all_basenames: list[str] = []

        if not artist_root.exists():
            return

        for f in artist_root.rglob("*"):
            if not f.is_file() or f.suffix.lower() not in AUDIO_EXTS:
                continue
            key = f.name.lower()
            self.by_basename.setdefault(key, []).append(f)

        self._all_basenames = list(self.by_basename.keys())

    def candidates_for_basename(self, basename: str) -> list[Path]:
        return self.by_basename.get(basename.lower(), [])

    def fuzzy_basenames(self, basename: str, n: int = 5) -> list[str]:
        return difflib.get_close_matches(
            basename.lower(), self._all_basenames, n=n, cutoff=_FUZZY_CUTOFF,
        )


def _tail_components(old_path: str, n: int) -> list[str]:
    """Last ``n`` path components of ``old_path``, normalising ``\\`` to
    ``/`` so Windows-exported playlists work too."""
    normalised = old_path.replace("\\", "/")
    parts = [p for p in PurePosixPath(normalised).parts if p not in ("", "/")]
    return parts[-n:] if len(parts) >= n else parts


def _folder_context(old_path: str) -> str:
    """The old entry's last :data:`_FOLDER_CONTEXT_DEPTH` directory
    components (excluding the filename), joined and lowercased."""
    parts = _tail_components(old_path, _FOLDER_CONTEXT_DEPTH + 1)
    return "/".join(parts[:-1]).lower()


def _candidate_folder_context(candidate: Path, artist_root: Path) -> str:
    """A candidate file's last :data:`_FOLDER_CONTEXT_DEPTH` directory
    components under ``artist_root``, joined and lowercased — the
    counterpart to :func:`_folder_context`."""
    rel_parts = candidate.relative_to(artist_root).parts[:-1]
    return "/".join(rel_parts[-_FOLDER_CONTEXT_DEPTH:]).lower()


def _folder_similarity(old_path: str, candidate: Path, artist_root: Path) -> float:
    """Similarity between the old entry's folder context and a
    candidate file's actual folder context — used to disambiguate when
    several files share a basename, and to corroborate a mid-confidence
    title match (see :data:`_TITLE_FOLDER_CORROBORATION_CUTOFF`)."""
    return difflib.SequenceMatcher(
        None, _folder_context(old_path), _candidate_folder_context(candidate, artist_root),
    ).ratio()


def _track_number(name: str) -> int | None:
    m = _LEADING_TRACK_NO_RE.match(name)
    return int(m.group(1)) if m else None


def _normalize_title_text(text: str) -> str:
    """Lowercased, with a leading track-number prefix stripped if
    present — the shared normalisation behind both a filename-derived
    title (:func:`_title_key`) and a tag-derived one
    (:func:`_candidate_tag_title`), so the two compare on equal footing."""
    return _LEADING_TRACK_PREFIX_RE.sub("", text, count=1).strip().lower()


def _title_key(filename: str) -> str:
    """``filename`` with its extension and a leading track-number prefix
    stripped, lowercased — isolates the part of a filename that actually
    carries a track's identity from the boilerplate (extension, "NN - ")
    every file in a library shares."""
    return _normalize_title_text(Path(filename).stem)


def _candidate_tag_title(candidate: Path) -> str | None:
    """``candidate``'s embedded title tag, normalised the same way as a
    filename-derived title, or ``None`` if unreadable/absent. A file
    filed under a generic or otherwise-unhelpful filename can still have
    an accurate title tag — this is what lets a match be found on that
    alone rather than only ever comparing filenames. Read lazily, one
    file at a time, only for candidates a filename pass has already
    narrowed down to a handful — see the module docstring."""
    try:
        audio = MutagenFile(str(candidate), easy=True)
    except Exception as exc:  # noqa: BLE001 — mutagen raises many types
        log.debug("could not read tags from %s: %s", candidate, exc)
        return None
    if not audio or not audio.tags:
        return None
    val = audio.tags.get("title")
    if not val:
        return None
    text = str(val[0]) if isinstance(val, list) else str(val)
    return _normalize_title_text(text) or None


def _best_title_ratio(old_title: str, candidate: Path) -> float:
    """The higher of two ratios between ``old_title`` and ``candidate``:
    against its filename-derived title, and against its tag-derived one
    (when the tag is readable and non-empty). Tags only ever raise the
    score here, never lower it — a missing/unreadable tag just falls
    back to the filename comparison that already existed."""
    ratio = difflib.SequenceMatcher(None, old_title, _title_key(candidate.name)).ratio()
    tag_title = _candidate_tag_title(candidate)
    if tag_title:
        ratio = max(ratio, difflib.SequenceMatcher(None, old_title, tag_title).ratio())
    return ratio


def _title_match_ratio(old_basename: str, candidate: Path) -> tuple[float, float]:
    """Return ``(ratio, cutoff-to-clear)`` for how well ``candidate``
    matches ``old_basename``. Compares titles (filename- and tag-derived,
    see ``_best_title_ratio``) when the old side has title text left
    after stripping; falls back to a strict raw-filename comparison for
    a bare track number (see ``_BARE_BASENAME_MATCH_CUTOFF``) — the old
    side's tag can't be read (see the module docstring), so a tag-only
    comparison isn't possible there."""
    old_title = _title_key(old_basename)
    if old_title:
        return _best_title_ratio(old_title, candidate), _TITLE_MATCH_CUTOFF
    return (
        difflib.SequenceMatcher(None, old_basename.lower(), candidate.name.lower()).ratio(),
        _BARE_BASENAME_MATCH_CUTOFF,
    )


def _fuzzy_candidate_score(
    old_path: str, old_basename: str, candidate: Path, artist_root: Path,
) -> float | None:
    """Score ``candidate`` for the branch-4 "no exact basename anywhere"
    fallback, or ``None`` to reject it outright. A title ratio below its
    cutoff is always rejected; a bare-basename match or a near-exact
    title match (>= _TITLE_EXACT_CUTOFF) is trusted on its own; anything
    in between additionally needs the old entry's folder-context
    similarity to clear _TITLE_FOLDER_CORROBORATION_CUTOFF (see that
    constant)."""
    ratio, cutoff = _title_match_ratio(old_basename, candidate)
    if ratio < cutoff:
        return None
    if cutoff == _BARE_BASENAME_MATCH_CUTOFF or ratio >= _TITLE_EXACT_CUTOFF:
        return ratio
    if _folder_similarity(old_path, candidate, artist_root) >= _TITLE_FOLDER_CORROBORATION_CUTOFF:
        return ratio
    return None


def _resolve_entry(old_path: str, index: _LibraryIndex) -> tuple[Path | None, str]:
    """Return ``(resolved_absolute_path, method)``. ``resolved_absolute_path``
    is ``None`` when nothing matched ("unmatched")."""
    artist_root = index.artist_root

    # 1. Unchanged — the literal path resolves, or its tail (mount-prefix
    #    differences only) resolves under the current artist root.
    literal = Path(old_path)
    if literal.is_absolute() and literal.exists() and literal.is_file():
        return literal, "unchanged"

    tail = _tail_components(old_path, 3)
    if tail:
        candidate = artist_root.joinpath(*tail)
        if candidate.exists() and candidate.is_file():
            return candidate, "unchanged"

    basename = Path(old_path.replace("\\", "/")).name
    if not basename:
        return None, "unmatched"

    # 2. Renumbered — the album folder itself still resolves; only the
    #    track filename changed. Match by leading track number within it.
    if len(tail) >= 2:
        album_dir = artist_root.joinpath(*tail[:-1])
        old_no = _track_number(basename)
        if album_dir.is_dir() and old_no is not None:
            same_no = [
                f for f in album_dir.iterdir()
                if f.is_file() and f.suffix.lower() in AUDIO_EXTS
                and _track_number(f.name) == old_no
            ]
            if len(same_no) == 1:
                return same_no[0], "renumbered"

    # 3. Relocated — exactly one file library-wide shares this filename.
    exact = index.candidates_for_basename(basename)
    if len(exact) == 1:
        return exact[0], "relocated"

    if len(exact) > 1:
        # Same filename lives under several albums — the old entry's
        # filename is already an exact match, so this is disambiguation
        # (which one?), not validation (is this real?). Old-playlist
        # parent-folder names are frequently unrelated junk (e.g. a
        # descriptive download-tool folder name, not "Artist/Album"), so
        # folder similarity is used to rank, not to gate.
        best = max(exact, key=lambda p: _folder_similarity(old_path, p, artist_root))
        return best, "fuzzy"

    # 4. Fuzzy — no exact filename match anywhere; try close spellings
    #    (see _fuzzy_candidate_score for the title+folder scoring).
    fuzzy_names = index.fuzzy_basenames(basename)
    fuzzy_candidates = [p for name in fuzzy_names for p in index.candidates_for_basename(name)]
    scored = [
        (score, p) for p in fuzzy_candidates
        if (score := _fuzzy_candidate_score(old_path, basename, p, artist_root)) is not None
    ]
    if scored:
        _, best = max(scored, key=lambda t: t[0])
        return best, "fuzzy"

    return None, "unmatched"


def default_artist_root() -> Path:
    """The artist root ``convert()`` uses when the caller doesn't pass
    one explicitly — shared with the manual-match endpoints so they
    resolve album/track paths against the exact same root."""
    return settings.app_music_dir / "synced_music" / "Artist"


def list_album_tracks(album_dir: Path) -> list[Path]:
    """Every audio file under ``album_dir``, at any depth — multi-disc
    albums in this library keep tracks under a "Disc 1"/"Disc 2"
    subfolder rather than directly in the album folder (same as
    :meth:`LibraryScanner._has_audio`'s recursive search), so a
    non-recursive listing would come back empty for those. Sorted by
    path relative to ``album_dir`` so discs group together and stay in
    disc order rather than interleaving same-numbered tracks across
    discs. Empty list if ``album_dir`` doesn't exist."""
    if not album_dir.is_dir():
        return []
    return sorted(
        (f for f in album_dir.rglob("*") if f.is_file() and f.suffix.lower() in AUDIO_EXTS),
        key=lambda f: f.relative_to(album_dir).as_posix().lower(),
    )


def resolve_within_album(old_basename: str, album_dir: Path) -> Path | None:
    """Best-effort match of ``old_basename`` against audio files already
    known to live in ``album_dir`` — used when a person has manually
    picked the correct album for an entry the fully-automatic pass
    couldn't place. A deliberately self-contained, lower-bar search
    (exact name, then track number, then title similarity alone, with no
    folder-corroboration requirement): unlike :func:`_resolve_entry`'s
    step 4, the album is no longer in question — a person already
    vouched for it — so this only needs to pick the right track inside
    it. Returns ``None`` if nothing in the folder is a plausible match,
    same as ``_resolve_entry`` — the caller then falls back to listing
    ``list_album_tracks`` and lets the person pick directly."""
    candidates = list_album_tracks(album_dir)
    if not candidates:
        return None

    for f in candidates:
        if f.name.lower() == old_basename.lower():
            return f

    old_no = _track_number(old_basename)
    if old_no is not None:
        same_no = [f for f in candidates if _track_number(f.name) == old_no]
        if len(same_no) == 1:
            return same_no[0]

    old_title = _title_key(old_basename)
    if old_title:
        # Checks each candidate's tag title alongside its filename-derived
        # one (see _best_title_ratio) — a track filed under a generic
        # name within the right album can still be found by its tag.
        scored = [
            (ratio, f) for f in candidates
            if (ratio := _best_title_ratio(old_title, f)) >= _TITLE_MATCH_CUTOFF
        ]
        if scored:
            return max(scored, key=lambda t: t[0])[1]

    return None


def convert(
    playlist_text: str,
    *,
    artist_root: Path | None = None,
) -> ConversionResult:
    """Remap every entry in ``playlist_text`` to its current on-disk
    location. Pure function over text in/out — the caller decides where
    the input came from and where the output goes (upload/download,
    a file on disk, etc.)."""
    root = artist_root or default_artist_root()
    index = _LibraryIndex(root)

    items = parse_playlist(playlist_text)
    entries: list[PlaylistEntry] = []
    out_lines: list[str] = []
    matched = 0

    line_no = 0
    for item in items:
        line_no += 1
        if isinstance(item, _PassthroughLine):
            out_lines.append(str(item))
            continue

        extinf, old_path = item
        resolved, method = _resolve_entry(old_path, index)

        if resolved is None:
            log.warning("playlist entry unmatched, skipping: %s", old_path)
            entries.append(PlaylistEntry(line_no, old_path, extinf, None, "unmatched"))
            out_lines.append(f"# UNMATCHED: {old_path}")
            continue

        matched += 1
        rel = resolved.relative_to(root).as_posix()
        entries.append(PlaylistEntry(line_no, old_path, extinf, rel, method))
        if extinf:
            out_lines.append(extinf)
        out_lines.append(str(resolved))

    text = "\n".join(out_lines) + "\n"

    return ConversionResult(
        entries=entries,
        playlist_text=text,
        total=len(entries),
        matched=matched,
        unmatched=len(entries) - matched,
    )
