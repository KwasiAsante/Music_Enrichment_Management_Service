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
from app.api import artist, enrich, library, logs, mapping, mb, picard, proxy
from app.config import settings
from app.storage import db
from app.ui import router as ui


# ── Logging ─────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=getattr(logging, settings.app_log_level.upper(), logging.INFO),
    format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
)
log = logging.getLogger("music-lib-helper")


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup/shutdown hook. Runs once per process lifecycle."""

    log.info("music-lib-helper v%s starting", __version__)
    log.info("data dir:  %s", settings.app_data_dir)
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

# ── API routers ─────────────────────────────────────────────────────────────
app.include_router(library.router)
app.include_router(artist.router)
app.include_router(mapping.router)
app.include_router(mb.router)
app.include_router(picard.router)
app.include_router(proxy.router)
app.include_router(enrich.router)
app.include_router(logs.router)
app.include_router(ui.router)


@app.get("/health", tags=["meta"])
async def health() -> dict[str, object]:
    """Liveness probe used by docker-compose healthcheck."""
    return {
        "ok": True,
        "version": __version__,
        "placeholders": settings.placeholder_fields(),
    }

