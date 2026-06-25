"""Reverse proxy + music-search.html host.

Three things, all on the same FastAPI app so we keep the "single
port, single container" promise from the plan:

* ``/proxy/prowlarr/{path}`` → forwards to ``PROWLARR_URL``
* ``/proxy/lidarr/{path}``   → forwards to ``LIDARR_URL``
* ``/proxy/qbit/{path}``     → forwards to ``QBIT_URL``

The proxy passes through cookies (so qBittorrent's ``SID`` auth cookie
survives), API keys (``X-Api-Key`` for the Prowlarr/Lidarr REST APIs),
content-type, and the request body. Responses come back with the
upstream status code and a curated set of headers (``Content-Type``,
``Content-Disposition``, ``Set-Cookie``).

CORS is permissive (``Access-Control-Allow-Origin: *``) so the
music-search SPA can hit these endpoints from anywhere — the original
``proxy.py`` did the same. This service is intended for private,
LAN-side deployment; lock down with Caddy if exposing to the public
internet.

The ``GET /music-search`` route serves the SPA's HTML from
``app/ui/static/music-search.html``. Drop your existing file there
before building the image (or mount it at runtime). When the file is
missing the route returns a 404 with a clear message instead of an
opaque internal error.
"""

from __future__ import annotations

import logging
from pathlib import Path

import httpx
from fastapi import APIRouter, HTTPException, Request, Response
from fastapi.responses import FileResponse, PlainTextResponse

from app.config import settings

log = logging.getLogger("music-lib-helper.proxy")

router = APIRouter(tags=["proxy"])

# Headers we forward TO the upstream service. Hop-by-hop headers and
# the Host header are deliberately not in this set.
_FORWARD_REQUEST_HEADERS = {
    "cookie", "content-type", "x-api-key", "x-csrf-token",
    "accept", "accept-language", "user-agent",
}

# Headers we copy FROM the upstream response back to the caller.
_FORWARD_RESPONSE_HEADERS = {
    "content-type", "content-disposition", "set-cookie",
    "cache-control", "etag", "last-modified",
}

# Permissive CORS to match the original proxy.py.
_CORS_HEADERS = {
    "Access-Control-Allow-Origin":  "*",
    "Access-Control-Allow-Methods": "GET, POST, PUT, DELETE, OPTIONS",
    "Access-Control-Allow-Headers": "Content-Type, X-Api-Key, Cookie",
}

# Where /music-search reads its index from. Static so the build step
# can bake it in; runtime users can mount a different file over it.
_STATIC_DIR = Path(__file__).resolve().parents[1] / "ui" / "static"
_MUSIC_SEARCH_HTML = _STATIC_DIR / "music-search.html"


# ── core: one request forwarder ────────────────────────────────────────────
async def _forward(request: Request, target_url: str) -> Response:
    """Forward ``request`` to ``target_url`` and return the upstream's response."""
    headers = {
        k: v for k, v in request.headers.items()
        if k.lower() in _FORWARD_REQUEST_HEADERS
    }
    body = await request.body()

    try:
        async with httpx.AsyncClient(timeout=60.0, follow_redirects=True) as client:
            upstream = await client.request(
                method=request.method,
                url=target_url,
                content=body if body else None,
                headers=headers,
            )
    except httpx.HTTPError as exc:
        log.warning("proxy upstream error to %s: %s", target_url, exc)
        raise HTTPException(status_code=502, detail=f"upstream error: {exc}") from exc

    response_headers = {
        k: v for k, v in upstream.headers.items()
        if k.lower() in _FORWARD_RESPONSE_HEADERS
    }
    response_headers.update(_CORS_HEADERS)
    return Response(
        content=upstream.content,
        status_code=upstream.status_code,
        headers=response_headers,
    )


def _join(base: str, path: str, qs: str) -> str:
    """Build the upstream URL preserving the query string."""
    url = f"{base.rstrip('/')}/{path.lstrip('/')}"
    return f"{url}?{qs}" if qs else url


# ── /proxy/prowlarr/* ──────────────────────────────────────────────────────
@router.api_route(
    "/proxy/prowlarr/{path:path}",
    methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
)
async def proxy_prowlarr(path: str, request: Request) -> Response:
    if request.method == "OPTIONS":
        return Response(status_code=204, headers=_CORS_HEADERS)
    return await _forward(request, _join(settings.prowlarr_url, path, request.url.query))


# ── /proxy/lidarr/* ────────────────────────────────────────────────────────
@router.api_route(
    "/proxy/lidarr/{path:path}",
    methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
)
async def proxy_lidarr(path: str, request: Request) -> Response:
    if request.method == "OPTIONS":
        return Response(status_code=204, headers=_CORS_HEADERS)
    return await _forward(request, _join(settings.lidarr_url, path, request.url.query))


# ── /proxy/qbit/* ──────────────────────────────────────────────────────────
@router.api_route(
    "/proxy/qbit/{path:path}",
    methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
)
async def proxy_qbit(path: str, request: Request) -> Response:
    if request.method == "OPTIONS":
        return Response(status_code=204, headers=_CORS_HEADERS)
    return await _forward(request, _join(settings.qbit_url, path, request.url.query))


# ── /music-search (the SPA) ────────────────────────────────────────────────
@router.get("/music-search", include_in_schema=False)
@router.get("/music-search/", include_in_schema=False)
async def music_search_index() -> Response:
    if not _MUSIC_SEARCH_HTML.exists():
        return PlainTextResponse(
            "music-search.html is not installed. Drop your file at "
            f"{_MUSIC_SEARCH_HTML} (relative to the project root: "
            "app/ui/static/music-search.html) and rebuild the image.",
            status_code=404,
            headers=_CORS_HEADERS,
        )
    return FileResponse(
        _MUSIC_SEARCH_HTML,
        media_type="text/html; charset=utf-8",
        headers=_CORS_HEADERS,
    )


# Sibling static assets the SPA references (its own .js / .css).
@router.get("/music-search/{filename}", include_in_schema=False)
async def music_search_asset(filename: str) -> Response:
    # Prevent path traversal — only a bare filename is allowed.
    if "/" in filename or "\\" in filename or filename.startswith(".."):
        raise HTTPException(status_code=404)
    target = _STATIC_DIR / filename
    if not target.exists() or not target.is_file():
        raise HTTPException(status_code=404)
    media = (
        "application/javascript" if filename.endswith(".js")
        else "text/css"          if filename.endswith(".css")
        else "text/html; charset=utf-8" if filename.endswith(".html")
        else "application/octet-stream"
    )
    return FileResponse(target, media_type=media, headers=_CORS_HEADERS)
