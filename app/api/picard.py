"""``/api/v1/picard/*`` — Picard Post-Tagging Action endpoints.

* ``POST /export``      — called by the Picard wrapper after every album
                          tag save. Refreshes one artist's entry.
* ``POST /export/full`` — full-library refresh. Useful for first-time
                          setup and as a scheduled rescue.

Both write ``artists_mbids.json`` in the data volume and (if a GitHub
token is configured) sync to a private Gist.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException

from app.core.picard_export import PicardExporter
from app.models.picard import ExportAllResult, ExportOneRequest, ExportOneResult
from app.storage import db

log = logging.getLogger("lidarr-helper.api.picard")

router = APIRouter(prefix="/api/v1/picard", tags=["picard"])


# ── POST /picard/export ────────────────────────────────────────────────────
@router.post("/export", response_model=ExportOneResult)
def export_one(req: ExportOneRequest) -> ExportOneResult:
    """Re-export a single artist's MB album-artist id."""
    job_id = db.create_job("picard_export")
    db.update_job(job_id, status="running")
    log.info("picard export requested for %r (job=%s)",
             req.artist_folder, job_id)

    try:
        result = PicardExporter().export_one(req.artist_folder)
    except Exception as exc:  # noqa: BLE001
        log.exception("picard export job %s failed", job_id)
        db.update_job(job_id, status="failed", append_log=str(exc))
        db.add_activity("picard", f"export failed: {exc}", level="error")
        raise HTTPException(500, f"export failed: {exc}") from exc

    db.update_job(job_id, status="success" if result["ok"] else "failed",
                  result=result)
    db.add_activity(
        "picard",
        result["message"]
        + (f" (gist updated)" if result["gist_updated"] else ""),
        level="info" if result["ok"] else "warning",
        artist=result.get("artist"),
    )
    return ExportOneResult(**result)


# ── POST /picard/export/full ───────────────────────────────────────────────
@router.post("/export/full", response_model=ExportAllResult)
def export_all() -> ExportAllResult:
    """Walk the entire artist root and rebuild artists_mbids.json."""
    job_id = db.create_job("picard_export_full")
    db.update_job(job_id, status="running")
    log.info("picard full export requested (job=%s)", job_id)

    try:
        result = PicardExporter().export_all()
    except Exception as exc:  # noqa: BLE001
        log.exception("picard full export job %s failed", job_id)
        db.update_job(job_id, status="failed", append_log=str(exc))
        db.add_activity("picard", f"full export failed: {exc}", level="error")
        raise HTTPException(500, f"full export failed: {exc}") from exc

    db.update_job(job_id, status="success" if result["ok"] else "failed",
                  result=result,
                  progress_current=result["processed"],
                  progress_total=result["processed"])
    db.add_activity(
        "picard",
        f"full export: {result['processed']} processed, "
        f"{result['new']} new, {result['skipped']} skipped"
        + (f" (gist updated)" if result["gist_updated"] else ""),
        level="warning" if result["errors"] else "info",
    )
    return ExportAllResult(**result)
