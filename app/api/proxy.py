"""Reverse proxy to Prowlarr, Lidarr, and qBittorrent.

Three routes, all on the same FastAPI app so we keep the "single port,
single container" promise from the plan:

* ``/proxy/prowlarr/{path}`` → forwards to ``PROWLARR_URL``
* ``/proxy/lidarr/{path}``   → forwards to ``LIDARR_URL``
* ``/proxy/qbit/{path}``     → forwards to ``QBIT_URL``

The proxy passes through cookies (so qBittorrent's ``SID`` auth cookie
survives), content-type, and the request body. Responses come back with
the upstream status code and a curated set of headers (``Content-Type``,
``Content-Disposition``, ``Set-Cookie``).

**Credentials never live in the browser.** The Prowlarr/Lidarr routes strip
any client-supplied ``apikey`` query param and inject the real one from
``settings`` before forwarding — the music-search page (served from
``app.ui.router``, Phase 2) sends no key at all. The qBittorrent login path
is special-cased the same way: whatever the client posts to
``/proxy/qbit/api/v2/auth/login`` is ignored, and the proxy always logs in
with ``settings.qbit_user``/``settings.qbit_pass`` instead — unless
``settings.qbit_api_key`` is set, in which case login is skipped entirely
and every request is authenticated via ``Authorization: Bearer …`` (qBittorrent
v5.2+ API key auth). This used to be
the other way around — real keys and a real password shipped as default
values in the static ``music-search.html`` file, readable via view-source
by anyone with network access to the page. Since every one of these
secrets already had a proper ``Settings`` field (see ``app/config.py``),
routing them server-side here closes that gap without adding new config.

**This router is now behind the same login as the web UI** (see
``dependencies=`` below, reusing :func:`app.ui.auth.require_login`).
Originally these routes were left open — the reasoning was "``/api/v1/*``
stays open for the Lidarr webhook, so `/proxy/*` can too" — but on
reflection ``/proxy/*`` is *only* ever called from the browser (Music
Search's JS), never server-to-server the way ``/api/v1/enrich/album`` is.
Leaving it open meant someone could skip the login page entirely and add
torrents straight through ``/proxy/qbit/...``, which defeats the point of
gating the page in front of it. ``/api/v1/*`` and ``/health`` remain open —
those genuinely are called without a browser (see ``app/ui/auth.py``).

Because this only matters for same-origin calls (the page and these routes
share one FastAPI app now), the browser reuses the Basic Auth credentials
it already cached from loading ``/music-search`` — no changes needed in
``music-search.js``. The ``OPTIONS`` short-circuit below now also requires
auth as a side effect of the router-level dependency; that's fine for a
same-origin deployment and only mattered when this proxy was reachable
cross-origin, which it no longer is.

CORS is permissive (``Access-Control-Allow-Origin: *``) so these endpoints
are reachable the same way the original standalone ``proxy.py`` allowed.
This service is intended for private, LAN-side deployment; lock down with
Caddy if exposing to the public internet.
"""

from __future__ import annotations

import logging
from urllib.parse import parse_qsl, quote, urlencode

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request, Response

from app.config import settings
from app.ui.auth import require_login

log = logging.getLogger("music-lib-helper.proxy")

router = APIRouter(tags=["proxy"], dependencies=[Depends(require_login)])

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


# ── core: one request forwarder ────────────────────────────────────────────
async def _forward(
    request: Request,
    target_url: str,
    override_body: bytes | None = None,
    extra_headers: dict[str, str] | None = None,
) -> Response:
    """Forward ``request`` to ``target_url`` and return the upstream's response.

    ``override_body``, when given, replaces the request's own body entirely
    — used by the qBittorrent login special-case below so the real password
    never needs to be read from (or exist in) the browser.

    ``extra_headers`` are merged in last (overriding any client-supplied
    values with the same name) — used to inject the server-side qBittorrent
    API key without exposing it to the browser.
    """
    headers = {
        k: v for k, v in request.headers.items()
        if k.lower() in _FORWARD_REQUEST_HEADERS
    }
    if extra_headers:
        headers.update(extra_headers)
    body = override_body if override_body is not None else await request.body()

    try:
        async with httpx.AsyncClient(timeout=60.0, follow_redirects=True) as client:
            upstream = await client.request(
                method=request.method,
                url=target_url,
                content=body if body else None,
                headers=headers,
            )
    except httpx.HTTPError as exc:
        log.warning("proxy upstream error %s %s: %s", request.method, target_url, exc)
        raise HTTPException(status_code=502, detail=f"upstream error: {exc}") from exc

    log.info(
        "proxy %s %s → %s (%s)",
        request.method, request.url.path, target_url.split("?", 1)[0], upstream.status_code,
    )

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


def _inject_apikey(query: str, api_key: str) -> str:
    """Strip any client-supplied ``apikey`` and inject the server's real one.

    Called on every Prowlarr/Lidarr request regardless of what the client
    sent — the music-search page's own requests carry no key at all, and
    this makes that the only thing that actually works, rather than a
    convention the client has to honor.
    """
    params = [
        (k, v) for k, v in parse_qsl(query, keep_blank_values=True)
        if k.lower() != "apikey"
    ]
    params.append(("apikey", api_key))
    return urlencode(params)


# ── /proxy/prowlarr/* ──────────────────────────────────────────────────────
@router.api_route(
    "/proxy/prowlarr/{path:path}",
    methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
)
async def proxy_prowlarr(path: str, request: Request) -> Response:
    if request.method == "OPTIONS":
        return Response(status_code=204, headers=_CORS_HEADERS)
    qs = _inject_apikey(request.url.query, settings.prowlarr_api_key)
    return await _forward(request, _join(settings.prowlarr_url, path, qs))


# ── /proxy/lidarr/* ────────────────────────────────────────────────────────
@router.api_route(
    "/proxy/lidarr/{path:path}",
    methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
)
async def proxy_lidarr(path: str, request: Request) -> Response:
    if request.method == "OPTIONS":
        return Response(status_code=204, headers=_CORS_HEADERS)
    qs = _inject_apikey(request.url.query, settings.lidarr_api_key)
    return await _forward(request, _join(settings.lidarr_url, path, qs))


# ── /proxy/qbit/* ──────────────────────────────────────────────────────────
@router.api_route(
    "/proxy/qbit/{path:path}",
    methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
)
async def proxy_qbit(path: str, request: Request) -> Response:
    if request.method == "OPTIONS":
        return Response(status_code=204, headers=_CORS_HEADERS)

    target = _join(settings.qbit_url, path, request.url.query)
    qbit_auth = (
        {"Authorization": f"Bearer {settings.qbit_api_key}"}
        if settings.qbit_uses_api_key()
        else None
    )

    if request.method == "POST" and path.rstrip("/") == "api/v2/auth/login":
        if settings.qbit_uses_api_key():
            # API keys cannot hit auth endpoints — the browser still calls
            # login for cookie-based flow, so stub success when key auth is on.
            response_headers = dict(_CORS_HEADERS)
            return Response(status_code=204, headers=response_headers)

        # Always log in with the server-configured account, regardless of
        # what (if anything) the client posted here.
        login_body = (
            f"username={quote(settings.qbit_user)}&password={quote(settings.qbit_pass)}"
        ).encode()
        return await _forward(
            request,
            target,
            override_body=login_body,
        )

    return await _forward(request, target, extra_headers=qbit_auth)
