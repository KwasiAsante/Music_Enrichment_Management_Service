"""Pydantic models for ``GET /api/v1/library/albums/detail``."""

from __future__ import annotations

from pydantic import BaseModel, Field


class TrackDetail(BaseModel):
    """One track, either read straight from a file's tags or (only when
    no audio files were readable at all) filled in from VGMDB/MusicBrainz
    as a tracklist-shaped fallback."""

    disc: int = Field(default=1, description="Disc/medium number, 1-based.")
    track: int = Field(default=0, description="Track number within its disc.")
    title: str
    duration_seconds: float | None = None
    composer: str | None = None


class AlbumArt(BaseModel):
    """Which sides of the cover are actually available — the frontend
    requests each from ``GET /library/art?side=`` only when true, rather
    than requesting both and eating a guaranteed 404 for albums (most of
    them) that don't have a back cover."""

    front: bool
    back: bool


class AlbumDetail(BaseModel):
    """Full detail view for one album — tag data from the files
    themselves, supplemented by VGMDB and/or MusicBrainz for anything
    the tags don't carry (extra credit roles, community tags, a
    description) when the album is mapped to either.
    """

    folder: str = Field(description="Path relative to the artist root — the album's stable identifier.")
    artist: str
    album: str
    mb_release_id: str | None = None
    vgmdb_id: str | None = None
    mapped: bool
    enriched: bool

    year: str | None = None
    label: str | None = None
    catalog: str | None = None
    media_format: str | None = None

    genres: list[str] = Field(default_factory=list)
    composers: list[str] = Field(default_factory=list)
    performers: list[str] = Field(default_factory=list)
    arrangers: list[str] = Field(default_factory=list)
    lyricists: list[str] = Field(default_factory=list)
    description: str | None = Field(
        default=None,
        description="Free-text notes/description, when VGMDB has any.",
    )

    disc_count: int = 1
    track_count: int = 0
    total_duration_seconds: float | None = None
    tracks: list[TrackDetail] = Field(default_factory=list)

    art: AlbumArt

    sources: list[str] = Field(
        default_factory=list,
        description="Which data sources actually contributed: any of "
        "'tags', 'vgmdb', 'musicbrainz'.",
    )
    warnings: list[str] = Field(
        default_factory=list,
        description="Non-fatal notes about missing/failed data, e.g. "
        "'VGMDB lookup failed' — shown in the UI as a small aside, never "
        "blocks rendering the rest of the page.",
    )
