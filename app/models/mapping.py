"""Pydantic models for the ``/api/v1/mapping/*`` endpoints."""

from __future__ import annotations

from pydantic import BaseModel, Field


# ── shared ─────────────────────────────────────────────────────────────────
class VGMDBHint(BaseModel):
    """One search hit from the VGMDB API, normalised."""
    vgmdb_id: str
    title:    str
    catalog:  str = ""
    barcode:  str = ""
    date:     str = ""


# ── GET /mapping ───────────────────────────────────────────────────────────
class MappingEntry(BaseModel):
    """One row from ``vgmdb_mapping.json`` with its key exposed."""
    mb_release_id: str
    vgmdb_id:      str = Field(description="VGMDB album id, or the literal 'skip'.")
    folder:        str = ""
    artist:        str = ""
    album:         str = ""
    source:        str = Field(
        default="manual",
        description="How the mapping was created: 'manual', 'mb_url_rel', "
        "'catalog', 'barcode', 'title', 'cli'.",
    )
    dry_run:       bool = False


# ── GET /mapping/unmapped ──────────────────────────────────────────────────
class UnmappedEntry(BaseModel):
    """An album_list row that has no VGMDB association."""
    folder_name:   str
    artist:        str
    album:         str
    mb_release_id: str | None = None
    folder:        str = ""


# ── POST /mapping/search ───────────────────────────────────────────────────
class SearchRequest(BaseModel):
    """Body for ``POST /mapping/search``.

    Any field may be empty — the search pipeline degrades gracefully:
    no ``mb_release_id`` skips the MB-URL-rel and catalog-from-MB steps;
    no ``folder`` skips the tag-reading step; ``album`` is what we fall
    back to for the title search.
    """
    mb_release_id: str | None = None
    album:         str
    artist:        str = ""
    folder:        str | None = Field(
        default=None,
        description="Folder name relative to artist root (used to read "
        "catalog from on-disk audio tags).",
    )


class SearchResult(BaseModel):
    """Response from ``POST /mapping/search``."""
    mb_vgmdb_id:   str | None = Field(
        default=None,
        description="Set when the MB release already has a VGMDB URL relationship.",
    )
    mb_catalog:    str | None = None
    mb_barcode:    str | None = None
    catalog_hints: list[VGMDBHint] = Field(default_factory=list)
    barcode_hints: list[VGMDBHint] = Field(default_factory=list)
    title_hints:   list[VGMDBHint] = Field(default_factory=list)
    suggested:     VGMDBHint | None = None


# ── PUT /mapping/{mb_release_id} ──────────────────────────────────────────
class SetMappingRequest(BaseModel):
    """Body for ``PUT /mapping/{mb_release_id}``."""
    vgmdb_id: str = Field(description="VGMDB album id, or the literal 'skip'.")
    artist:   str = ""
    album:    str = ""
    folder:   str = ""
    source:   str = "manual"


# ── DELETE /mapping/{mb_release_id} ───────────────────────────────────────
class DeleteMappingResult(BaseModel):
    """Response from ``DELETE /mapping/{mb_release_id}``."""
    deleted: bool = Field(
        description="True if the entry existed (and was removed, unless "
        "dry_run was set).",
    )
    dry_run: bool = False


# ── POST /mapping/bulk-skip ─────────────────────────────────────────────────
class BulkSkipItem(BaseModel):
    """One album to skip — matches the fields an Unmapped/Library row
    already carries client-side, so the frontend can build this straight
    from what's already rendered without another round trip."""
    mb_release_id: str
    artist: str = ""
    album: str = ""
    folder: str = ""


class BulkSkipRequest(BaseModel):
    """Body for ``POST /mapping/bulk-skip``."""
    items: list[BulkSkipItem] = Field(default_factory=list)


class BulkSkipResult(BaseModel):
    """Response from ``POST /mapping/bulk-skip``."""
    skipped: list[str] = Field(description="mb_release_ids successfully marked as skipped.")
    failed: list[dict] = Field(
        default_factory=list,
        description="Items that couldn't be processed: [{mb_release_id, error}].",
    )


# ── POST /mapping/bulk-exclude-artists ──────────────────────────────────────
class BulkExcludeArtistsRequest(BaseModel):
    """Body for ``POST /mapping/bulk-exclude-artists``. Shared by the
    Mappings and Library pages' bulk-select toolbars."""
    artists: list[str] = Field(default_factory=list)


class BulkExcludeArtistsResult(BaseModel):
    """Response from ``POST /mapping/bulk-exclude-artists``."""
    added: list[str] = Field(description="Artists newly added to the exclusion list.")
    already_excluded: list[str] = Field(
        default_factory=list,
        description="Artists that were already on the list — no-ops, not errors.",
    )


# ── excluded-artists CRUD ───────────────────────────────────────────────────
class ExcludedArtistRequest(BaseModel):
    """Body for ``POST /mapping/excluded-artists``."""
    artist: str = Field(description="Exact artist name to exclude.")


class ExcludedArtistResult(BaseModel):
    """Response from ``POST``/``DELETE /mapping/excluded-artists``."""
    artist:  str
    changed: bool = Field(
        description="True if the list actually changed — false for adding "
        "a duplicate or removing an artist that wasn't present.",
    )


# ── POST /mapping/import ───────────────────────────────────────────────────
class ImportMappingsResult(BaseModel):
    """Response from ``POST /mapping/import``."""
    mode:            str = Field(description="'merge' or 'replace'.")
    added:           int = Field(description="Entries not previously present.")
    updated:         int = Field(description="Existing entries whose values changed.")
    unchanged:       int = Field(description="Existing entries the import matched exactly.")
    removed:         int = Field(
        default=0,
        description="Entries dropped because mode='replace' and they "
        "weren't in the imported file. Always 0 for mode='merge'.",
    )
    skipped_invalid: int = Field(
        default=0,
        description="Rows in the imported file that were missing a "
        "vgmdb_id (or weren't objects) and were ignored.",
    )
    total_after:     int = Field(description="Total mapping count after the import.")
    dry_run:         bool = False
