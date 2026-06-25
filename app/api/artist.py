"""``/api/v1/artist/*`` — single-artist fix, bulk fix, and suggestions.

Three endpoints:

* ``POST /fix-path``       — called by Lidarr's OnArtistAdd thin wrapper
                             (``lidarr-scripts/on_artist_add.py``).
                             Single artist. Supports ``?dry_run=true``.
* ``POST /fix-all-paths``  — bulk fix across every non-Latin artist.
                             Supports ``?dry_run=true``.
* ``GET  /paths``          — read-only list of non-Latin artists with
                             the path each would be renamed to.

All endpoints share one :class:`ArtistFixer` instance per request so the
mb-id-to-folder scan is computed once per call rather than per artist.

The Lidarr-driven endpoint accepts (and is no-ops on) Lidarr's "Test"
events — the original ``on_artist_add.py`` did the same so the user can
press "Test" in Lidarr's Connect UI without spurious work happening.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException, Query

from app.core.artist_fixer import ArtistFixer
from app.models.artist import (
    ArtistPathSuggestion,
    FixAllResult,
    FixOneResult,
    FixPathRequest,
)
from app.storage import db

log = logging.getLogger("music-lib-helper.api.artist")

router = APIRouter(prefix="/api/v1/artist", tags=["artist"])


# ── POST /artist/fix-path ───────────────────────────────────────────────────
@router.post("/fix-path", response_model=FixOneResult)
def fix_path(
    req: FixPathRequest,
    dry_run: bool = Query(
        default=False,
        description="Resolve the correct path without updating Lidarr, "
        "renaming any folder, or sending a Discord notification.",
    ),
) -> FixOneResult:
    """Fix one artist's path. Called by Lidarr's OnArtistAdd custom script."""

    # Treat Lidarr's "Test" pings and empty event types as a no-op success,
    # so the Connect UI's Test button doesn't produce noise. Anything other
    # than ArtistAdd is also ignored — matches the original wrapper.
    et = (req.event_type or "").strip()
    if et in ("", "Test"):
        return FixOneResult(
            ok=True, strategy="event_ignored",
            message=f"event_type={et or '(empty)'} — ignored",
        )
    if et != "ArtistAdd":
        return FixOneResult(
            ok=True, strategy="event_ignored",
            message=f"event_type={et} is not ArtistAdd — ignored",
        )

    job_id = db.create_job("fix_path")
    db.update_job(job_id, status="running")
    log.info("fix-path requested for %s (job=%s)", req.artist_name, job_id)

    fixer = ArtistFixer()
    try:
        result = fixer.fix_one(
            artist_id=req.artist_id,
            mb_id=req.mb_id,
            artist_name=req.artist_name,
            artist_path=req.artist_path,
            dry_run=dry_run,
        )
    except Exception as exc:  # noqa: BLE001
        log.exception("fix-path job %s failed", job_id)
        db.update_job(job_id, status="failed", append_log=str(exc))
        db.add_activity("artist_fix", f"fix-path failed for {req.artist_name}: {exc}",
                        level="error", artist=req.artist_name)
        raise HTTPException(500, f"fix-path failed: {exc}") from exc

    db.update_job(job_id, status="success" if result["ok"] else "failed", result=result)
    db.add_activity(
        "artist_fix",
        result["message"] + (f" → {result['new_path']}" if result["new_path"] else ""),
        level="info" if result["ok"] else "error",
        artist=req.artist_name,
    )
    return FixOneResult(**result)


# ── POST /artist/fix-all-paths ──────────────────────────────────────────────
@router.post("/fix-all-paths", response_model=FixAllResult)
def fix_all_paths(
    dry_run: bool = Query(
        default=False,
        description="Report what would change without updating Lidarr "
        "or renaming any folders.",
    ),
) -> FixAllResult:
    """Bulk fix all artists with non-Latin folder paths."""
    job_id = db.create_job("fix_all_paths")
    db.update_job(job_id, status="running")
    log.info("fix-all requested (dry_run=%s, job=%s)", dry_run, job_id)

    fixer = ArtistFixer()
    try:
        result = fixer.fix_all(dry_run=dry_run)
    except Exception as exc:  # noqa: BLE001
        log.exception("fix-all job %s failed", job_id)
        db.update_job(job_id, status="failed", append_log=str(exc))
        db.add_activity("artist_fix", f"fix-all failed: {exc}", level="error")
        raise HTTPException(500, f"fix-all failed: {exc}") from exc

    status = "failed" if result.get("error") else "success"
    db.update_job(job_id, status=status, result=result)
    summary = (
        f"fix-all: {len(result['fixed'])} fixed, "
        f"{len(result['would_fix'])} would_fix, "
        f"{len(result['not_found'])} not_found, "
        f"{len(result['unchanged'])} unchanged"
        + (" [dry run]" if result["dry_run"] else "")
    )
    db.add_activity("artist_fix", summary,
                    level="error" if result.get("error") else "info")

    return FixAllResult(**result, job_id=job_id)


# ── GET /artist/paths ───────────────────────────────────────────────────────
@router.get("/paths", response_model=list[ArtistPathSuggestion])
def list_suggestions() -> list[ArtistPathSuggestion]:
    """List every artist with a non-Latin path plus the suggested fix.

    Read-only: never updates Lidarr, never renames folders on disk.
    """
    fixer = ArtistFixer()
    rows = fixer.suggest_all()
    return [ArtistPathSuggestion(**r) for r in rows]
