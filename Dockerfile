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

# ── Beets configuration and custom plugin ────────────────────────────────────
# The custom VGMplug plugin lives at app/beets_plugins/VGMplug.py in the repo.
# Drop your VGMplug_custom.py there (renamed to VGMplug.py) before building.
RUN mkdir -p ${BEETSDIR}
COPY config.yaml ${BEETSDIR}/config.yaml
COPY app/beets_plugins/ ${BEETSDIR}/beetsplug/

# ── Application code ─────────────────────────────────────────────────────────
COPY app/ ./app/
COPY scripts/ ./scripts/
COPY lidarr-scripts/ ./lidarr-scripts/

# ── Runtime data directory ───────────────────────────────────────────────────
RUN mkdir -p ${APP_DATA_DIR}

EXPOSE 8900

ENTRYPOINT ["/usr/bin/tini", "--"]
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8900"]
