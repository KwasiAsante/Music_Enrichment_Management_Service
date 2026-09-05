"""Raw-tag fallback for non-standard ("custom") tag keys on formats whose
mutagen "easy" interface — used everywhere else in this codebase — is a
*closed* key set rather than a free-form dict.

Vorbis-comment and APEv2 formats (``.flac .ogg .opus .ape``) need none of
this: their easy-interface tags object already behaves like an arbitrary
string-keyed dict, so an unrecognised key just works. ID3 (``.mp3 .wav``)
and MP4 (``.m4a .aac``) restrict ``easy=True`` to a fixed set of
registered keys, and hide any frame/atom outside that set from
``.tags.keys()`` entirely — a custom value has to live in an ID3
``TXXX:<desc>`` frame or an MP4 freeform ``----:mean:name`` atom instead,
read and written here via mutagen's raw (non-easy) tag objects.

Best-effort throughout, same convention as
:mod:`app.core.field_overrides`: any failure is logged and treated as
"nothing to report" rather than raised, so one bad file never blocks an
album-wide read or write.
"""

from __future__ import annotations

import logging
from pathlib import Path

from mutagen import File as MutagenFile  # type: ignore[import-untyped]
from mutagen.id3 import TXXX  # type: ignore[import-untyped]
from mutagen.mp4 import MP4FreeForm  # type: ignore[import-untyped]

log = logging.getLogger("music-lib-helper.custom_tags")

ID3_EXTS = {".mp3", ".wav"}
MP4_EXTS = {".m4a", ".aac"}
RAW_FALLBACK_EXTS = ID3_EXTS | MP4_EXTS

_MP4_FREEFORM_MEAN = "com.apple.iTunes"


def read_custom_tags(path: Path) -> dict[str, str]:
    """Every custom (non-standard) tag on one file: ID3 ``TXXX`` frames
    keyed by their description, or MP4 freeform atoms keyed by their
    name. Empty for any other format — its easy-interface dict already
    surfaces everything."""
    ext = path.suffix.lower()
    if ext not in RAW_FALLBACK_EXTS:
        return {}

    try:
        audio = MutagenFile(str(path))
    except Exception as exc:  # noqa: BLE001
        log.debug("could not open %s for custom tags: %s", path, exc)
        return {}
    if audio is None or audio.tags is None:
        return {}

    out: dict[str, str] = {}
    if ext in ID3_EXTS:
        for frame in audio.tags.values():
            if getattr(frame, "FrameID", None) == "TXXX" and frame.text:
                out[frame.desc] = str(frame.text[0])
    else:  # MP4_EXTS
        for key, value in audio.tags.items():
            if not key.startswith("----:") or not value:
                continue
            name = key.split(":", 2)[-1]
            try:
                out[name] = bytes(value[0]).decode("utf-8", "replace")
            except Exception as exc:  # noqa: BLE001
                log.debug("could not decode freeform atom %s on %s: %s", key, path, exc)
    return out


def write_custom_tag(path: Path, key: str, value: str) -> bool:
    """Set one custom tag's value. Returns True if the file was saved."""
    ext = path.suffix.lower()
    if ext not in RAW_FALLBACK_EXTS:
        return False

    try:
        audio = MutagenFile(str(path))
        if audio is None:
            return False
        if audio.tags is None:
            audio.add_tags()

        if ext in ID3_EXTS:
            audio.tags.setall(f"TXXX:{key}", [TXXX(encoding=3, desc=key, text=[value])])
        else:  # MP4_EXTS
            audio.tags[f"----:{_MP4_FREEFORM_MEAN}:{key}"] = [MP4FreeForm(value.encode("utf-8"))]

        audio.save()
        return True
    except Exception as exc:  # noqa: BLE001
        log.debug("could not write custom tag %r on %s: %s", key, path, exc)
        return False


def delete_custom_tag(path: Path, key: str) -> bool:
    """Remove one custom tag. Returns True if the file was saved."""
    ext = path.suffix.lower()
    if ext not in RAW_FALLBACK_EXTS:
        return False

    try:
        audio = MutagenFile(str(path))
        if audio is None or audio.tags is None:
            return False

        if ext in ID3_EXTS:
            audio.tags.delall(f"TXXX:{key}")
        else:  # MP4_EXTS
            mp4_key = f"----:{_MP4_FREEFORM_MEAN}:{key}"
            if mp4_key not in audio.tags:
                return False
            del audio.tags[mp4_key]

        audio.save()
        return True
    except Exception as exc:  # noqa: BLE001
        log.debug("could not delete custom tag %r on %s: %s", key, path, exc)
        return False
