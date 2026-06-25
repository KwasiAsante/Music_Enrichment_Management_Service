"""APScheduler wiring for the two periodic jobs.

Two crontab-driven jobs, both configured via .env:

* ``SCAN_CRON``   → :class:`LibraryScanner.scan` (default ``0 2 * * 0`` —
                    Sunday 2am, matching the original host crontab).
* ``ENRICH_CRON`` → :class:`BeetsEnricher.run_bulk` (default ``0 3 * * 0``
                    — Sunday 3am).

Set either to an empty string in ``.env`` to disable that job.

Both fires:

* create a row in the ``jobs`` table tagged ``scan_scheduled`` /
  ``enrich_run_scheduled`` so they show up in the history feed
  distinguishably from API-triggered runs;
* append a one-line summary to the activity log when finished.

The scheduler runs in-process (``BackgroundScheduler``) — we don't need
a separate worker process for two cron jobs and an occasional manual
trigger. ``start()`` / ``stop()`` are called from the FastAPI lifespan.
"""

from __future__ import annotations

import logging
from typing import Optional

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

from app.config import settings
from app.core.beets_enricher import BeetsEnricher
from app.core.library_scanner import LibraryScanner
from app.storage import db

log = logging.getLogger("music-lib-helper.scheduler")

# Module-level handle. ``None`` when not running. Tests can read this to
# assert what got registered.
_scheduler: Optional[BackgroundScheduler] = None


# ── job bodies (also reachable directly from tests) ────────────────────────
def run_scheduled_scan() -> dict:
    """Fire :class:`LibraryScanner.scan` and record a job row."""
    job_id = db.create_job("scan_scheduled")
    db.update_job(job_id, status="running")
    log.info("scheduled scan starting (job=%s)", job_id)
    try:
        result = LibraryScanner().scan(dry_run=False, cleanup=False)
    except Exception as exc:  # noqa: BLE001
        log.exception("scheduled scan job %s crashed", job_id)
        db.update_job(job_id, status="failed", append_log=str(exc))
        db.add_activity("scan_summary", f"scheduled scan crashed: {exc}",
                        level="error")
        raise
    db.update_job(job_id, status="success", result=result,
                  progress_current=result["total"],
                  progress_total=result["total"])
    db.add_activity(
        "scan_summary",
        f"scheduled scan: {result['total']} albums, "
        f"{result['new']} new, {result['without_mb_id']} without MB id",
        level="warning" if result["errors"] else "info",
    )
    return result


def run_scheduled_enrich() -> dict:
    """Fire :class:`BeetsEnricher.run_bulk` and record a job row."""
    job_id = db.create_job("enrich_run_scheduled")
    db.update_job(job_id, status="running")
    log.info("scheduled enrich starting (job=%s)", job_id)
    try:
        result = BeetsEnricher().run_bulk(dry_run=False)
    except Exception as exc:  # noqa: BLE001
        log.exception("scheduled enrich job %s crashed", job_id)
        db.update_job(job_id, status="failed", append_log=str(exc))
        db.add_activity("enrich_summary", f"scheduled enrich crashed: {exc}",
                        level="error")
        raise
    status = "failed" if result.get("error") else "success"
    db.update_job(job_id, status=status, result=result,
                  progress_current=result["total"],
                  progress_total=result["total"])
    return result


# ── lifecycle ──────────────────────────────────────────────────────────────
def start() -> Optional[BackgroundScheduler]:
    """Start the in-process scheduler and register both cron jobs.

    Returns the scheduler instance (or ``None`` if both crons are empty —
    nothing to schedule, no scheduler started).
    """
    global _scheduler
    if _scheduler is not None and _scheduler.running:
        log.debug("scheduler already running")
        return _scheduler

    scan_cron = (settings.scan_cron or "").strip()
    enrich_cron = (settings.enrich_cron or "").strip()
    if not scan_cron and not enrich_cron:
        log.info("scheduler disabled: SCAN_CRON and ENRICH_CRON both empty")
        return None

    sched = BackgroundScheduler(timezone=settings.tz or "UTC")

    if scan_cron:
        sched.add_job(
            run_scheduled_scan,
            CronTrigger.from_crontab(scan_cron, timezone=settings.tz or "UTC"),
            id="scheduled_scan",
            name="Sunday library scan",
            misfire_grace_time=3600,
            coalesce=True,
            max_instances=1,
            replace_existing=True,
        )
        log.info("scheduled scan: %s (%s)", scan_cron, settings.tz or "UTC")

    if enrich_cron:
        sched.add_job(
            run_scheduled_enrich,
            CronTrigger.from_crontab(enrich_cron, timezone=settings.tz or "UTC"),
            id="scheduled_enrich",
            name="Sunday bulk enrichment",
            misfire_grace_time=3600,
            coalesce=True,
            max_instances=1,
            replace_existing=True,
        )
        log.info("scheduled enrich: %s (%s)", enrich_cron, settings.tz or "UTC")

    sched.start()
    _scheduler = sched
    return sched


def stop(wait: bool = False) -> None:
    """Shut the scheduler down. ``wait=False`` returns immediately even if
    a fire is in flight — matches FastAPI's graceful-shutdown contract.
    """
    global _scheduler
    if _scheduler is None:
        return
    try:
        _scheduler.shutdown(wait=wait)
    finally:
        _scheduler = None
    log.info("scheduler stopped")


def get() -> Optional[BackgroundScheduler]:
    """Test/debug accessor for the running scheduler instance."""
    return _scheduler
