# Music Enrichment Management Service

A self-contained Docker service ("music-lib-helper") that consolidates a Lidarr/Picard music-library enrichment pipeline into one FastAPI app, with a web UI for day-to-day use. It's tuned for soundtrack/VGM (video game & anime music) collections, where MusicBrainz metadata is often thin and [VGMDB](https://vgmdb.net) is the better source.

What it does:

- Scans the music library and tracks which albums have MusicBrainz IDs.
- Resolves albums against VGMDB (MB URL → catalog → barcode → title) and lets you record/curate the mapping.
- Runs `beet import` with a custom VGMDB beets plugin to tag albums, including rewriting non-Latin artist tags to their English alias.
- Fixes Lidarr artist folder paths that come in kanji/native script.
- Exports MusicBrainz artist IDs for Picard's Post-Tagging Action, optionally syncing to a GitHub Gist.
- Sends Discord notifications and runs the above on a weekly schedule.
- Ships a web UI (dashboard, VGMDB mapping queue, enrichment runs, library browser, activity log, and a MusicBrainz → Lidarr → Prowlarr → qBittorrent search/download tool) — see [Web UI](#web-ui) below.

## Documentation

- `[docs/music-lib-helper-service-plan.md](docs/music-lib-helper-service-plan.md)`
— full architecture, API reference, and design decisions.
- `[docs/PHASE-1-COMPLETE.md](docs/PHASE-1-COMPLETE.md)` — status
snapshot of the core API (Phase 1).
- `[lidarr-scripts/README.md](lidarr-scripts/README.md)` — installing
the Lidarr custom-script wrappers.
- `[picard-scripts/README.md](picard-scripts/README.md)` — installing
the Picard Post-Tagging Action wrapper.
- In-app: once it's running, the **Help** page (`/help`) has a full workflow guide and per-page reference — the docs above are for the code, that page is for using the site day to day.

## Deploy with Docker Compose (recommended)

Every push to `main` publishes a multi-arch (amd64/arm64) image to
[GHCR](https://github.com/KwasiAsante/Music_Enrichment_Management_Service/pkgs/container/music_enrichment_management_service),
so deploying doesn't require cloning this repo or building anything — two
files are enough:

1. **Grab the two files** into an empty folder on your server:

   ```bash
   mkdir music-lib-helper && cd music-lib-helper
   curl -O https://raw.githubusercontent.com/KwasiAsante/Music_Enrichment_Management_Service/main/docker-compose.yml.example
   curl -O https://raw.githubusercontent.com/KwasiAsante/Music_Enrichment_Management_Service/main/.env.example
   mv docker-compose.yml.example docker-compose.yml
   mv .env.example .env
   ```

2. **Fill in** `.env` — every `PLACEHOLDER_ME` (Lidarr/Prowlarr API keys, qBittorrent password, Discord webhooks, GitHub token + Gist id, `WEB_UI_USER`/`WEB_UI_PASS`), plus `LIDARR_URL`/`PROWLARR_URL`/`QBIT_URL`/`VGMDB_URL` and `HOST_MUSIC_DIR` (where your music actually lives on the host).

3. **Start it:**

   ```bash
   docker compose up -d
   ```

   This pulls `ghcr.io/kwasiasante/music_enrichment_management_service:latest` — no `--build` needed, and no local copy of the Dockerfile or source required. The custom VGMDB beets plugin (`app/beets_plugins/VGMplug.py`) already ships baked into the published image.

4. Check `GET /health` (reports any env vars still left as `PLACEHOLDER_ME`) and `GET /docs` for the interactive API reference.
5. Open `http://<host>:8900/` and log in with `WEB_UI_USER`/`WEB_UI_PASS`. The in-app **Help** page (top of the sidebar nav) walks through the actual day-to-day workflow.

Point Lidarr's custom scripts and Picard's Post-Tagging Action at the running container per the READMEs linked above.

### Updating

```bash
docker compose pull && docker compose up -d
```

### Building from source instead

If you've cloned the repo — to modify app code, or swap in your own build of the VGMDB plugin — copy `docker-compose.yml.example` to `docker-compose.yml` (it's gitignored, so your local copy won't show up as a repo change), then add a `docker-compose.override.yml` next to it:

```yaml
services:
  music-lib-helper:
    build: .
```

Compose merges override files in automatically, so `docker compose up -d --build` now builds locally from the `Dockerfile` in the repo root instead of pulling. The `image:` name in `docker-compose.yml` is reused as the local build's tag either way, so removing `docker-compose.override.yml` later just goes back to pulling from GHCR.

### Joining an existing *arr stack's network

By default this runs on its own Docker network and reaches Lidarr/Prowlarr/qBittorrent via whatever URLs you put in `.env` (host IP, LAN IP, `host.docker.internal`, etc.). If those already run in Docker and you'd rather address them by container name, uncomment the `networks:` block at the bottom of your local `docker-compose.yml` (see `docker-compose.yml.example`) and point it at your stack's actual network name (`docker network ls`).

## Running without Docker

Docker isn't required — everything in `Dockerfile` beyond installing python packages is either OS setup or path defaults tuned for a container, both reproducible locally. This is what the image does that you'd otherwise be doing by hand:

1. **Create a venv and install dependencies.**

   ```bash
   python -m venv .venv
   .venv\Scripts\activate          # Windows (PowerShell/cmd)
   # source .venv/bin/activate     # macOS/Linux
   pip install -r requirements.txt
   ```

2. **Install** `ffmpeg` (beets needs it for tag handling on some formats) and make sure it's on `PATH` — e.g. `choco install ffmpeg` on Windows, `brew install ffmpeg` on macOS, `apt install ffmpeg` on Debian/Ubuntu.

3. **Install the custom VGMDB plugin into your venv** — beets discovers plugins via `beetsplug.<name>`, a namespace package that has to live in `site-packages/beetsplug/`. `app/beets_plugins/VGMplug.py` is already in the repo, so this just copies it into place the same way the Dockerfile does:

   ```bash
   python -c "import beetsplug, shutil; shutil.copy('app/beets_plugins/VGMplug.py', beetsplug.__path__[0])"
   ```

   If you're using your own build of the plugin instead, drop it at that same path (renamed to `VGMplug.py`) before running the command above.

4. **Point** `BEETSDIR` **at a real folder and drop** `config.yaml` **there.** This one matters: beets itself reads `BEETSDIR` straight from the OS process environment, not from `.env` — pydantic-settings parses `.env` for *this app's* settings, but doesn't export it to the environment for the `beet` subprocess to see. So this has to be a real environment variable, not just an `.env` entry:

   ```bash
   mkdir beets-config
   copy config.yaml beets-config\config.yaml      # Windows
   # cp config.yaml beets-config/config.yaml      # macOS/Linux
   set BEETSDIR=%cd%\beets-config                 # Windows cmd
   # $env:BEETSDIR = "$pwd\beets-config"          # Windows PowerShell
   # export BEETSDIR="$(pwd)/beets-config"        # macOS/Linux
   ```

5. **Copy** `.env.example` **to** `.env` and fill in the usual secrets, but also override the path defaults that only make sense inside a container — `app_data_dir`/`app_music_dir` default to `/data`/`/music`, and `beet_bin` defaults to `/usr/local/bin/beet`:

   ```ini
   APP_DATA_DIR=./data
   APP_MUSIC_DIR=D:\Music                # wherever your library actually lives
   BEET_BIN=beet                         # just needs to resolve on PATH
   ```

   `APP_DATA_DIR` gets created automatically at startup if it doesn't exist — same `mkdir(parents=True, exist_ok=True)` call runs whether you're in Docker or not, so no manual step needed there.

6. **Run it:**

   ```bash
   uvicorn app.main:asgi_app --reload --port 8900
   ```

   Same `.env`, same behavior as the container — `--reload` is the one addition, handy for local development since it restarts on code changes.

What you lose by skipping Docker: process supervision (`tini`, `restart:unless-stopped`), the isolated filesystem, and not needing `ffmpeg`/beets installed on your actual machine. Fine for local development; the container is still what's meant for actually running this day to day.

## Web UI

Eight pages, server-rendered (Jinja2 + vanilla JS, no build step), all behind
a login:

| Page          | Path            | What it does                                                                                                                               |
| ------------- | --------------- | ------------------------------------------------------------------------------------------------------------------------------------------ |
| Dashboard     | `/`             | Library stats, recent activity (filterable by category), one-click scan (with an opt-in cleanup pass), artist path fix, and enrichment run |
| Mappings      | `/mappings`     | Queue of albums without a VGMDB association — search VGMDB, set, or skip; excluded-artists list; export/import backups                     |
| Enrich        | `/enrich`       | Run enrichment against filtered artists/albums or specific redos, with a live progress/log view                                            |
| Library       | `/library`      | Browse every scanned album — filter, group by artist, list/grid layout with cover art, click through to an album's full detail page        |
| Logs          | `/logs`         | Full activity log — filter by category, level, or artist                                                                                   |
| Music Search  | `/music-search` | MusicBrainz search → add to Lidarr, or search Prowlarr/Nyaa indexers and send straight to qBittorrent                                      |
| Help          | `/help`         | In-app documentation — a step-by-step workflow guide plus a reference section per page                                                     |
| Settings      | `/settings`     | View/edit runtime configuration and restart the app to apply changes — see below                                                           |

**Auth:** HTTP Basic Auth (`WEB_UI_USER`/`WEB_UI_PASS` in `.env`) protects all eight pages plus `/proxy/`* (what Music Search uses to reach Lidarr, Prowlarr, and qBittorrent). The REST API under `/api/v1/*` and `/health` are **intentionally left open** — `lidarr-scripts/on_album_download.py` calls `POST /api/v1/enrich/album` directly from the Lidarr host with no browser involved, and Docker's healthcheck hits `/health` with plain `curl`; neither can answer a login prompt. In practice this means someone with API knowledge and LAN access can still reach the API directly, bypassing the login — a deliberate scope trade-off for a single-operator, LAN-only deployment, not an oversight. Lock down further with a reverse proxy (e.g. Caddy) if you need to expose this beyond your LAN. `/api/v1/settings/*` is the one exception — it sits behind the same login even on the API side, since nothing external needs to call it and it can change credentials or restart the process.

Every credential Music Search needs (Lidarr/Prowlarr API keys, qBittorrent password) is injected server-side from `.env` — none of it lives in the browser or in any file that could end up committed to this repo.

### Settings & restarting

Everything under `/settings` is read from `.env`/the environment once, at startup — there's no hot-reload, so every change needs a restart to actually apply. Saving a change writes it to `{APP_DATA_DIR}/settings_override.json` (inside the same `./data` volume as everything else — see `.gitignore`, which now excludes `data/` entirely, since this file can hold plaintext secrets), which takes priority over `.env` on the *next* boot. The "Restart Now" button on that page sends the process a graceful `SIGTERM`; Docker's `restart: unless-stopped` policy (or uvicorn's `--reload` supervisor for local dev) brings it back up automatically.
