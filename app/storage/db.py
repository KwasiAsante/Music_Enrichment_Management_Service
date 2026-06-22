"""SQLite layer for job history and the activity log.

The JSON files in :mod:`app.storage.json_store` hold *domain state*
(mappings, the album list, caches). SQLite holds *operational history*:

* ``jobs``         — every scan / enrichment / path-fix run, its status
                     and progress, used to back the ``/enrich/jobs/{id}``
                     polling endpoint.
* ``activity_log`` — a chronological feed of notable events, used to back
                     the Logs page in the Phase 2 web UI.

Synchronous ``sqlite3`` is used deliberately. FastAPI runs sync route
handlers in a threadpool, and APScheduler jobs run in their own threads,
so a connection-per-call pattern is both simple and safe here. If write
contention ever becomes a real problem this can move to ``aiosqlite``
without changing any of the public functions below.
"""

from __future__ import annotations

import json
import logging
import sqlite3
import uuid
from datetime import datetime, timezone
from typing import Any, Optional

from app.config import settings

log = logging.getLogger("lidarr-helper.db")

# Valid job lifecycle states. Kept as a module constant so callers and
# tests share one definition.
JOB_STATUSES = ("pending", "running", "success", "failed")


# ── connection helpers ──────────────────────────────────────────────────────
def _connect() -> sqlite3.Connection:
    """Open a connection with sensible defaults.

    * ``row_factory`` set to ``sqlite3.Row`` so callers get dict-like rows.
    * WAL mode + ``foreign_keys`` on for concurrent reads during writes.
    """
    settings.app_data_dir.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(settings.db_path, timeout=15.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def _utcnow() -> str:
    """Current UTC time as an ISO-8601 string (second precision)."""
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# ── schema ──────────────────────────────────────────────────────────────────
_SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
    id               TEXT PRIMARY KEY,
    type             TEXT NOT NULL,
    status           TEXT NOT NULL DEFAULT 'pending',
    created_at       TEXT NOT NULL,
    updated_at       TEXT NOT NULL,
    progress_current INTEGER NOT NULL DEFAULT 0,
    progress_total   INTEGER NOT NULL DEFAULT 0,
    result           TEXT,
    log              TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS activity_log (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    ts        TEXT NOT NULL,
    category  TEXT NOT NULL,
    level     TEXT NOT NULL DEFAULT 'info',
    artist    TEXT,
    album     TEXT,
    message   TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_jobs_type_created
    ON jobs (type, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_activity_ts
    ON activity_log (ts DESC);
CREATE INDEX IF NOT EXISTS idx_activity_artist
    ON activity_log (artist);
"""


def init_db() -> None:
    """Create tables and indexes if they don't exist. Safe to call repeatedly."""
    with _connect() as conn:
        conn.executescript(_SCHEMA)
    log.info("sqlite ready at %s", settings.db_path)


# ── jobs ────────────────────────────────────────────────────────────────────
def create_job(job_type: str, progress_total: int = 0) -> str:
    """Insert a new ``pending`` job and return its generated id."""
    job_id = uuid.uuid4().hex
    now = _utcnow()
    with _connect() as conn:
        conn.execute(
            "INSERT INTO jobs (id, type, status, created_at, updated_at, "
            "progress_total) VALUES (?, ?, 'pending', ?, ?, ?)",
            (job_id, job_type, now, now, progress_total),
        )
    return job_id


def update_job(
    job_id: str,
    *,
    status: Optional[str] = None,
    progress_current: Optional[int] = None,
    progress_total: Optional[int] = None,
    result: Optional[Any] = None,
    append_log: Optional[str] = None,
) -> None:
    """Update mutable fields of a job.

    Only the arguments you pass are touched. ``result`` is JSON-encoded.
    ``append_log`` is appended (newline-terminated) to the existing log
    rather than replacing it.
    """
    if status is not None and status not in JOB_STATUSES:
        raise ValueError(f"invalid job status: {status!r}")

    sets: list[str] = ["updated_at = ?"]
    params: list[Any] = [_utcnow()]

    if status is not None:
        sets.append("status = ?")
        params.append(status)
    if progress_current is not None:
        sets.append("progress_current = ?")
        params.append(progress_current)
    if progress_total is not None:
        sets.append("progress_total = ?")
        params.append(progress_total)
    if result is not None:
        sets.append("result = ?")
        params.append(json.dumps(result, ensure_ascii=False))
    if append_log is not None:
        sets.append("log = log || ?")
        params.append(append_log if append_log.endswith("\n") else append_log + "\n")

    params.append(job_id)
    with _connect() as conn:
        conn.execute(f"UPDATE jobs SET {', '.join(sets)} WHERE id = ?", params)


def get_job(job_id: str) -> Optional[dict]:
    """Return a job as a dict (``result`` decoded from JSON), or ``None``."""
    with _connect() as conn:
        row = conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
    if row is None:
        return None
    return _job_row_to_dict(row)


def list_jobs(limit: int = 50, job_type: Optional[str] = None) -> list[dict]:
    """Return recent jobs, newest first, optionally filtered by type."""
    query = "SELECT * FROM jobs"
    params: list[Any] = []
    if job_type:
        query += " WHERE type = ?"
        params.append(job_type)
    query += " ORDER BY created_at DESC LIMIT ?"
    params.append(limit)
    with _connect() as conn:
        rows = conn.execute(query, params).fetchall()
    return [_job_row_to_dict(r) for r in rows]


def _job_row_to_dict(row: sqlite3.Row) -> dict:
    d = dict(row)
    if d.get("result"):
        try:
            d["result"] = json.loads(d["result"])
        except json.JSONDecodeError:
            pass  # leave the raw string if it somehow isn't valid JSON
    return d


# ── activity log ────────────────────────────────────────────────────────────
def add_activity(
    category: str,
    message: str,
    *,
    level: str = "info",
    artist: Optional[str] = None,
    album: Optional[str] = None,
) -> None:
    """Append one entry to the activity log."""
    with _connect() as conn:
        conn.execute(
            "INSERT INTO activity_log (ts, category, level, artist, album, message) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (_utcnow(), category, level, artist, album, message),
        )


def list_activity(
    limit: int = 100,
    category: Optional[str] = None,
    artist: Optional[str] = None,
) -> list[dict]:
    """Return recent activity-log entries, newest first, with optional filters."""
    query = "SELECT * FROM activity_log"
    where: list[str] = []
    params: list[Any] = []
    if category:
        where.append("category = ?")
        params.append(category)
    if artist:
        where.append("artist = ?")
        params.append(artist)
    if where:
        query += " WHERE " + " AND ".join(where)
    query += " ORDER BY ts DESC, id DESC LIMIT ?"
    params.append(limit)
    with _connect() as conn:
        rows = conn.execute(query, params).fetchall()
    return [dict(r) for r in rows]
