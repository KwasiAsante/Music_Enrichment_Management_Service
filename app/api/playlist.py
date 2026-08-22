"""``/api/v1/playlist/*`` — remap an existing playlist to the current
library layout, with a manual fallback for entries the automatic pass
can't place.

Two endpoints:

* ``POST /convert`` — upload a ``.m3u``/``.m3u8`` file exported before a
  re-tagging/re-organizing run, get back the same playlist with every
  entry pointed at its current on-disk location (see
  ``app.core.playlist_converter`` for the matching strategy and why it
  isn't MB-id based). Same upload-in/download-out shape as
  ``POST /mapping/import`` — see that router's docstring. The response's
  ``entries`` carry enough structured data (``extinf``, ``resolved_path``,
  ``artist_root``) for the browser to rebuild the playlist text itself
  after a manual fix, with no second convert round trip.
* ``GET /album-tracks`` — the manual-match flow for an entry the
  automatic pass left unmatched (or a person just wants to double-check/
  override). A person searches for the right album via the existing
  ``GET /library/albums?q=`` (no new search endpoint needed — that one
  already does free-text substring search across artist, album, and
  folder), then this lists that album's actual audio files plus a
  best-effort suggestion, so picking the right one is usually one click,
  not a manual browse.
"""

from __future__ import annotations

import logging
from pathlib import Path

from fastapi import APIRouter, File, HTTPException, Query, UploadFile

from app.core.playlist_converter import (
    SUPPORTED_SUFFIXES,
    convert,
    default_artist_root,
    list_album_tracks,
    resolve_within_album,
)
from app.models.playlist import (
    AlbumTrack,
    AlbumTracksResult,
    PlaylistConvertResult,
    PlaylistEntryResult,
)
from app.storage import db

log = logging.getLogger("music-lib-helper.api.playlist")

router = APIRouter(prefix="/api/v1/playlist", tags=["playlist"])


def _resolve_folder(folder: str) -> Path:
    """Resolve an album folder (relative to the artist root) to an
    absolute path, raising 400 if it would escape the root (path
    traversal) — same guard as ``app.api.library._resolve_album_dir``,
    kept local rather than imported since the two routers don't
    otherwise share private helpers."""
    artist_root = default_artist_root().resolve()
    album_dir = (artist_root / folder).resolve()
    if not album_dir.is_relative_to(artist_root):
        raise HTTPException(400, "folder must resolve inside the artist root")
    return album_dir


# ── POST /playlist/convert ──────────────────────────────────────────────────
@router.post("/convert", response_model=PlaylistConvertResult)
async def convert_playlist(
    file: UploadFile = File(..., description="A .m3u or .m3u8 playlist file."),
) -> PlaylistConvertResult:
    """Remap every entry in the uploaded playlist to its current path.

    Unmatched entries are logged and left out of the returned playlist
    (as a ``# UNMATCHED: <original path>`` comment) rather than failing
    the whole conversion — see ``entries`` for the full per-line report.
    """
    filename = file.filename or "playlist.m3u"
    if Path(filename).suffix.lower() not in SUPPORTED_SUFFIXES:
        raise HTTPException(
            400, f"unsupported playlist format — expected one of {sorted(SUPPORTED_SUFFIXES)}",
        )

    raw = await file.read()
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise HTTPException(400, f"could not decode file as UTF-8: {exc}") from exc

    job_id = db.create_job("playlist_convert")
    db.update_job(job_id, status="running")
    log.info("playlist convert requested: %s (job=%s)", filename, job_id)

    try:
        result = convert(text)
    except Exception as exc:  # noqa: BLE001
        log.exception("playlist convert job %s failed", job_id)
        db.update_job(job_id, status="failed", append_log=str(exc))
        db.add_activity("playlist", f"convert failed for {filename}: {exc}", level="error")
        raise HTTPException(500, f"playlist conversion failed: {exc}") from exc

    db.update_job(
        job_id, status="success",
        progress_current=result.total, progress_total=result.total,
        result={"total": result.total, "matched": result.matched, "unmatched": result.unmatched},
    )
    summary = f"converted {filename}: {result.matched}/{result.total} matched"
    db.add_activity("playlist", summary, level="warning" if result.unmatched else "info")

    return PlaylistConvertResult(
        filename=filename,
        artist_root=str(default_artist_root()),
        total=result.total,
        matched=result.matched,
        unmatched=result.unmatched,
        entries=[
            PlaylistEntryResult(
                line_no=e.line_no, original_path=e.original_path, extinf=e.extinf,
                resolved_path=e.resolved_path, method=e.method,
            )
            for e in result.entries
        ],
        playlist=result.playlist_text,
        job_id=job_id,
    )


# ── GET /playlist/album-tracks ──────────────────────────────────────────────
@router.get("/album-tracks", response_model=AlbumTracksResult)
def album_tracks(
    folder: str = Query(
        ...,
        description="An album folder relative to the artist root, e.g. "
        "'Some Artist/Some Album' — from GET /library/albums.",
    ),
    original_path: str | None = Query(
        default=None,
        description="A playlist entry's original path, to compute a "
        "best-effort suggested_path within this album.",
    ),
) -> AlbumTracksResult:
    """List one album folder's audio files for manual matching, with a
    best-effort ``suggested_path`` when ``original_path`` is given.
    """
    album_dir = _resolve_folder(folder)
    artist_root = default_artist_root().resolve()

    tracks = [
        AlbumTrack(
            path=t.relative_to(artist_root).as_posix(),
            filename=t.name,
            relative_path=t.relative_to(album_dir).as_posix(),
        )
        for t in list_album_tracks(album_dir)
    ]

    suggested_path: str | None = None
    if original_path:
        basename = Path(original_path.replace("\\", "/")).name
        if basename:
            match = resolve_within_album(basename, album_dir)
            if match is not None:
                suggested_path = match.relative_to(artist_root).as_posix()

    return AlbumTracksResult(folder=folder, tracks=tracks, suggested_path=suggested_path)
