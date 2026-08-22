"""FastAPI application entry point.

Phase 1 wiring:
  - boot the container,
  - confirm settings loaded from .env are visible,
  - initialise the SQLite schema,
  - mount the API routers,
  - respond to a /health check.

The scheduler and the remaining routers get added as their core
services are ported.
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from app import __version__, scheduler
from app.api import artist, backup, enrich, field_overrides, library, logs, mapping, mb, picard, playlist, proxy, settings as settings_api
from app.config import settings
from app.core.beets_config import sync_beets_vgmdb_url, validate_beet_bin
from app.logging_config import setup_logging
from app.storage import db
from app.ui import router as ui


# ── Logging ─────────────────────────────────────────────────────────────────
setup_logging()
log = logging.getLogger("music-lib-helper")


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup/shutdown hook. Runs once per process lifecycle."""

    log.info("music-lib-helper v%s starting", __version__)
    log.info("data dir:  %s", settings.app_data_dir)
    if settings.app_log_to_files:
        log.info("log files: %s", settings.app_log_dir)
    log.info("music dir: %s", settings.app_music_dir)
    log.info("lidarr:    %s", settings.lidarr_url)
    log.info("vgmdb:     %s", settings.vgmdb_url)

    placeholders = settings.placeholder_fields()
    if placeholders:
        log.warning(
            "the following secret env vars are still PLACEHOLDER_ME: %s "
            "(fill them in .env and restart)",
            ", ".join(placeholders),
        )

    # Ensure the data directory exists. The Dockerfile already creates it,
    # but mounting an empty named volume can shadow that, so we recreate
    # at startup as well.
    settings.app_data_dir.mkdir(parents=True, exist_ok=True)

    # Create the SQLite schema (jobs + activity_log). Idempotent.
    db.init_db()

    # Beets reads VGMplug.baseurl from BEETSDIR/config.yaml, not from
    # settings.vgmdb_url — sync so the subprocess sees the live URL.
    sync_beets_vgmdb_url()
    validate_beet_bin()

    # Start the in-process scheduler (Sunday scan + enrich crons).
    # Set SCAN_CRON / ENRICH_CRON to "" in .env to disable either.
    scheduler.start()

    yield

    scheduler.stop()
    log.info("music-lib-helper shutting down")


app = FastAPI(
    title="Music Library Helper",
    description=(
        "Consolidated REST API for Lidarr/Picard music library "
        "enrichment workflows."
    ),
    version=__version__,
    lifespan=lifespan,
)

# ── static files + web UI ─────────────────────────────────────────────────────
app.mount("/static", StaticFiles(directory="app/ui/static"), name="static")
ui.templates.env.globals["app_version"] = __version__
# Every hardcoded internal link/fetch in the templates and static JS is
# manually prefixed with this (see static/js/url-base.js for the JS side,
# and the many `{{ url_base }}/...` hrefs across app/ui/templates/*.html)
# rather than relying solely on ASGI root_path propagation through
# url_for() — that part *is* handled automatically by the Mount() below
# for static-file URLs, but hardcoded string literals bypass url_for()
# entirely and need this to actually go anywhere useful when the app is
# served from a subpath.
ui.templates.env.globals["url_base"] = settings.url_base

# ── API routers ─────────────────────────────────────────────────────────────
app.include_router(library.router)
app.include_router(artist.router)
app.include_router(mapping.router)
app.include_router(field_overrides.router)
app.include_router(mb.router)
app.include_router(picard.router)
app.include_router(playlist.router)
app.include_router(proxy.router)
app.include_router(enrich.router)
app.include_router(logs.router)
app.include_router(settings_api.router)
app.include_router(backup.router)
app.include_router(ui.router)


@app.get("/health", tags=["meta"])
async def health() -> dict[str, object]:
    """Liveness probe used by docker-compose healthcheck."""
    return {
        "ok": True,
        "version": __version__,
        "placeholders": settings.placeholder_fields(),
    }


# ── URL base (subpath deployment) ───────────────────────────────────────────
# Same idea as Sonarr/Radarr/Lidarr's "URL Base" setting: when set, the
# whole app — UI, API, static assets, health check, docs — actually gets
# served under that path prefix, so a reverse proxy can pass e.g.
# `/music-helper/*` straight through with no path-rewriting. This wraps
# `app` (all routes above stay defined at their normal, unprefixed paths)
# in an outer mounting shell; Starlette's Mount() strips the prefix
# before dispatching to `app`, and sets ASGI root_path so `url_for()`
# (used for static asset URLs) automatically includes it too.
#
# Starlette does NOT automatically run a mounted sub-app's lifespan —
# only the top-level ASGI app's lifespan fires on startup/shutdown. Since
# `asgi_app`, not `app`, is what uvicorn actually drives once URL_BASE is
# set, the outer shell needs its own lifespan that just delegates into
# `app`'s real one — otherwise db.init_db(), the scheduler, etc. would
# silently never run. (Caught by an actual TestClient run against
# asgi_app failing on "no such table: activity_log" — not hypothetical.)
#
# `asgi_app` — not `app` — is what actually gets served (see the
# Dockerfile's uvicorn command). `app` stays importable on its own
# (`from app.main import app`) for tests, which don't care about url_base
# and exercise `lifespan` directly via `TestClient(app)`.
if settings.url_base:
    log.info("serving under URL base: %s", settings.url_base)

    @asynccontextmanager
    async def _root_lifespan(_: FastAPI):
        async with lifespan(app):
            yield

    _root = FastAPI(openapi_url=None, docs_url=None, redoc_url=None, lifespan=_root_lifespan)
    _root.mount(settings.url_base, app)
    asgi_app = _root
else:
    asgi_app = app

