"""Pydantic models for the ``/api/v1/artist/*`` endpoints.

Three request/response shapes:

* ``FixPathRequest`` / ``FixOneResult`` — body and response for
  ``POST /fix-path``, called by the Lidarr ``OnArtistAdd`` thin wrapper.
  Both ``fix-path`` and the bulk endpoint take ``dry_run`` as a query
  parameter (``?dry_run=true``), not a body field.
* ``FixAllResult`` — response for ``POST /fix-all-paths``, the bulk
  endpoint.
* ``ArtistPathSuggestion`` — entries returned by ``GET /paths``.
"""

from __future__ import annotations

from pydantic import BaseModel, Field


# ── POST /artist/fix-path ──────────────────────────────────────────────────
class FixPathRequest(BaseModel):
    """Body for ``POST /artist/fix-path``. Field names match the lower-cased
    keys the Lidarr custom-script wrapper sends."""

    artist_id: int | str = Field(description="Lidarr internal artist id.")
    mb_id: str = Field(description="MusicBrainz artist id (foreignArtistId).")
    artist_name: str
    artist_path: str = Field(description="The path Lidarr currently has for the artist.")
    event_type: str | None = Field(
        default=None,
        description="The Lidarr event type. Only 'ArtistAdd' triggers work; "
        "'Test' and empty are no-ops (returned as ok).",
    )


class FixOneResult(BaseModel):
    """Response from ``POST /artist/fix-path``."""

    ok: bool
    new_path: str | None = Field(
        default=None,
        description="The path Lidarr was updated to. None if no change was made.",
    )
    strategy: str | None = Field(
        default=None,
        description="Which strategy resolved the path: 'mapped_track', "
        "'mb_id_map', 'mb_alias_rename', 'skip_already_latin', "
        "'rename_failed', 'none', or 'event_ignored'.",
    )
    mb_alias: str | None = Field(
        default=None,
        description="The English alias used, when strategy is 'mb_alias_rename'.",
    )
    message: str
    discord_sent: bool = False
    dry_run: bool = False


# ── POST /artist/fix-all-paths ─────────────────────────────────────────────
class FixedRow(BaseModel):
    """One row in the ``fixed`` / ``would_fix`` arrays of FixAllResult."""

    artist_name: str
    old_path: str
    new_path: str
    strategy: str
    mb_alias: str | None = None


class NotFoundRow(BaseModel):
    """One row in the ``not_found`` array of FixAllResult."""

    artist_name: str
    reason: str


class FixAllResult(BaseModel):
    """Response from ``POST /artist/fix-all-paths``."""

    dry_run: bool
    fixed: list[FixedRow] = Field(default_factory=list)
    would_fix: list[FixedRow] = Field(default_factory=list)
    not_found: list[NotFoundRow] = Field(default_factory=list)
    unchanged: list[str] = Field(default_factory=list)
    error: str | None = Field(
        default=None,
        description="Top-level error (e.g. could not reach Lidarr). Per-artist "
        "failures go in not_found instead.",
    )
    job_id: str | None = None


# ── GET /artist/paths ──────────────────────────────────────────────────────
class ArtistPathSuggestion(BaseModel):
    """One entry in the response from ``GET /artist/paths``."""

    artist_id: int | str | None = None
    artist_name: str
    current_path: str
    suggested_path: str | None = None
    mb_alias: str | None = None
    strategy: str | None = None
