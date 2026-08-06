"""Pydantic model for ``GET /api/v1/logs``.

Structurally identical to :class:`app.models.enrich.EnrichLogEntry` (both
are just ``activity_log`` rows), kept as a separate model rather than
reused because the two endpoints serve different concerns — this one is
generic across every category, that one is enrich-specific — and letting
them drift independently is safer than one page's changes silently
affecting the other's schema.
"""

from __future__ import annotations

from pydantic import BaseModel


class ActivityLogEntry(BaseModel):
    """One row from the ``activity_log`` table, any category."""

    id: int
    ts: str
    category: str
    level: str = "info"
    artist: str | None = None
    album: str | None = None
    message: str
