"""``/api/v1/mapping/*`` — VGMDB mapping list, search, set, delete.

Five endpoints:

* ``GET    /``                — list every entry in ``vgmdb_mapping.json``
                                (optionally filtered by artist).
* ``GET    /unmapped``        — list album_list entries with no mapping.
* ``POST   /search``          — run the four-step VGMDB search pipeline
                                for one album. Used by the Phase 2 Web UI's
                                Mappings page and by ad-hoc CLI workflows.
* ``PUT    /{mb_release_id}`` — set / update a single mapping.
* ``DELETE /{mb_release_id}`` — remove a mapping (clears it for re-mapping).
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException, Path, Query

from app.core.vgmdb_mapper import VGMDBMapper
from app.models.mapping import (
    MappingEntry,
    SearchRequest,
    SearchResult,
    SetMappingRequest,
    UnmappedEntry,
)
from app.storage import db

log = logging.getLogger("lidarr-helper.api.mapping")

router = APIRouter(prefix="/api/v1/mapping", tags=["mapping"])


# ── GET /mapping ───────────────────────────────────────────────────────────
@router.get("", response_model=list[MappingEntry])
def list_mappings(
    artist: str | None = Query(
        default=None,
        description="Case-insensitive substring match on the artist field.",
    ),
) -> list[MappingEntry]:
    """Every mapping in ``vgmdb_mapping.json``, sorted by artist + album."""
    rows = VGMDBMapper().list_mappings(artist_filter=artist)
    return [MappingEntry(**r) for r in rows]


# ── GET /mapping/unmapped ──────────────────────────────────────────────────
@router.get("/unmapped", response_model=list[UnmappedEntry])
def list_unmapped(
    artist: str | None = Query(default=None),
    include_western: bool = Query(
        default=False,
        description="Include artists from the built-in Western-acts skip "
        "list (off by default to match the original script).",
    ),
) -> list[UnmappedEntry]:
    """Albums with no VGMDB mapping. Skips Western artists by default."""
    rows = VGMDBMapper().list_unmapped(
        artist_filter=artist,
        skip_western=not include_western,
    )
    return [UnmappedEntry(**r) for r in rows]


# ── POST /mapping/search ───────────────────────────────────────────────────
@router.post("/search", response_model=SearchResult)
def search(req: SearchRequest) -> SearchResult:
    """Run the four-step VGMDB search pipeline for one album.

    The original interactive ``generate-mappings-template.py`` ran this
    same pipeline inline. Exposing it as an endpoint lets the future Web
    UI present each step's hints in one shot.
    """
    mapper = VGMDBMapper()
    try:
        result = mapper.search(
            mb_release_id=req.mb_release_id,
            album=req.album,
            artist=req.artist,
            folder=req.folder,
        )
    except Exception as exc:  # noqa: BLE001
        log.exception("mapping search failed for %s", req.album)
        raise HTTPException(500, f"search failed: {exc}") from exc

    db.add_activity(
        "mapping",
        f"search: {req.artist} — {req.album}"
        + (f" → auto vgmdb:{result['mb_vgmdb_id']}" if result["mb_vgmdb_id"] else "")
        + (f" → suggested vgmdb:{result['suggested']['vgmdb_id']}"
           if result["suggested"] else ""),
        artist=req.artist or None,
        album=req.album,
    )
    return SearchResult(**result)


# ── PUT /mapping/{mb_release_id} ──────────────────────────────────────────
@router.put("/{mb_release_id:path}", response_model=MappingEntry)
def set_mapping(
    req: SetMappingRequest,
    mb_release_id: str = Path(..., description="MusicBrainz release id (or 'vgmdb-<id>')."),
) -> MappingEntry:
    """Insert or update one mapping entry."""
    try:
        row = VGMDBMapper().set_mapping(
            mb_release_id=mb_release_id,
            vgmdb_id=req.vgmdb_id,
            artist=req.artist,
            album=req.album,
            folder=req.folder,
            source=req.source,
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc

    db.add_activity(
        "mapping",
        f"set vgmdb:{req.vgmdb_id} for {row['artist']} — {row['album']} "
        f"(source={req.source})",
        artist=row.get("artist") or None,
        album=row.get("album"),
    )
    return MappingEntry(**row)


# ── DELETE /mapping/{mb_release_id} ────────────────────────────────────────
@router.delete("/{mb_release_id:path}")
def delete_mapping(
    mb_release_id: str = Path(..., description="MusicBrainz release id."),
) -> dict[str, bool]:
    """Remove a mapping entry. Returns ``{"deleted": true|false}``."""
    ok = VGMDBMapper().delete_mapping(mb_release_id)
    if ok:
        db.add_activity("mapping", f"deleted mapping {mb_release_id}")
    return {"deleted": ok}
