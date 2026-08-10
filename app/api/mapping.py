"""``/api/v1/mapping/*`` — VGMDB mapping list, search, set, delete,
export/import, and the two "purposely excluded" lists (skipped albums,
excluded artists).

* ``GET    /``                        — list every entry in
                                        ``vgmdb_mapping.json`` (optionally
                                        filtered by artist).
* ``GET    /unmapped``                — list album_list entries with no
                                        mapping.
* ``GET    /skipped``                 — mappings explicitly marked
                                        ``vgmdb_id == "skip"``.
* ``GET    /excluded-artists``        — artists excluded from "unmapped"
                                        and bulk enrichment.
* ``POST   /excluded-artists``        — add an artist to that list.
* ``DELETE /excluded-artists/{name}`` — remove an artist from that list.
* ``POST   /search``                  — run the four-step VGMDB search
                                        pipeline for one album.
* ``PUT    /{mb_release_id}``         — set / update a single mapping
                                        (``vgmdb_id="skip"`` marks it skipped).
* ``DELETE /{mb_release_id}``         — remove a mapping (un-skips it too).
* ``GET    /export``                  — download the whole mapping as a
                                        backup file.
* ``POST   /import``                  — restore/merge mappings from a
                                        backup file.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone

from fastapi import APIRouter, File, HTTPException, Path, Query, UploadFile
from fastapi.responses import JSONResponse

from app.core.vgmdb_mapper import VGMDBMapper
from app.models.mapping import (
    DeleteMappingResult,
    ExcludedArtistRequest,
    ExcludedArtistResult,
    ImportMappingsResult,
    MappingEntry,
    SearchRequest,
    SearchResult,
    SetMappingRequest,
    UnmappedEntry,
)
from app.storage import db

log = logging.getLogger("music-lib-helper.api.mapping")

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


# ── GET /mapping/skipped ────────────────────────────────────────────────────
@router.get("/skipped", response_model=list[MappingEntry])
def list_skipped(
    artist: str | None = Query(
        default=None,
        description="Case-insensitive substring match on the artist field.",
    ),
) -> list[MappingEntry]:
    """Mappings explicitly marked ``vgmdb_id == "skip"`` — albums confirmed
    to have no VGMDB presence. Restore one with ``DELETE /mapping/{id}``,
    which puts it back into the unmapped pool."""
    rows = VGMDBMapper().list_skipped(artist_filter=artist)
    return [MappingEntry(**r) for r in rows]


# ── GET /mapping/excluded-artists ───────────────────────────────────────────
@router.get("/excluded-artists", response_model=list[str])
def list_excluded_artists() -> list[str]:
    """Artists purposely excluded from "unmapped" listings and bulk
    enrichment (Western acts with no VGMDB presence, etc.)."""
    return VGMDBMapper().list_excluded_artists()


# ── POST /mapping/excluded-artists ──────────────────────────────────────────
@router.post("/excluded-artists", response_model=ExcludedArtistResult)
def add_excluded_artist(req: ExcludedArtistRequest) -> ExcludedArtistResult:
    """Add an artist to the exclusion list."""
    try:
        changed = VGMDBMapper().add_excluded_artist(req.artist)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    if changed:
        db.add_activity("mapping", f"excluded artist added: {req.artist}",
                        artist=req.artist)
    return ExcludedArtistResult(artist=req.artist, changed=changed)


# ── DELETE /mapping/excluded-artists/{artist} ───────────────────────────────
@router.delete("/excluded-artists/{artist}", response_model=ExcludedArtistResult)
def remove_excluded_artist(
    artist: str = Path(..., description="Exact artist name to un-exclude."),
) -> ExcludedArtistResult:
    """Remove an artist from the exclusion list — their albums will show
    up in "unmapped" (and be eligible for bulk enrichment) again."""
    changed = VGMDBMapper().remove_excluded_artist(artist)
    if changed:
        db.add_activity("mapping", f"excluded artist removed: {artist}",
                        artist=artist)
    return ExcludedArtistResult(artist=artist, changed=changed)


# ── POST /mapping/search ───────────────────────────────────────────────────
@router.post("/search", response_model=SearchResult)
def search(
    req: SearchRequest,
    dry_run: bool = Query(
        default=False,
        description="Accepted for consistency with the other write "
        "endpoints. Search has no side effects to suppress (it never "
        "writes vgmdb_mapping.json), so this has no effect on the result.",
    ),
) -> SearchResult:
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
    dry_run: bool = Query(
        default=False,
        description="Compute and return the entry without writing "
        "vgmdb_mapping.json.",
    ),
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
            dry_run=dry_run,
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
@router.delete("/{mb_release_id:path}", response_model=DeleteMappingResult)
def delete_mapping(
    mb_release_id: str = Path(..., description="MusicBrainz release id."),
    dry_run: bool = Query(
        default=False,
        description="Report whether the entry exists (and would be "
        "removed) without writing vgmdb_mapping.json.",
    ),
) -> DeleteMappingResult:
    """Remove a mapping entry."""
    ok = VGMDBMapper().delete_mapping(mb_release_id, dry_run=dry_run)
    if ok and not dry_run:
        db.add_activity("mapping", f"deleted mapping {mb_release_id}")
    return DeleteMappingResult(deleted=ok, dry_run=dry_run)


# ── GET /mapping/export ─────────────────────────────────────────────────────
@router.get("/export")
def export_mappings() -> JSONResponse:
    """Download the full ``vgmdb_mapping.json`` as a timestamped backup file.

    The response body wraps the raw mapping with a bit of metadata
    (``exported_at``, ``count``); ``POST /mapping/import`` accepts that
    wrapped shape *or* a bare ``vgmdb_mapping.json`` (e.g. one copied
    straight off the data volume), so either can be restored later.
    """
    mapping = VGMDBMapper().export_mappings()
    now = datetime.now(timezone.utc)
    payload = {
        "exported_at": now.isoformat(),
        "count": len(mapping),
        "mappings": mapping,
    }
    db.add_activity("mapping", f"exported {len(mapping)} mapping(s)")
    filename = f"vgmdb_mapping-{now:%Y%m%d-%H%M%S}.json"
    return JSONResponse(
        content=payload,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


# ── POST /mapping/import ────────────────────────────────────────────────────
@router.post("/import", response_model=ImportMappingsResult)
async def import_mappings(
    file: UploadFile = File(
        ...,
        description="A file from GET /mapping/export, or a raw vgmdb_mapping.json.",
    ),
    mode: str = Query(
        default="merge",
        pattern="^(merge|replace)$",
        description="'merge' adds/overwrites on top of the current mapping. "
        "'replace' makes the imported file the entire mapping — anything "
        "not in it is dropped.",
    ),
    dry_run: bool = Query(
        default=False,
        description="Report what would change without writing "
        "vgmdb_mapping.json.",
    ),
) -> ImportMappingsResult:
    """Restore (or merge in) mappings from a previously exported backup."""
    raw = await file.read()
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise HTTPException(400, f"not valid JSON: {exc}") from exc

    if isinstance(data, dict) and isinstance(data.get("mappings"), dict):
        incoming = data["mappings"]
    elif isinstance(data, dict):
        incoming = data
    else:
        raise HTTPException(
            400,
            "expected a JSON object — either an export from GET "
            "/mapping/export, or a raw vgmdb_mapping.json.",
        )

    try:
        result = VGMDBMapper().import_mappings(incoming, mode=mode, dry_run=dry_run)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc

    if not dry_run:
        db.add_activity(
            "mapping",
            f"imported mappings (mode={mode}): +{result['added']} added, "
            f"{result['updated']} updated, {result['removed']} removed, "
            f"{result['skipped_invalid']} skipped",
        )
    return ImportMappingsResult(**result)
