"""``/`` and friends — the Phase 2 web UI.

Six page routes, all ``GET``, all rendering server-side templates via the
shared :data:`app.ui.templates.templates` instance, and all behind HTTP
Basic Auth (see :func:`app.ui.auth.require_login`, applied once via
``dependencies=`` on the router below rather than per-route). No prefix —
these are top-level paths so they read naturally in a browser
(``/mappings``, not ``/ui/mappings``).

* ``GET /``          — Dashboard. Real data: :func:`library_stats` +
                        recent activity from the ``activity_log`` table.
* ``GET /mappings``  — Real data: :meth:`VGMDBMapper.list_unmapped`,
                        :meth:`VGMDBMapper.list_skipped`, and
                        :meth:`VGMDBMapper.list_excluded_artists`. Search/
                        Set/Skip/Restore/Exclude all wired live via
                        ``static/js/mappings.js``.
* ``GET /enrich``    — Real data: recent ``enrich``-category activity.
                        Run form + live job polling via
                        ``static/js/enrich.js``.
* ``GET /library``   — Real data: :func:`app.api.library.list_albums`,
                        :func:`app.api.library.list_albums_grouped` (group-
                        by-artist view), or :func:`app.api.library.list_skipped`
                        (``?view=skipped``), reused directly so the page and
                        the API never define filtering logic twice.
                        ``?view=`` (all/unmapped/enriched/skipped),
                        ``?artist=``, ``?folder=``, and ``?source=`` drive
                        the server-rendered initial state, so links from the
                        Dashboard's stat cards land pre-filtered rather than
                        flashing unfiltered content. Layout (list/grid) and
                        group-by-artist are client-only preferences (saved
                        to localStorage) applied after load; further
                        filtering/pagination live via ``static/js/library.js``.
* ``GET /library/album`` — One album's full detail view. Real data:
                        :func:`app.api.library.album_detail` (tags from the
                        files themselves, supplemented by VGMDB/MusicBrainz).
                        Server-rendered — no client-side fetch, since the
                        whole page is just one album's static-for-now data.
* ``GET /logs``      — Real data: :func:`app.api.logs.list_logs` (first
                        100 rows, unfiltered). Filtering lives via
                        ``static/js/logs.js``.
* ``GET /music-search`` — The MusicBrainz → Lidarr → Prowlarr/Nyaa →
                        qBittorrent SPA, now a real template extending
                        ``base.html`` instead of a standalone static file
                        served by ``app.api.proxy``. Only non-secret
                        context (``qbit_save_path``) is passed in — the
                        page no longer carries any API keys or passwords;
                        see ``app.api.proxy`` for where those moved.
* ``GET /help``      — Static documentation page, no data. Per-page
                        dismissible tip banners (``templates/_tip_banner.html``,
                        ``static/js/tips.js``) link into this page's anchors.

Each handler passes ``request`` in the template context — required by
Jinja2Templates, and also what ``base.html`` uses to compute which sidebar
nav item gets the ``active`` class.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import HTMLResponse

from app.api import library as library_api
from app.api import logs as logs_api
from app.config import settings
from app.core.vgmdb_mapper import VGMDBMapper
from app.storage import db
from app.storage.json_store import store
from app.ui.auth import require_login
from app.ui.templates import templates

log = logging.getLogger("music-lib-helper.ui")

router = APIRouter(tags=["ui"], dependencies=[Depends(require_login)])


# ── shared helpers ───────────────────────────────────────────────────────────
def _has_vgmdb_association(mb_release_id: str | None, mapping: dict) -> bool:
    """Same rule the library API uses — kept local to avoid a cross-router
    import for one four-line function. If this drifts, promote it to a
    shared module."""
    if not mb_release_id:
        return False
    if mb_release_id in mapping:
        return True
    return mb_release_id.startswith("vgmdb-")


def _library_stats() -> dict:
    """Same computation as ``GET /api/v1/library/stats``, called directly
    rather than over HTTP since the UI and the API share one process."""
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

    return {
        "total": total,
        "enriched": enriched_count,
        "unmapped": unmapped_count,
        "skipped": len(skipped),
        "with_mb_id": with_mb,
        "without_mb_id": total - with_mb,
    }


# ── GET / ────────────────────────────────────────────────────────────────────
@router.get("/", response_class=HTMLResponse)
def dashboard(request: Request) -> HTMLResponse:
    stats = _library_stats()
    activity = db.list_activity(limit=15)
    return templates.TemplateResponse(
        request,
        "dashboard.html",
        {"stats": stats, "activity": activity, "categories": logs_api.KNOWN_CATEGORIES},
    )


# ── GET /mappings ────────────────────────────────────────────────────────────
@router.get("/mappings", response_class=HTMLResponse)
def mappings(request: Request) -> HTMLResponse:
    mapper = VGMDBMapper()
    unmapped = mapper.list_unmapped(artist_filter=None, skip_western=True)
    skipped = mapper.list_skipped()
    excluded_artists = mapper.list_excluded_artists()
    return templates.TemplateResponse(
        request,
        "mappings.html",
        {
            "unmapped": unmapped,
            "skipped": skipped,
            "excluded_artists": excluded_artists,
        },
    )


# ── GET /enrich ──────────────────────────────────────────────────────────────
@router.get("/enrich", response_class=HTMLResponse)
def enrich_page(request: Request) -> HTMLResponse:
    activity = db.list_activity(limit=20, category="enrich")
    return templates.TemplateResponse(
        request,
        "enrich.html",
        {"activity": activity},
    )


# ── GET /library ─────────────────────────────────────────────────────────────
@router.get("/library", response_class=HTMLResponse)
def library_page(
    request: Request,
    view: str = Query(default="all"),
    artist: str | None = Query(default=None),
    folder: str | None = Query(default=None),
    source: str | None = Query(default=None),
) -> HTMLResponse:
    if view == "skipped":
        skipped = library_api.list_skipped()
        return templates.TemplateResponse(
            request,
            "library.html",
            {
                "view": view, "artist_q": artist or "", "folder_q": folder or "",
                "source_q": source or "", "skipped": skipped,
            },
        )

    page = library_api.list_albums(
        artist=artist,
        folder=folder,
        unmapped=(view == "unmapped"),
        enriched=(view == "enriched"),
        source=source,
        page=1,
        limit=50,
    )
    return templates.TemplateResponse(
        request,
        "library.html",
        {
            "view": view, "artist_q": artist or "", "folder_q": folder or "",
            "source_q": source or "", "page": page,
        },
    )


# ── GET /library/album ──────────────────────────────────────────────────────
@router.get("/library/album", response_class=HTMLResponse)
def album_detail_page(request: Request, folder: str = Query(...)) -> HTMLResponse:
    """One album's detail view. Reuses
    :func:`app.api.library.album_detail` directly — a 404 there (unknown
    folder) just renders the page in a "not found" state rather than a
    hard error, since the most likely cause is a stale link (the folder
    got rescanned away) rather than anything the person did wrong.
    """
    try:
        detail = library_api.album_detail(folder=folder)
    except HTTPException:
        detail = None
    return templates.TemplateResponse(
        request,
        "album_detail.html",
        {"folder": folder, "detail": detail},
    )


# ── GET /logs ────────────────────────────────────────────────────────────────
@router.get("/logs", response_class=HTMLResponse)
def logs_page(request: Request) -> HTMLResponse:
    rows = logs_api.list_logs(limit=100, category=None, level=None, artist=None)
    return templates.TemplateResponse(
        request,
        "logs.html",
        {"rows": rows, "categories": logs_api.KNOWN_CATEGORIES},
    )


# ── GET /music-search ──────────────────────────────────────────────────────
@router.get("/music-search", response_class=HTMLResponse)
def music_search_page(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(
        request,
        "music_search.html",
        {"qbit_save_path": settings.qbit_save_path},
    )


# ── GET /help ────────────────────────────────────────────────────────────────
@router.get("/help", response_class=HTMLResponse)
def help_page(request: Request) -> HTMLResponse:
    """Static documentation — no data to fetch, just the template."""
    return templates.TemplateResponse(request, "help.html", {})
