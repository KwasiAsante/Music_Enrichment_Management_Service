"""``/api/v1/library/*`` — library scan, browse, and stats.

These endpoints are the API surface over :class:`LibraryScanner` and the
JSON storage layer:

* ``POST /scan``    — rebuild album_list.json (optionally with cleanup),
                      recording the run in the jobs table + activity log.
* ``GET  /albums``  — browse album_list.json, paginated, with artist,
                      unmapped, and enriched filters.
* ``GET  /skipped`` — browse skipped_albums.json (low-confidence VGMDB
                      matches beets declined to auto-tag).
* ``GET  /stats``   — dashboard counts.

The scan endpoint is a *synchronous* ``def`` on purpose: FastAPI runs
sync handlers in a threadpool, so the (blocking, IO-heavy) scan doesn't
stall the event loop, and the caller still gets the result directly —
which matches the original ``scan-library.py`` call pattern used by
``on_artist_add.py``. Bulk enrichment, which is genuinely long-running,
is the thing that becomes a background job (see the enrich router).
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException, Query

from app.core.library_scanner import LibraryScanner
from app.models.library import (
    AlbumEntry,
    AlbumsPage,
    LibraryStats,
    ScanRequest,
    ScanResult,
    SkippedEntry,
)
from app.storage import db
from app.storage.json_store import store

log = logging.getLogger("music-lib-helper.api.library")

router = APIRouter(prefix="/api/v1/library", tags=["library"])


def _has_vgmdb_association(mb_release_id: str | None, mapping: dict) -> bool:
    """Mirror of map-vgmdb.py's unmapped logic: an album counts as mapped if
    it has an entry in vgmdb_mapping.json *or* its mb_release_id is itself a
    'vgmdb-' id (an album that came straight from VGMDB)."""
    if not mb_release_id:
        return False
    if mb_release_id in mapping:
        return True
    return mb_release_id.startswith("vgmdb-")


# ── POST /library/scan ──────────────────────────────────────────────────────
@router.post("/scan", response_model=ScanResult)
def scan_library(
    req: ScanRequest,
    dry_run: bool = Query(
        default=False,
        description="Report what would change without writing "
        "album_list.json or deleting anything.",
    ),
) -> ScanResult:
    """Rebuild ``album_list.json`` from the music library on disk.

    Records the run in the ``jobs`` table (so it shows up in history even
    though the result is returned synchronously) and writes one
    activity-log line.
    """
    job_id = db.create_job("scan")
    db.update_job(job_id, status="running")
    log.info("scan requested (dry_run=%s, cleanup=%s) job=%s",
             dry_run, req.cleanup, job_id)

    scanner = LibraryScanner()
    try:
        result = scanner.scan(dry_run=dry_run, cleanup=req.cleanup)
    except Exception as exc:  # noqa: BLE001 — surface any failure as 500 + job record
        log.exception("scan job %s failed", job_id)
        db.update_job(job_id, status="failed", append_log=str(exc))
        db.add_activity("scan", f"scan failed: {exc}", level="error")
        raise HTTPException(status_code=500, detail=f"scan failed: {exc}") from exc

    db.update_job(
        job_id,
        status="success",
        progress_current=result["total"],
        progress_total=result["total"],
        result=result,
    )
    summary = (
        f"scanned library: {result['total']} albums, {result['new']} new, "
        f"{result['without_mb_id']} without MB id"
        + (" [dry run]" if result["dry_run"] else "")
    )
    db.add_activity("scan", summary, level="warning" if result["errors"] else "info")

    return ScanResult(**result, job_id=job_id)


# ── GET /library/albums ─────────────────────────────────────────────────────
@router.get("/albums", response_model=AlbumsPage)
def list_albums(
    artist: str | None = Query(
        default=None,
        description="Case-insensitive substring match on the artist name.",
    ),
    unmapped: bool = Query(
        default=False,
        description="Return only albums with no VGMDB association.",
    ),
    enriched: bool = Query(
        default=False,
        description="Return only albums whose mb_release_id is in the enriched log.",
    ),
    page: int = Query(default=1, ge=1),
    limit: int = Query(default=50, ge=1, le=500),
) -> AlbumsPage:
    """Browse ``album_list.json`` with optional filters and pagination.

    ``unmapped`` and ``enriched`` can combine (though in practice an
    enriched album is never unmapped) — each is applied independently,
    same as ``artist``.
    """
    album_list = store.album_list.read()
    mapping = store.vgmdb_mapping.read()
    enriched_set = store.enriched_set()

    artist_q = artist.lower() if artist else None

    entries: list[AlbumEntry] = []
    for folder_name, info in album_list.items():
        mb_id = info.get("mb_release_id")
        mapped = _has_vgmdb_association(mb_id, mapping)
        is_enriched = bool(mb_id and mb_id in enriched_set)

        if artist_q and artist_q not in info.get("artist", "").lower():
            continue
        if unmapped and mapped:
            continue
        if enriched and not is_enriched:
            continue

        entries.append(
            AlbumEntry(
                folder_name=folder_name,
                artist=info.get("artist", ""),
                album=info.get("album", ""),
                mb_release_id=mb_id,
                folder=info.get("folder", ""),
                mapped=mapped,
                enriched=is_enriched,
            )
        )

    # Stable ordering: artist, then album.
    entries.sort(key=lambda e: (e.artist.lower(), e.album.lower()))

    total = len(entries)
    start = (page - 1) * limit
    page_items = entries[start : start + limit]

    return AlbumsPage(total=total, page=page, limit=limit, albums=page_items)


# ── GET /library/skipped ─────────────────────────────────────────────────────
@router.get("/skipped", response_model=list[SkippedEntry])
def list_skipped() -> list[SkippedEntry]:
    """Every entry in ``skipped_albums.json`` — albums beets found a VGMDB
    candidate for but wasn't confident enough in to tag automatically.

    Distinct from ``GET /albums?unmapped=true``: unmapped means no VGMDB
    candidate was ever found; skipped means one was found but rejected on
    confidence. See :class:`app.models.library.SkippedEntry`.
    """
    skipped = store.skipped_albums.read()
    return [
        SkippedEntry(mb_release_id=mb_id, **entry)
        for mb_id, entry in sorted(skipped.items(), key=lambda kv: kv[1].get("artist", "").lower())
    ]


# ── GET /library/stats ──────────────────────────────────────────────────────
@router.get("/stats", response_model=LibraryStats)
def library_stats() -> LibraryStats:
    """Dashboard counts derived from album_list, vgmdb_mapping, the enriched
    log, and skipped_albums."""
    album_list = store.album_list.read()
    mapping = store.vgmdb_mapping.read()
    enriched = store.enriched_set()
    skipped = store.skipped_albums.read()

    total = len(album_list)
    with_mb = 0
    enriched_count = 0
    unmapped_count = 0

    for info in album_list.values():
        mb_id = info.get("mb_release_id")
        if mb_id:
            with_mb += 1
            if mb_id in enriched:
                enriched_count += 1
        if not _has_vgmdb_association(mb_id, mapping):
            unmapped_count += 1

    return LibraryStats(
        total=total,
        enriched=enriched_count,
        unmapped=unmapped_count,
        skipped=len(skipped),
        with_mb_id=with_mb,
        without_mb_id=total - with_mb,
    )
