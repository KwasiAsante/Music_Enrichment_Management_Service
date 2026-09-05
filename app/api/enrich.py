"""``/api/v1/enrich/*`` — album enrichment endpoints.

Four endpoints:

* ``POST /enrich/album``           — synchronous single-album flow,
                                     called by the Lidarr OnAlbumDownload
                                     slim wrapper.
* ``POST /enrich/run``              — bulk enrichment kicked off in a
                                     background thread. Returns ``job_id``;
                                     poll ``/jobs/{job_id}``.
* ``GET  /enrich/jobs/{job_id}``    — current status of a running or
                                     finished bulk job.
* ``GET  /enrich/log``              — recent activity_log rows (``enrich``
                                     category) for the Logs page.
* ``GET  /enrich/export``           — download the enriched-albums log
                                     (``enriched_albums.json``) as a backup file.
* ``POST /enrich/import``           — restore/merge the enriched-albums log
                                     from a backup file.

The bulk runner deliberately uses a thin daemon-thread rather than
FastAPI's BackgroundTasks: BackgroundTasks runs *after* the response is
sent but still on the request's worker, which would let a long-running
enrichment block its uvicorn worker. The thread approach scales to
"client polls until done" without blocking new requests.
"""

from __future__ import annotations

import json
import logging
import threading
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, File, HTTPException, Path as PathParam, Query, UploadFile
from fastapi.responses import JSONResponse

from app.core.beets_enricher import BeetsEnricher
from app.core.enriched_albums import EnrichedAlbumsService
from app.core.library_scanner import LibraryScanner
from app.models.enrich import (
    EnrichAlbumRequest,
    EnrichAlbumResult,
    EnrichJobStatus,
    EnrichLogEntry,
    EnrichRunRequest,
    EnrichRunStarted,
    ImportEnrichedAlbumsResult,
)
from app.storage import db

log = logging.getLogger("music-lib-helper.api.enrich")

router = APIRouter(prefix="/api/v1/enrich", tags=["enrich"])


# ── POST /enrich/album ─────────────────────────────────────────────────────
@router.post("/album", response_model=EnrichAlbumResult)
def enrich_album(
    req: EnrichAlbumRequest,
    dry_run: bool = Query(
        default=False,
        description="Resolve the VGMDB id but skip the beet import, tag "
        "fixes, and notifications.",
    ),
) -> EnrichAlbumResult:
    """Enrich one album. Called by the Lidarr OnAlbumDownload wrapper."""

    # Accept Test / empty / non-AlbumDownload events as no-ops so the
    # Lidarr Connect UI's Test button doesn't generate noise.
    et = (req.event_type or "").strip()
    if et.lower() in ("", "test"):
        return EnrichAlbumResult(
            ok=True, source="event_ignored",
            message=f"event_type={et or '(empty)'} — ignored",
        )
    if et.lower() != "albumdownload":
        return EnrichAlbumResult(
            ok=True, source="event_ignored",
            message=f"event_type={et} is not AlbumDownload — ignored",
        )

    if not req.mb_release_id:
        return EnrichAlbumResult(
            ok=False, source="no_mb_release_id",
            artist=req.artist_name, album=req.album_title,
            message="no MB release id — cannot enrich",
        )

    album_folder = _album_folder_from_tracks(req.track_paths)
    if album_folder is None:
        return EnrichAlbumResult(
            ok=False, source="folder_not_found",
            artist=req.artist_name, album=req.album_title,
            mb_release_id=req.mb_release_id,
            message=f"could not derive album folder from track_paths: {req.track_paths}",
        )

    job_id = db.create_job("enrich_album")
    db.update_job(job_id, status="running")
    log.info("enrich-album requested: %s — %s (job=%s)",
             req.artist_name, req.album_title, job_id)

    info = {
        "artist":        req.artist_name,
        "album":         req.album_title,
        "mb_release_id": req.mb_release_id,
        "folder":        str(album_folder),
    }
    try:
        result = BeetsEnricher().enrich_album(info, allow_search=True, dry_run=dry_run)
    except Exception as exc:  # noqa: BLE001
        log.exception("enrich_album job %s failed", job_id)
        db.update_job(job_id, status="failed", append_log=str(exc))
        db.add_activity(
            "enrich", f"album enrichment crashed: {exc}",
            level="error", artist=req.artist_name, album=req.album_title,
        )
        raise HTTPException(500, f"enrich failed: {exc}") from exc

    if not dry_run:
        # Keep album_list.json current without waiting on the next cron
        # scan — mirrors BeetsEnricher.run_bulk()'s post-enrich scan.
        try:
            LibraryScanner().scan(dry_run=False, cleanup=False)
        except Exception as exc:  # noqa: BLE001
            log.warning("post-enrich library scan failed: %s", exc)

    db.update_job(job_id, status="success" if result["ok"] else "failed",
                  result=result)
    return EnrichAlbumResult(**result, job_id=job_id)


# ── POST /enrich/run ───────────────────────────────────────────────────────
@router.post("/run", response_model=EnrichRunStarted)
def enrich_run(
    req: EnrichRunRequest,
    dry_run: bool = Query(
        default=False,
        description="Resolve VGMDB ids and report what would be enriched "
        "without invoking beet.",
    ),
) -> EnrichRunStarted:
    """Start a bulk-enrichment job. Returns immediately with a job_id."""
    # Pre-size progress_total later — the worker will set it once it's
    # built the plan.
    job_id = db.create_job("enrich_run")
    db.update_job(job_id, status="pending")
    log.info("enrich-run queued (dry_run=%s, artist=%s, album=%s, redo=%s, "
             "redo_skipped=%s, job=%s)",
             dry_run, req.artist, req.album, req.redo,
             req.redo_skipped, job_id)

    thread = threading.Thread(
        target=_run_bulk_in_thread,
        args=(job_id, dry_run, req.artist, req.album, req.redo,
              req.redo_skipped),
        daemon=True,
        name=f"enrich-{job_id[:8]}",
    )
    thread.start()
    return EnrichRunStarted(job_id=job_id)


def _run_bulk_in_thread(
    job_id: str,
    dry_run: bool,
    artist_filter: list[str],
    album_filters: list[str],
    redo: list[str],
    redo_skipped: bool,
) -> None:
    """Bulk run wrapper that records progress + final result in the jobs row."""
    db.update_job(job_id, status="running",
                  append_log=f"started (dry_run={dry_run})")

    def on_progress(current: int, total: int, info: dict) -> None:
        # Set progress_total once and bump current as we go.
        db.update_job(
            job_id,
            progress_current=current,
            progress_total=total,
            append_log=f"[{current}/{total}] {info.get('artist','?')} — "
                       f"{info.get('album','?')}",
        )

    try:
        result = BeetsEnricher().run_bulk(
            dry_run=dry_run,
            artist_filter=artist_filter or None,
            album_filters=album_filters or None,
            redo=redo or None,
            redo_skipped=redo_skipped,
            on_progress=on_progress,
        )
    except Exception as exc:  # noqa: BLE001
        log.exception("enrich_run job %s crashed", job_id)
        db.update_job(job_id, status="failed", append_log=f"crashed: {exc}")
        db.add_activity("enrich_summary", f"bulk crashed: {exc}", level="error")
        return

    status = "failed" if result.get("error") else "success"
    db.update_job(job_id, status=status, result=result,
                  append_log=f"finished: enriched={len(result['enriched'])} "
                             f"skipped={len(result['skipped'])} "
                             f"failed={len(result['failed'])} "
                             f"no_map={len(result['no_map'])}")


# ── GET /enrich/jobs/{job_id} ─────────────────────────────────────────────
@router.get("/jobs/{job_id}", response_model=EnrichJobStatus)
def get_job(
    job_id: str = PathParam(..., description="Bulk-run job id."),
) -> EnrichJobStatus:
    row = db.get_job(job_id)
    if not row:
        raise HTTPException(404, f"no job with id {job_id!r}")
    return EnrichJobStatus(**row)


# ── GET /enrich/log ────────────────────────────────────────────────────────
@router.get("/log", response_model=list[EnrichLogEntry])
def list_log(
    limit: int = Query(default=100, ge=1, le=1000),
    artist: str | None = Query(default=None),
) -> list[EnrichLogEntry]:
    rows = db.list_activity(limit=limit, category="enrich", artist=artist)
    return [EnrichLogEntry(**r) for r in rows]


# ── GET /enrich/export ──────────────────────────────────────────────────────
@router.get("/export")
def export_enriched() -> JSONResponse:
    """Download the full ``enriched_albums.json`` log as a timestamped
    backup file — the set of MB release ids already run through
    enrichment."""
    enriched = EnrichedAlbumsService().export_enriched()
    now = datetime.now(timezone.utc)
    payload = {
        "exported_at": now.isoformat(),
        "count": len(enriched),
        "enriched_albums": enriched,
    }
    db.add_activity("enrich", f"exported {len(enriched)} enriched album id(s)")
    filename = f"enriched_albums-{now:%Y%m%d-%H%M%S}.json"
    return JSONResponse(
        content=payload,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


# ── POST /enrich/import ─────────────────────────────────────────────────────
@router.post("/import", response_model=ImportEnrichedAlbumsResult)
async def import_enriched(
    file: UploadFile = File(
        ...,
        description="A file from GET /enrich/export, or a raw enriched_albums.json.",
    ),
    mode: str = Query(
        default="merge",
        pattern="^(merge|replace)$",
        description="'merge' unions the current log with the imported ids. "
        "'replace' makes the imported file the entire log — anything not "
        "in it is dropped.",
    ),
    dry_run: bool = Query(
        default=False,
        description="Report what would change without writing enriched_albums.json.",
    ),
) -> ImportEnrichedAlbumsResult:
    """Restore (or merge in) the enriched-albums log from a previously
    exported backup."""
    raw = await file.read()
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise HTTPException(400, f"not valid JSON: {exc}") from exc

    if isinstance(data, dict) and isinstance(data.get("enriched_albums"), list):
        incoming = data["enriched_albums"]
    elif isinstance(data, list):
        incoming = data
    else:
        raise HTTPException(
            400,
            "expected a JSON array — either an export from GET "
            "/enrich/export, or a raw enriched_albums.json.",
        )

    try:
        result = EnrichedAlbumsService().import_enriched(incoming, mode=mode, dry_run=dry_run)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc

    if not dry_run:
        db.add_activity(
            "enrich",
            f"imported enriched albums (mode={mode}): +{result['added']} added, "
            f"{result['removed']} removed, {result['skipped_invalid']} skipped",
        )
    return ImportEnrichedAlbumsResult(**result)


# ── helpers ────────────────────────────────────────────────────────────────
def _album_folder_from_tracks(track_paths: list[str]) -> Path | None:
    """Derive the album folder from Lidarr's added-track-paths list.

    Strips any ``Disc N`` parent — multi-disc imports put track files in
    ``.../Album/Disc 1/01.flac`` and we want ``.../Album``.
    """
    paths = [Path(p) for p in track_paths if p]
    if not paths:
        return None
    folder = paths[0].parent
    if folder.name.lower().startswith("disc"):
        folder = folder.parent
    return folder
