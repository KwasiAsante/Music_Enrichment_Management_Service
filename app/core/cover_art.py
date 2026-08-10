"""Cover-art lookup for one album folder.

Tries, in order:

1. A conventional cover-image file at the top of the folder
   (``cover.jpg``, ``folder.png``, etc. — case-insensitive).
2. Whatever picture is embedded in the folder's first audio file — ID3
   ``APIC`` (MP3, ID3-tagged WAV), FLAC's ``.pictures``, MP4/M4A/AAC's
   ``covr`` atom, or Ogg Vorbis/Opus's base64 ``METADATA_BLOCK_PICTURE``.

APE/WavPack embedded art isn't exposed through mutagen's simple API and
is skipped — folder-level cover files still work for those.

Every function here is best-effort: on any failure (corrupt tags,
permission error, unsupported format) they return ``None`` rather than
raising, so ``GET /api/v1/library/art`` can just 404 and let the frontend
fall back to a placeholder instead of the whole request blowing up over
one bad file.
"""

from __future__ import annotations

import base64
import logging
from pathlib import Path

from mutagen import File as MutagenFile  # type: ignore[import-untyped]
from mutagen.flac import Picture  # type: ignore[import-untyped]

log = logging.getLogger("music-lib-helper.cover_art")

# Same set used by LibraryScanner/VGMDBMapper/BeetsEnricher for "is this
# an audio file" checks.
AUDIO_EXTS: set[str] = {
    ".mp3", ".flac", ".ogg", ".opus", ".m4a", ".aac", ".wav", ".ape",
}

_COVER_FILENAMES = (
    "cover.jpg", "cover.jpeg", "cover.png",
    "folder.jpg", "folder.jpeg", "folder.png",
    "front.jpg", "front.jpeg", "front.png",
    "albumart.jpg", "albumart.jpeg", "albumart.png",
)

_MIME_BY_EXT = {".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png"}


def find_cover_art(album_dir: Path) -> tuple[bytes, str] | None:
    """Return ``(image_bytes, mime_type)`` for an album folder, or
    ``None`` if nothing usable was found."""
    if not album_dir.is_dir():
        return None
    return _find_cover_file(album_dir) or _find_embedded_art(album_dir)


def _find_cover_file(album_dir: Path) -> tuple[bytes, str] | None:
    try:
        names = {p.name.lower(): p for p in album_dir.iterdir() if p.is_file()}
    except OSError as exc:
        log.debug("could not list %s: %s", album_dir, exc)
        return None

    for candidate in _COVER_FILENAMES:
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
        return data, _MIME_BY_EXT.get(path.suffix.lower(), "image/jpeg")
    return None


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


def _find_embedded_art(album_dir: Path) -> tuple[bytes, str] | None:
    audio_path = _first_audio_file(album_dir)
    if audio_path is None:
        return None

    try:
        audio = MutagenFile(str(audio_path), easy=False)
    except Exception as exc:  # noqa: BLE001 — mutagen raises many types
        log.debug("could not open %s for art: %s", audio_path, exc)
        return None
    if audio is None:
        return None

    # FLAC / OggFLAC expose a dedicated `.pictures` list.
    pictures = getattr(audio, "pictures", None)
    if pictures:
        pic = pictures[0]
        if pic.data:
            return pic.data, pic.mime or "image/jpeg"

    tags = getattr(audio, "tags", None)
    if tags is None:
        return None

    # ID3 (MP3, ID3-tagged WAV): APIC frames, possibly several (front
    # cover, back cover, ...) — take the first.
    if hasattr(tags, "getall"):
        apics = tags.getall("APIC")
        if apics and apics[0].data:
            return apics[0].data, apics[0].mime or "image/jpeg"

    # MP4 / M4A / AAC: 'covr' atom — a list of MP4Cover (a bytes subclass
    # with an .imageformat attribute).
    covr = tags.get("covr") if hasattr(tags, "get") else None
    if covr:
        cover = covr[0]
        mime = "image/jpeg"
        try:
            from mutagen.mp4 import MP4Cover  # type: ignore[import-untyped]
            if cover.imageformat == MP4Cover.FORMAT_PNG:
                mime = "image/png"
        except Exception:  # noqa: BLE001
            pass
        if bytes(cover):
            return bytes(cover), mime

    # Ogg Vorbis / Opus: base64-encoded FLAC Picture block(s).
    block = tags.get("metadata_block_picture") if hasattr(tags, "get") else None
    if block:
        try:
            pic = Picture(base64.b64decode(block[0]))
            if pic.data:
                return pic.data, pic.mime or "image/jpeg"
        except Exception as exc:  # noqa: BLE001
            log.debug("could not decode metadata_block_picture in %s: %s", audio_path, exc)

    return None
