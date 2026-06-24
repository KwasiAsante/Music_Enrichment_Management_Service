"""Pydantic models for the ``/api/v1/enrich/*`` endpoints."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


# ── POST /enrich/album ─────────────────────────────────────────────────────
class EnrichAlbumRequest(BaseModel):
    """Body for ``POST /enrich/album``. Field names follow the lower-cased
    keys Lidarr's OnAlbumDownload custom-script forwards through the slim
    wrapper.
    """
    artist_name: str = ""
    album_title: str = ""
    mb_release_id: str = ""
    track_paths: list[str] = Field(
        default_factory=list,
        description="Absolute paths Lidarr just added. The first one's "
        "parent (peeled past any 'Disc N' subfolder) becomes the album "
        "folder for the beet import.",
    )
    event_type: str | None = Field(
        default=None,
        description="Lidarr event type. Only 'AlbumDownload' triggers "
        "actual work; 'Test' and empty are accepted no-ops.",
    )


class EnrichAlbumResult(BaseModel):
    """Response from ``POST /enrich/album``."""
    ok: bool
    artist: str = ""
    album: str = ""
    mb_release_id: str = ""
    vgmdb_id: str | None = None
    source: str | None = Field(
        default=None,
        description="Resolution path: 'mapping', 'mb_release_id_prefix', "
        "'mb_url_rel', 'search_catalog', 'search_barcode', 'search_title', "
        "'enriched_log', 'skipped', 'skip_sentinel', 'event_ignored', "
        "'folder_not_found', 'not_found'.",
    )
    match: str | None = None
    already_enriched: bool = False
    tags_fixed: int = 0
    seed_category: str | None = Field(
        default=None,
        description="Discord category the MB seed URL was posted to, if any.",
    )
    message: str = ""
    job_id: str | None = None
    dry_run: bool = False


# ── POST /enrich/run ───────────────────────────────────────────────────────
class EnrichRunRequest(BaseModel):
    """Body for ``POST /enrich/run`` — bulk enrichment. ``dry_run`` is a
    query parameter, not a body field — see ``?dry_run=true``."""
    artist: list[str] = Field(
        default_factory=list,
        description="Artist folder names (case-insensitive). Empty = all.",
    )
    album: list[str] = Field(
        default_factory=list,
        description="Album substring filters. Empty = all.",
    )
    redo: list[str] = Field(
        default_factory=list,
        description="Album substrings to clear from the enriched log so "
        "they re-process.",
    )
    redo_skipped: bool = Field(
        default=False,
        description="Pull every eligible entry out of skipped_albums.json "
        "and re-attempt it (entries with vgmdb_id='skip' are left alone).",
    )


class EnrichRunStarted(BaseModel):
    """Immediate response from ``POST /enrich/run`` — the work happens in
    a background thread, the caller polls ``GET /enrich/jobs/{job_id}``.
    """
    job_id: str
    message: str = "bulk enrichment started in background"


# ── GET /enrich/jobs/{job_id} ─────────────────────────────────────────────
class EnrichJobStatus(BaseModel):
    """Job row from the SQLite ``jobs`` table, decoded."""
    id: str
    type: str
    status: str
    created_at: str
    updated_at: str
    progress_current: int = 0
    progress_total: int = 0
    log: str = ""
    result: dict[str, Any] | None = None


# ── GET /enrich/log ───────────────────────────────────────────────────────
class EnrichLogEntry(BaseModel):
    """One row from the activity_log filtered to category='enrich'."""
    id: int
    ts: str
    category: str
    level: str = "info"
    artist: str | None = None
    album: str | None = None
    message: str
