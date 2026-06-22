"""Pydantic models for the ``/api/v1/mb/*`` endpoints."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class VGMDBLinkResult(BaseModel):
    """Response from ``GET /api/v1/mb/vgmdb-link/{mb_release_id}``."""

    mb_release_id: str
    has_link:      bool = Field(
        description="True if the MB release already has any vgmdb.net / "
        "vgmdb.info URL relationship.",
    )
    vgmdb_url:     str | None = Field(
        default=None,
        description="The VGMDB album URL (the existing one if has_link is "
        "true, the proposed one if a vgmdb_id query param was supplied).",
    )
    existing_url:  str | None = Field(
        default=None,
        description="The exact URL MB has on file, if any.",
    )
    seed_url:      str | None = Field(
        default=None,
        description="MusicBrainz release-editor URL that pre-fills the "
        "VGMDB link. None if MB already has the link or if no vgmdb_id "
        "was supplied.",
    )


class ArtistAliasResult(BaseModel):
    """Response from ``GET /api/v1/mb/artist-alias/{mb_artist_id}``."""

    mb_artist_id: str
    english_name: str | None = Field(
        default=None,
        description="The artist's preferred English name (primary alias > "
        "any English Artist-name alias > romanised sort-name).",
    )
    aliases: list[dict[str, Any]] = Field(
        default_factory=list,
        description="The raw aliases array from the MB API. Empty if the "
        "artist isn't cached and MB couldn't be reached.",
    )
