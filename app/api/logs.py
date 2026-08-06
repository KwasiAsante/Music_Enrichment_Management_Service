"""``/api/v1/logs`` — generic, filterable view over the ``activity_log``
table.

Every other router writes to ``activity_log`` (``scan``, ``mapping``,
``enrich``, ``artist_fix``, ``picard``, ...) but only the ``enrich`` router
exposed a read endpoint, and only for its own category. This router is the
general-purpose equivalent the Logs page (Phase 2) needs to browse
everything in one place.
"""

from __future__ import annotations

from fastapi import APIRouter, Query

from app.models.logs import ActivityLogEntry
from app.storage import db

router = APIRouter(prefix="/api/v1/logs", tags=["logs"])

# Every category any router actually writes today. Not enforced server-side
# (an unrecognized category filter just returns zero rows, not a 400) — kept
# here so the UI's filter dropdown has a source of truth instead of a
# hand-typed list that can drift from the real routers.
KNOWN_CATEGORIES = [
    "scan",
    "scan_summary",
    "mapping",
    "enrich",
    "enrich_summary",
    "artist_fix",
    "picard",
]


# ── GET /logs ────────────────────────────────────────────────────────────────
@router.get("/", response_model=list[ActivityLogEntry])
def list_logs(
    limit: int = Query(default=100, ge=1, le=1000),
    category: str | None = Query(default=None, description="e.g. 'enrich', 'scan', 'mapping'."),
    level: str | None = Query(default=None, description="'info', 'warning', or 'error'."),
    artist: str | None = Query(default=None, description="Exact match, not substring."),
) -> list[ActivityLogEntry]:
    rows = db.list_activity(limit=limit, category=category, artist=artist, level=level)
    return [ActivityLogEntry(**r) for r in rows]
