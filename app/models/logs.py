"""Pydantic models for ``GET /api/v1/logs``.

The endpoint merges SQLite ``activity_log`` rows (user-facing summaries)
with parsed feature log files (diagnostic detail). Both share this schema.
"""

from __future__ import annotations

from pydantic import BaseModel


class ActivityLogEntry(BaseModel):
    """One log line from either ``activity_log`` or a feature log file."""

    id: int | None = None
    ts: str
    category: str
    level: str = "info"
    artist: str | None = None
    album: str | None = None
    message: str
    source: str = "activity"
    feature: str = ""
