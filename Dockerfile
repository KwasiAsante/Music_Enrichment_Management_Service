# syntax=docker/dockerfile:1.6
FROM python:3.12-slim

# ── Environment ──────────────────────────────────────────────────────────────
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1 \
    BEETSDIR=/config/beets \
    APP_DATA_DIR=/data \
    APP_MUSIC_DIR=/music

WORKDIR /app

# ── System dependencies ──────────────────────────────────────────────────────
# beets needs ffmpeg for tag handling on some formats; git is useful for any
# pip installs that pull from source. tini gives us proper PID 1 signal
# handling so docker stop is graceful.
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        ffmpeg \
        git \
        tini \
        ca-certificates \
        curl \
    && rm -rf /var/lib/apt/lists/*

# ── Python dependencies ──────────────────────────────────────────────────────
COPY requirements.txt .
RUN pip install -r requirements.txt

# ── Beets configuration ──────────────────────────────────────────────────────
RUN mkdir -p ${BEETSDIR}
COPY config.yaml ${BEETSDIR}/config.yaml

# ── Custom beets plugins ─────────────────────────────────────────────────────
# Beets discovers plugins by importing `beetsplug.<NAME>` (a Python namespace
# package), so the .py files have to land in the on-disk `beetsplug/`
# directory that's on PYTHONPATH — i.e. inside site-packages. We discover
# that path dynamically so it isn't tied to a Python version.
#
# app/beets_plugins/VGMplug.py is committed in the repo and ships in every
# published image — no manual step needed for a normal deploy. Swapping in
# your own build: replace that file (keep the name VGMplug.py) and rebuild
# with `docker compose up -d --build`. Either way, this step still fails
# loudly if the directory ends up with no .py files at all — the helper is
# useless without the plugin, so an empty directory should never build
# silently into a broken image.
COPY app/beets_plugins/ /tmp/beets_plugins/
RUN BEETSPLUG_DIR="$(python3 -c 'import beetsplug, os; \
        print(next(p for p in beetsplug.__path__ if "site-packages" in p))')" \
    && PY_FILES=$(find /tmp/beets_plugins -maxdepth 1 -name '*.py' -type f) \
    && if [ -z "$PY_FILES" ]; then \
        echo "ERROR: no .py files in app/beets_plugins/ — drop your custom" >&2; \
        echo "       VGMplug.py there before docker compose up --build" >&2; \
        exit 1; \
    fi \
    && cp -v $PY_FILES "$BEETSPLUG_DIR/" \
    && rm -rf /tmp/beets_plugins

# ── Application code ─────────────────────────────────────────────────────────
COPY app/ ./app/
COPY scripts/ ./scripts/
COPY lidarr-scripts/ ./lidarr-scripts/

# ── Runtime data directory ───────────────────────────────────────────────────
RUN mkdir -p ${APP_DATA_DIR}

EXPOSE 8900

ENTRYPOINT ["/usr/bin/tini", "--"]
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8900"]
