"""Pydantic models for the ``/api/v1/library/*`` endpoints.

These are the request/response schemas FastAPI uses for validation and
for the auto-generated docs at ``/docs``. They mirror the shapes the
:class:`app.core.library_scanner.LibraryScanner` and the JSON storage
layer already produce — the router does the small amount of glue.
"""

from __future__ import annotations

from pydantic import BaseModel, Field


# ── POST /library/scan ──────────────────────────────────────────────────────
class ScanRequest(BaseModel):
    """Body for a scan request. ``dry_run`` is a query parameter, not a body
    field — see ``?dry_run=true``. ``cleanup`` defaults off — a plain ``{}``
    does a normal, non-destructive scan."""

    cleanup: bool = Field(
        default=False,
        description="Also run the (non-interactive) cleanup pass that removes "
        "empty / audio-less folders. Off by default — destructive.",
    )


class CleanupResult(BaseModel):
    """Outcome of the optional cleanup pass."""

    album_folders_removed: int
    artist_folders_removed: int
    removed: list[str] = Field(
        default_factory=list,
        description="Paths (relative to the artist root) that were removed.",
    )
    errors: list[str] = Field(
        default_factory=list,
        description="Per-folder failures, e.g. a read-only mount or a locked file.",
    )


class NewAlbum(BaseModel):
    """An album seen for the first time since the previous scan."""

    artist: str
    album: str


class ScanResult(BaseModel):
    """Response from ``POST /library/scan``."""

    dry_run: bool
    total: int = Field(description="Total albums found in the library.")
    new: int = Field(description="Albums not present in the previous album_list.")
    with_mb_id: int
    without_mb_id: int
    new_albums: list[NewAlbum] = Field(default_factory=list)
    cleanup: CleanupResult | None = Field(
        default=None,
        description="Present only when cleanup=true was requested.",
    )
    errors: list[str] = Field(default_factory=list)
    job_id: str | None = Field(
        default=None,
        description="Id of the row recorded in the jobs table for this run.",
    )


# ── GET /library/albums ─────────────────────────────────────────────────────
class AlbumEntry(BaseModel):
    """One album from ``album_list.json``, plus derived ``mapped``/``enriched``
    flags."""

    folder_name: str = Field(description="The album-list key (the album folder name).")
    artist: str
    album: str
    mb_release_id: str | None = None
    folder: str = Field(description="Path relative to the artist root.")
    mapped: bool = Field(
        description="True if this album has a VGMDB association — either an "
        "entry in vgmdb_mapping.json or an mb_release_id that is itself a "
        "'vgmdb-' id.",
    )
    enriched: bool = Field(
        description="True if this album's mb_release_id is in the enriched log.",
    )
    mapping_source: str | None = Field(
        default=None,
        description="The 'source' field from vgmdb_mapping.json when mapped "
        "(e.g. 'manual', 'import', 'mb_url_rel', 'search_catalog', "
        "'search_barcode', 'search_title'). None when unmapped.",
    )


class AlbumsPage(BaseModel):
    """Paginated response from ``GET /library/albums``."""

    total: int = Field(description="Total albums matching the filters (pre-pagination).")
    page: int
    limit: int
    albums: list[AlbumEntry]


# ── GET /library/albums/grouped ─────────────────────────────────────────────
class ArtistGroup(BaseModel):
    """All matching albums for one artist, for the grouped-by-artist view."""

    artist: str
    count: int
    albums: list[AlbumEntry]


class GroupedAlbumsPage(BaseModel):
    """Response from ``GET /library/albums/grouped``.

    Unlike ``AlbumsPage`` this isn't paginated — grouping only makes sense
    over the whole filtered set, so every matching album is fetched and
    bucketed by artist server-side. ``total`` reflects every match; if
    that exceeds the internal cap, ``truncated`` is set and only the first
    ``total_artists`` artists (alphabetically) are actually included in
    ``groups`` — narrow the filters (artist/folder/source) to see the rest.
    """

    total: int = Field(description="Total albums matching the filters, before any cap.")
    total_artists: int = Field(description="Number of artist groups actually returned.")
    truncated: bool = Field(
        description="True if the match count exceeded the cap and the "
        "result was cut off.",
    )
    groups: list[ArtistGroup]


# ── GET /library/skipped ────────────────────────────────────────────────────
class SkippedEntry(BaseModel):
    """One entry from ``skipped_albums.json`` — an album the beets enrichment
    pass found a VGMDB candidate for, but whose match confidence fell below
    threshold, so it was left un-tagged rather than guessed at. Distinct from
    ``mapped=False`` ("no VGMDB candidate was ever found") — a skipped album
    usually *does* have a mapping, just one beets wasn't confident enough to
    act on automatically."""

    mb_release_id: str = Field(description="The skipped_albums.json key.")
    vgmdb_id: str
    folder: str
    artist: str
    album: str
    match_percentage: str
    threshold: str
    skipped_reason: str


# ── GET /library/stats ──────────────────────────────────────────────────────
class LibraryStats(BaseModel):
    """Response from ``GET /library/stats`` — counts for the dashboard."""

    total: int
    enriched: int = Field(description="Albums whose mb_release_id is in the enriched log.")
    unmapped: int = Field(description="Albums with no VGMDB mapping and not a 'vgmdb-' id.")
    skipped: int = Field(description="Entries in skipped_albums.json.")
    with_mb_id: int
    without_mb_id: int
