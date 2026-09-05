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


class AlbumTagsView(BaseModel):
    """Full tag-editor view for one album."""

    folder: str
    artist: str
    album: str
    fields: list[TagFieldView] = Field(default_factory=list)
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
