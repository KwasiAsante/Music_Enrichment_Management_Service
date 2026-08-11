"""Cover-art lookup for one album folder — front cover, and back cover
when one can be identified.

Tries, in order:

1. Conventional cover-image files at the top of the folder
   (``cover.jpg``/``back.jpg``/etc. — case-insensitive).
2. Whatever's embedded in the folder's first audio file — ID3 ``APIC``
   (MP3, ID3-tagged WAV), FLAC's ``.pictures``, MP4/M4A/AAC's ``covr``
   atom, or Ogg Vorbis/Opus's base64 ``METADATA_BLOCK_PICTURE``. Where
   the format carries a picture-type marker (ID3/FLAC use the same
   numbering: 3 = front cover, 4 = back cover), that's used to tell the
   two apart; MP4's ``covr`` has no such marker, so a second image (if
   any) is assumed to be the back cover.

APE/WavPack embedded art isn't exposed through mutagen's simple API and
is skipped — folder-level cover files still work for those.

Every function here is best-effort: on any failure (corrupt tags,
permission error, unsupported format) they return ``None``/omit the key
rather than raising, so callers can just 404 and let the frontend fall
back to a placeholder instead of the whole request blowing up over one
bad file.
"""

from __future__ import annotations

import base64
import logging
from pathlib import Path
from typing import Literal

from mutagen import File as MutagenFile  # type: ignore[import-untyped]
from mutagen.flac import Picture  # type: ignore[import-untyped]

log = logging.getLogger("music-lib-helper.cover_art")

Side = Literal["front", "back"]
ArtResult = tuple[bytes, str]  # (image_bytes, mime_type)

# Same set used by LibraryScanner/VGMDBMapper/BeetsEnricher for "is this
# an audio file" checks.
AUDIO_EXTS: set[str] = {
    ".mp3", ".flac", ".ogg", ".opus", ".m4a", ".aac", ".wav", ".ape",
}

# ID3's PictureType enum (FLAC's Picture.type reuses the same numbering):
# 3 = front cover, 4 = back cover.
_PICTURE_TYPE_FRONT = 3
_PICTURE_TYPE_BACK = 4

_FRONT_FILENAMES = (
    "cover.jpg", "cover.jpeg", "cover.png",
    "folder.jpg", "folder.jpeg", "folder.png",
    "front.jpg", "front.jpeg", "front.png",
    "albumart.jpg", "albumart.jpeg", "albumart.png",
)
_BACK_FILENAMES = (
    "back.jpg", "back.jpeg", "back.png",
    "backcover.jpg", "backcover.jpeg", "backcover.png",
    "cover_back.jpg", "cover_back.jpeg", "cover_back.png",
)

_MIME_BY_EXT = {".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png"}


def find_cover_art(album_dir: Path) -> ArtResult | None:
    """Return ``(image_bytes, mime_type)`` for the front cover, or
    ``None`` if nothing usable was found. Thin wrapper over
    :func:`find_album_art` for callers that only want the front image."""
    return find_album_art(album_dir).get("front")


def find_album_art(album_dir: Path) -> dict[Side, ArtResult]:
    """Return whichever of ``{"front": ..., "back": ...}`` could be found
    for an album folder — either key may be absent, and the dict may be
    empty. Checks folder-level cover files first, then falls back to
    whatever's embedded in the first audio file for any side not already
    found.
    """
    if not album_dir.is_dir():
        return {}

    found: dict[Side, ArtResult] = _find_cover_files(album_dir)
    if len(found) < 2:
        for side, art in _find_embedded_art(album_dir).items():
            found.setdefault(side, art)
    return found


def _find_cover_files(album_dir: Path) -> dict[Side, ArtResult]:
    try:
        names = {p.name.lower(): p for p in album_dir.iterdir() if p.is_file()}
    except OSError as exc:
        log.debug("could not list %s: %s", album_dir, exc)
        return {}

    found: dict[Side, ArtResult] = {}
    for side, filenames in (("front", _FRONT_FILENAMES), ("back", _BACK_FILENAMES)):
        for candidate in filenames:
            path = names.get(candidate)
            if path is None:
                continue
            try:
                data = path.read_bytes()
            except OSError as exc:
                log.debug("could not read %s: %s", path, exc)
                continue
            if not data:
                continue
            found[side] = (data, _MIME_BY_EXT.get(path.suffix.lower(), "image/jpeg"))
            break
    return found


def _first_audio_file(album_dir: Path) -> Path | None:
    try:
        candidates = sorted(
            f for f in album_dir.rglob("*")
            if f.is_file() and f.suffix.lower() in AUDIO_EXTS
        )
    except OSError as exc:
        log.debug("could not walk %s: %s", album_dir, exc)
        return None
    return candidates[0] if candidates else None


def _find_embedded_art(album_dir: Path) -> dict[Side, ArtResult]:
    audio_path = _first_audio_file(album_dir)
    if audio_path is None:
        return {}

    try:
        audio = MutagenFile(str(audio_path), easy=False)
    except Exception as exc:  # noqa: BLE001 — mutagen raises many types
        log.debug("could not open %s for art: %s", audio_path, exc)
        return {}
    if audio is None:
        return {}

    found: dict[Side, ArtResult] = {}

    # FLAC / OggFLAC expose a dedicated `.pictures` list, each with a
    # `.type` matching ID3's PictureType numbering.
    pictures = getattr(audio, "pictures", None)
    if pictures:
        _classify_pictures(pictures, found)

    tags = getattr(audio, "tags", None)
    if tags is not None:
        # ID3 (MP3, ID3-tagged WAV): APIC frames, each with a `.type`.
        if hasattr(tags, "getall"):
            apics = tags.getall("APIC")
            if apics:
                _classify_pictures(apics, found)

        # MP4 / M4A / AAC: 'covr' atom — a list of MP4Cover (a bytes
        # subclass with an .imageformat attribute, but no picture-type
        # marker). First entry is front; a second, if present, is
        # assumed to be back.
        if len(found) < 2:
            covr = tags.get("covr") if hasattr(tags, "get") else None
            if covr:
                for side, cover in zip(("front", "back"), covr):
                    if side in found or not bytes(cover):
                        continue
                    mime = "image/jpeg"
                    try:
                        from mutagen.mp4 import MP4Cover  # type: ignore[import-untyped]
                        if cover.imageformat == MP4Cover.FORMAT_PNG:
                            mime = "image/png"
                    except Exception:  # noqa: BLE001
                        pass
                    found[side] = (bytes(cover), mime)

        # Ogg Vorbis / Opus: base64-encoded FLAC Picture block(s), each
        # with the same `.type` numbering once decoded.
        if len(found) < 2:
            block = tags.get("metadata_block_picture") if hasattr(tags, "get") else None
            if block:
                decoded = []
                for raw in block:
                    try:
                        decoded.append(Picture(base64.b64decode(raw)))
                    except Exception as exc:  # noqa: BLE001
                        log.debug("could not decode metadata_block_picture in %s: %s",
                                  audio_path, exc)
                if decoded:
                    _classify_pictures(decoded, found)

    return found


def _classify_pictures(pictures: list, found: dict[Side, ArtResult]) -> None:
    """Sort a list of ID3/FLAC-style picture objects (each with `.data`,
    `.mime`, `.type`) into ``found["front"]``/``found["back"]`` by their
    picture-type marker, falling back to positional order (first unused
    picture -> front, next -> back) for pictures with an unrecognised
    type. Mutates ``found`` in place; never overwrites an already-found
    side.
    """
    leftovers = []
    for pic in pictures:
        if not getattr(pic, "data", None):
            continue
        ptype = getattr(pic, "type", None)
        mime = getattr(pic, "mime", None) or "image/jpeg"
        if ptype == _PICTURE_TYPE_FRONT and "front" not in found:
            found["front"] = (pic.data, mime)
        elif ptype == _PICTURE_TYPE_BACK and "back" not in found:
            found["back"] = (pic.data, mime)
        else:
            leftovers.append((pic.data, mime))

    for side in ("front", "back"):
        if side in found or not leftovers:
            continue
        found[side] = leftovers.pop(0)
