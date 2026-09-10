"""Pydantic models for ``/api/v1/tags/*``."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class TagFieldView(BaseModel):
    """One tag field's current state on an album.

    ``source`` is purely a display grouping (see
    :func:`app.core.tag_fields.classify_tag_source`) — every group is
    equally editable and lockable through this same API.
    """

    name: str
    value: str | None = None
    locked: bool = False
    source: Literal["standard", "musicbrainz", "vgmdb", "custom"] = Field(
        description="'standard' (the built-in field set), 'musicbrainz' "
        "or 'vgmdb' (an identifier/credit tag recognized by name), or "
        "'custom' (anything else found on the files).",
    )


class TrackLockState(BaseModel):
    """Whether track titles / track-level artist tags are protected from
    enrichment. Applies uniformly to every track in the album — each
    track still keeps its own value, this doesn't force one shared value
    like a field lock does (see ``TagFieldView.locked``)."""

    title: bool = False
    artist: bool = False


class AlbumTagsView(BaseModel):
    """Full tag-editor view for one album."""

    folder: str
    artist: str
    album: str
    fields: list[TagFieldView] = Field(default_factory=list)
    track_locks: TrackLockState = Field(default_factory=TrackLockState)
    warnings: list[str] = Field(default_factory=list)


class UpdateAlbumTagsRequest(BaseModel):
    """Body for ``PUT /api/v1/tags/album``. A field mapped to ``""``
    deletes that tag rather than writing an empty value. A field name
    not already on the album creates a new custom tag."""

    fields: dict[str, str] = Field(default_factory=dict)


class UpdateAlbumTagsResult(BaseModel):
    folder: str
    files_touched: int
    tags: AlbumTagsView


class SetLockRequest(BaseModel):
    field: str
    locked: bool


class SetLockResult(BaseModel):
    folder: str
    locked_fields: list[str]


class SetTrackLockRequest(BaseModel):
    field: str = Field(description="'title' or 'artist'.")
    locked: bool


class SetTrackLockResult(BaseModel):
    folder: str
    track_locks: TrackLockState


# ── POST /tags/locks/import ─────────────────────────────────────────────────
class ImportLockedFieldsResult(BaseModel):
    """Response from ``POST /tags/locks/import``."""
    mode:            str = Field(description="'merge' or 'replace'.")
    added:           int = Field(description="Entries not previously present.")
    updated:         int = Field(description="Existing entries whose field list changed.")
    unchanged:       int = Field(description="Existing entries the import matched exactly.")
    removed:         int = Field(
        default=0,
        description="Entries dropped because mode='replace' and they "
        "weren't in the imported file. Always 0 for mode='merge'.",
    )
    skipped_invalid: int = Field(
        default=0,
        description="Rows in the imported file that weren't a list of "
        "field names, or had none left after dropping per-track-only fields.",
    )
    total_after:     int = Field(description="Total locked-folder count after the import.")
    dry_run:         bool = False
