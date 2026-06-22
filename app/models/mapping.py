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
