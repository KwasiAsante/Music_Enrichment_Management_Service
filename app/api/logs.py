"""``/api/v1/logs`` — unified, filterable view over activity and diagnostic logs.

Activity rows come from the ``activity_log`` table (concise summaries written
by API routers and core modules). Diagnostic rows are parsed from the
per-feature log files under ``{APP_DATA_DIR}/logs/`` — the same verbose
output operators see when tailing ``enrich.log``, ``scan.log``, etc.

``APP_WEB_UI_LOG_LEVEL`` caps how verbose the API returns; the Logs page can
filter further by feature, source, and minimum level.
"""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Query

from app.config import settings
from app.core.log_reader import (
    ACTIVITY_CATEGORY_TO_FEATURE,
    KNOWN_FEATURES,
    activity_feature,
    effective_minimum,
    level_meets_minimum,
    parse_log_timestamp,
    read_diagnostic_entries,
)
from app.models.logs import ActivityLogEntry
from app.storage import db

router = APIRouter(prefix="/api/v1/logs", tags=["logs"])

LogSource = Literal["all", "activity", "diagnostic"]

# Legacy category list — kept for dashboard/enrich pages that still filter by
# activity category. Prefer KNOWN_FEATURES for new UI.
KNOWN_CATEGORIES = list(ACTIVITY_CATEGORY_TO_FEATURE.keys())


def _activity_rows(
    *,
    category: str | None,
    feature: str | None,
    artist: str | None,
    min_level: str,
    limit: int,
) -> list[ActivityLogEntry]:
    rows = db.list_activity(limit=limit * 2, category=category, artist=artist)
    entries: list[ActivityLogEntry] = []
    for row in rows:
        row_feature = activity_feature(row["category"])
        if feature and row_feature != feature:
            continue
        if not level_meets_minimum(row["level"], min_level):
            continue
        entries.append(
            ActivityLogEntry(
                id=row["id"],
                ts=row["ts"],
                category=row["category"],
                level=row["level"],
                artist=row.get("artist"),
                album=row.get("album"),
                message=row["message"],
                source="activity",
                feature=row_feature,
            ),
        )
    return entries


def _diagnostic_rows(
    *,
    feature: str | None,
    min_level: str,
    limit: int,
) -> list[ActivityLogEntry]:
    rows = read_diagnostic_entries(feature=feature, min_level=min_level, limit=limit * 2)
    return [ActivityLogEntry(**row) for row in rows]


def list_logs(
    limit: int = 100,
    category: str | None = None,
    feature: str | None = None,
    level: str | None = None,
    artist: str | None = None,
    source: LogSource = "all",
) -> list[ActivityLogEntry]:
    """Merge, filter, and sort log entries for the web UI."""
    min_level = effective_minimum(level, settings.app_web_ui_log_level)

    # Legacy ``category`` filter maps to a feature when ``feature`` is omitted.
    if category and not feature:
        feature = activity_feature(category)
        category = None if source == "diagnostic" else category

    entries: list[ActivityLogEntry] = []

    if source in ("all", "activity"):
        entries.extend(
            _activity_rows(
                category=category,
                feature=feature,
                artist=artist,
                min_level=min_level,
                limit=limit,
            ),
        )

    if source in ("all", "diagnostic") and not artist:
        entries.extend(
            _diagnostic_rows(feature=feature, min_level=min_level, limit=limit),
        )

    entries.sort(key=lambda e: parse_log_timestamp(e.ts), reverse=True)

    # Deduplicate activity vs diagnostic lines that share the same timestamp
    # and message (rare, but possible for mirrored job progress).
    seen: set[tuple[str, str, str]] = set()
    unique: list[ActivityLogEntry] = []
    for entry in entries:
        key = (entry.ts, entry.feature, entry.message)
        if key in seen:
            continue
        seen.add(key)
        unique.append(entry)

    return unique[:limit]


# ── GET /logs ────────────────────────────────────────────────────────────────
@router.get("/", response_model=list[ActivityLogEntry])
def get_logs(
    limit: int = Query(default=100, ge=1, le=1000),
    category: str | None = Query(
        default=None,
        description="Legacy activity category filter (prefer ``feature``).",
    ),
    feature: str | None = Query(
        default=None,
        description="Feature key, e.g. 'enrich', 'scan', 'mapping'.",
    ),
    level: str | None = Query(
        default=None,
        description="Minimum level — 'debug', 'info', 'warning', or 'error'.",
    ),
    artist: str | None = Query(default=None, description="Exact match, activity only."),
    source: LogSource = Query(
        default="all",
        description="'activity', 'diagnostic', or 'all' (default).",
    ),
) -> list[ActivityLogEntry]:
    return list_logs(
        limit=limit,
        category=category,
        feature=feature,
        level=level,
        artist=artist,
        source=source,
    )
