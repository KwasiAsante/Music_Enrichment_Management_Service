# Lidarr Helper Service — Architecture & Implementation Plan
> A self-contained Docker service that consolidates all custom scripts into a
> single deployable web UI + API server. Pull the repo, run `docker compose up`,
> and the entire enrichment pipeline is restored.

---

## Table of Contents
1. [Goals](#goals)
2. [What Gets Consolidated](#what-gets-consolidated)
3. [Architecture Overview](#architecture-overview)
4. [Project Structure](#project-structure)
5. [API Endpoints](#api-endpoints)
6. [Web UI Pages](#web-ui-pages)
7. [Docker Setup](#docker-setup)
8. [Integration Points](#integration-points)
9. [Implementation Phases](#implementation-phases)
10. [Tech Stack Recommendation](#tech-stack-recommendation)

---

## Goals

- **Single repo** — everything needed to restore the pipeline lives in one git repository
- **Single `docker compose up`** — no manual script setup, pip installs, or path configuration
- **Lidarr-compatible** — Lidarr custom scripts point to the container instead of host files
- **Picard-compatible** — Picard Post-Tagging Action calls the container API instead of a local script
- **Web UI** — manage mappings, trigger enrichment, view logs — no SSH needed
- **Persistent state** — `vgmdb_mapping.json`, `enriched_albums.json`, `album_list.json` stored in a Docker volume

---

## What Gets Consolidated

### Scripts → API Endpoints

| Current Script | New API Endpoint | Trigger |
|----------------|-----------------|---------|
| `on_album_download.py` | `POST /api/enrich/album` | Lidarr custom script (calls API) |
| `on_artist_add.py` | `POST /api/artist/fix-path` | Lidarr custom script (calls API) |
| `scan-library.py` | `POST /api/library/scan` | Called by above, or scheduled |
| `generate-mappings-template.py` | `POST /api/mapping/search` + `PUT /api/mapping/{id}` | Web UI |
| `beets-enrich.py` | `POST /api/enrich/run` | Web UI or scheduled |
| `fix_artist_paths.py` | `POST /api/artist/fix-all-paths` | Web UI |
| `map-vgmdb.py` | `GET/POST/DELETE /api/mapping` | Web UI |
| `mb_vgmdb_link.py` | `GET /api/mb/vgmdb-link/{mb_id}` | Internal helper |
| `export_mbids.py` | `POST /api/picard/export` | Picard Post-Tagging Action |

### Scripts → Scheduled Jobs

| Current Cron Job | New Scheduler |
|-----------------|---------------|
| Sunday 2am: `scan-library.py` | APScheduler cron job inside container |
| Sunday 3am: `beets-enrich.py` | APScheduler cron job inside container |

### The Proxy Server

The existing `proxy.py` (music search proxy) gets merged into this service — one port, one container.

---

## Architecture Overview

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                    lidarr-helper  (Docker container)                         │
│                                                                               │
│  ┌─────────────────────────────────────────────────────────────────────┐    │
│  │                         FastAPI Application                          │    │
│  │                                                                       │    │
│  │  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐              │    │
│  │  │  Web UI      │  │  REST API    │  │  Scheduler   │              │    │
│  │  │  (Jinja2 or  │  │  /api/v1/    │  │  (APScheduler│              │    │
│  │  │   React SPA) │  │             │  │   cron jobs) │              │    │
│  │  └──────┬───────┘  └──────┬───────┘  └──────┬───────┘              │    │
│  │         │                 │                  │                       │    │
│  │         └─────────────────┼──────────────────┘                      │    │
│  │                           │                                          │    │
│  │  ┌────────────────────────▼────────────────────────────────────┐    │    │
│  │  │                    Core Services                              │    │    │
│  │  │                                                               │    │    │
│  │  │  LibraryScanner │ VGMDBMapper │ BeetsEnricher │ ArtistFixer  │    │    │
│  │  │  MBLinkChecker  │ Notifier   │ PicardExporter               │    │    │
│  │  └───────────────────────────────────────────────────────────────┘   │    │
│  └─────────────────────────────────────────────────────────────────────┘    │
│                                                                               │
│  ┌──────────────────────────────┐  ┌──────────────────────────────────┐    │
│  │  /data (Docker Volume)       │  │  /music (bind mount, read-write) │    │
│  │  - vgmdb_mapping.json        │  │  ~/Music/synced_music/Artist/    │    │
│  │  - enriched_albums.json      │  │                                  │    │
│  │  - album_list.json           │  └──────────────────────────────────┘    │
│  │  - mb_artist_cache.json      │                                           │
│  │  - app.db (SQLite for logs)  │                                           │
│  └──────────────────────────────┘                                           │
│                                                                               │
│  Port: 8900                                                                  │
└─────────────────────────────────────────────────────────────────────────────┘
        │                    │                    │
        ▼                    ▼                    ▼
   Lidarr :8686         Picard :5800        Caddy (reverse proxy)
   (custom scripts       (Post-Tagging       /lidarr-helper/*
    call /api/v1/)        Action calls
                          /api/v1/picard/)
```

---

## Project Structure

```
lidarr-helper/
│
├── docker-compose.yml          # Standalone compose (for the helper service only)
├── Dockerfile
├── README.md
│
├── app/
│   ├── main.py                 # FastAPI app entry point
│   ├── config.py               # Settings (from env vars)
│   ├── scheduler.py            # APScheduler setup
│   │
│   ├── api/
│   │   ├── __init__.py
│   │   ├── library.py          # /api/v1/library/*
│   │   ├── mapping.py          # /api/v1/mapping/*
│   │   ├── enrich.py           # /api/v1/enrich/*
│   │   ├── artist.py           # /api/v1/artist/*
│   │   ├── mb.py               # /api/v1/mb/*
│   │   ├── picard.py           # /api/v1/picard/*
│   │   └── proxy.py            # /proxy/* (music-search proxy)
│   │
│   ├── core/
│   │   ├── __init__.py
│   │   ├── library_scanner.py  # from scan-library.py
│   │   ├── vgmdb_mapper.py     # from generate-mappings-template.py
│   │   ├── beets_enricher.py   # from beets-enrich.py
│   │   ├── artist_fixer.py     # from fix_artist_paths.py + on_artist_add.py
│   │   ├── mb_link.py          # from mb_vgmdb_link.py
│   │   ├── notifier.py         # Discord webhook sender
│   │   └── picard_export.py    # from export_mbids.py
│   │
│   ├── models/
│   │   ├── __init__.py
│   │   ├── mapping.py          # Pydantic models for mapping data
│   │   ├── album.py            # Album, Artist models
│   │   └── job.py              # Background job status models
│   │
│   ├── storage/
│   │   ├── __init__.py
│   │   ├── json_store.py       # Read/write JSON data files
│   │   └── db.py               # SQLite for job history/logs
│   │
│   └── ui/
│       ├── static/
│       │   ├── css/
│       │   │   └── app.css
│       │   └── js/
│       │       └── app.js
│       └── templates/
│           ├── base.html
│           ├── dashboard.html
│           ├── mappings.html
│           ├── enrich.html
│           ├── library.html
│           ├── logs.html
│           └── music_search.html  # the existing music-search.html
│
├── scripts/                    # Standalone versions kept for CLI use
│   ├── scan-library.py
│   ├── generate-mappings-template.py
│   ├── beets-enrich.py
│   ├── fix_artist_paths.py
│   ├── map-vgmdb.py
│   ├── mb_vgmdb_link.py
│   └── export_mbids.py
│
├── lidarr-scripts/             # Scripts Lidarr calls directly (thin wrappers)
│   ├── on_album_download.py    # HTTP POST to /api/v1/enrich/album
│   └── on_artist_add.py        # HTTP POST to /api/v1/artist/fix-path
│
└── data/                       # Gitignored — persisted via Docker volume
    ├── vgmdb_mapping.json
    ├── enriched_albums.json
    ├── album_list.json
    └── mb_artist_cache.json
```

---

## API Endpoints

### Library

```
POST   /api/v1/library/scan
       Runs scan-library.py logic
       Body: {} or {"artist": "Kenji Kawai"}
       Returns: {total, new, with_mb_id, without_mb_id}

GET    /api/v1/library/albums
       Returns album_list.json contents
       Query: ?artist=Kenji+Kawai&unmapped=true&page=1&limit=50

GET    /api/v1/library/stats
       Returns counts: total, enriched, unmapped, skipped
```

### Mapping

```
GET    /api/v1/mapping
       Returns all vgmdb_mapping.json entries
       Query: ?artist=Kenji+Kawai&source=mb_url_rel

GET    /api/v1/mapping/unmapped
       Returns album_list entries with no mapping

POST   /api/v1/mapping/search
       Search VGMDB for an album (all steps: MB URL → catalog → barcode → title)
       Body: {"mb_release_id": "...", "album": "...", "artist": "..."}
       Returns: {mb_vgmdb_id, catalog_hints, barcode_hints, title_hints}

PUT    /api/v1/mapping/{mb_release_id}
       Set a mapping
       Body: {"vgmdb_id": "1234"} or {"vgmdb_id": "skip"}

DELETE /api/v1/mapping/{mb_release_id}
       Remove a mapping (allows re-mapping)
```

### Enrichment

```
POST   /api/v1/enrich/album
       Called by on_album_download.py (Lidarr custom script)
       Body: {artist_name, album_title, mb_release_id, track_paths}
       Returns: {ok, vgmdb_id, message}

POST   /api/v1/enrich/run
       Bulk enrichment (replaces beets-enrich.py)
       Body: {"artist": "Kenji Kawai", "dry_run": false, "redo": []}
       Returns: job_id (background task)

GET    /api/v1/enrich/jobs/{job_id}
       Get status of a running enrichment job
       Returns: {status, progress, enriched, failed, log}

GET    /api/v1/enrich/log
       Returns recent enrichment log entries
       Query: ?limit=100&artist=Kenji+Kawai
```

### Artist

```
POST   /api/v1/artist/fix-path
       Called by on_artist_add.py (Lidarr custom script)
       Body: {artist_id, mb_id, artist_name, artist_path}
       Returns: {ok, new_path, message}

POST   /api/v1/artist/fix-all-paths
       Bulk path fix (replaces fix_artist_paths.py)
       Body: {"dry_run": false}
       Returns: job_id

GET    /api/v1/artist/paths
       List all artists with non-Latin paths
       Returns: [{artist_name, current_path, suggested_path, mb_alias}]
```

### MusicBrainz

```
GET    /api/v1/mb/vgmdb-link/{mb_release_id}
       Check if MB release has VGMDB link
       Returns: {has_link, vgmdb_url, seed_url}

GET    /api/v1/mb/artist-alias/{mb_artist_id}
       Get English alias for a MB artist
       Returns: {english_name, aliases}
```

### Picard

```
POST   /api/v1/picard/export
       Called by Picard Post-Tagging Action (replaces export_mbids.py)
       Body: {"artist_folder": "/storage/synced_music/Artist/Kenji Kawai"}
       Returns: {ok, artist, mb_id, gist_updated}
```

### Proxy (Music Search)

```
GET    /proxy/prowlarr/*     → forwards to Prowlarr
GET    /proxy/lidarr/*       → forwards to Lidarr
GET/POST /proxy/qbit/*       → forwards to qBittorrent
GET    /music-search         → serves music-search.html
```

---

## Web UI Pages

### Dashboard (`/`)

```
┌─────────────────────────────────────────────────────┐
│  Lidarr Helper                            v1.0.0     │
├─────────────────────────────────────────────────────┤
│                                                       │
│  Library Stats                                        │
│  ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌────────┐ │
│  │  Total   │ │Enriched  │ │ Unmapped │ │ Failed │ │
│  │  436     │ │  407     │ │   21     │ │    0   │ │
│  └──────────┘ └──────────┘ └──────────┘ └────────┘ │
│                                                       │
│  Recent Activity                                      │
│  ✅ Kenji Kawai — Ghost in the Shell OST  2min ago   │
│  🔗 MB seed sent — vgmdb:1234             2min ago   │
│  ✅ Yoko Shimomura — Kingdom Hearts OST   1hr ago    │
│                                                       │
│  Quick Actions                                        │
│  [Scan Library]  [Run Enrichment]  [Fix Artist Paths]│
└─────────────────────────────────────────────────────┘
```

### Mappings (`/mappings`)

```
┌─────────────────────────────────────────────────────┐
│  VGMDB Mappings                                       │
│                                                       │
│  Filter: [Artist...    ] [Status: All ▼] [Search]   │
│                                                       │
│  Unmapped Albums (21)                                 │
│  ┌───────────────────────────────────────────────┐   │
│  │ TCY FORCE — Panty & Stocking OST              │   │
│  │ MB: abc123...  [Search VGMDB] [Set ID] [Skip] │   │
│  ├───────────────────────────────────────────────┤   │
│  │ Kenji Kawai — Ghost in the Shell              │   │
│  │ MB: def456...  [Search VGMDB] [Set ID] [Skip] │   │
│  └───────────────────────────────────────────────┘   │
│                                                       │
│  Search VGMDB results appear inline when clicked     │
│  with catalog/barcode/title match suggestions         │
└─────────────────────────────────────────────────────┘
```

### Enrichment (`/enrich`)

```
┌─────────────────────────────────────────────────────┐
│  Enrichment                                          │
│                                                       │
│  Artist filter: [              ] [All artists]       │
│  Options:       □ Dry run  □ Interactive             │
│                                                       │
│  [▶ Run Enrichment]                                  │
│                                                       │
│  Job: Running... (23/407 albums)                     │
│  ████████░░░░░░░░░░░░  56%                          │
│                                                       │
│  Log:                                                 │
│  ── Shoji Meguro                                     │
│     DONE  PERSONA3 Original Soundtrack (enriched)   │
│     ENRICH PERSONA5 Original Soundtrack             │
│            vgmdb:9999 ✓                             │
└─────────────────────────────────────────────────────┘
```

### Library (`/library`)

```
┌─────────────────────────────────────────────────────┐
│  Library                          [Scan Now]         │
│                                                       │
│  Last scan: 2026-05-11 03:00  │  436 albums          │
│                                                       │
│  Search: [              ]  Artist: [All ▼]           │
│                                                       │
│  Album                          Artist       MB ID   │
│  PERSONA3 Original Soundtrack   Shoji Meguro  ✓      │
│  Ghost in the Shell OST         Kenji Kawai   ✓      │
│  Panty & Stocking OST           TCY FORCE     ✓      │
└─────────────────────────────────────────────────────┘
```

### Music Search (`/music-search`)

The existing `music-search.html` served at this path. All proxy routes already built in.

### Logs (`/logs`)

```
┌─────────────────────────────────────────────────────┐
│  Logs                    Filter: [All ▼] [Artist...] │
│                                                       │
│  2026-05-11 03:53  AlbumDownload: Shoji Meguro       │
│                    PERSONA3 Original Soundtrack       │
│                    ✅ Enriched: vgmdb:4000            │
│                    🔗 MB seed sent                   │
│                                                       │
│  2026-05-10 21:30  ArtistAdd: 川井憲次               │
│                    ✅ Renamed → Kenji Kawai           │
└─────────────────────────────────────────────────────┘
```

---

## Docker Setup

### `Dockerfile`

```dockerfile
FROM python:3.12-slim

WORKDIR /app

# System deps (beets needs these)
RUN apt-get update && apt-get install -y \
    ffmpeg \
    git \
    && rm -rf /var/lib/apt/lists/*

# Python deps
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app/ ./app/
COPY scripts/ ./scripts/
COPY lidarr-scripts/ ./lidarr-scripts/

EXPOSE 8900

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8900"]
```

### `requirements.txt`

```
fastapi>=0.110.0
uvicorn[standard]>=0.29.0
apscheduler>=3.10.0
jinja2>=3.1.0
python-multipart>=0.0.9
requests>=2.31.0
mutagen>=1.47.0
beets>=2.11.0
beets-vgmdb>=1.3.5
aiofiles>=23.0.0
pydantic-settings>=2.0.0
aiosqlite>=0.20.0
httpx>=0.27.0
```

### `docker-compose.yml`

```yaml
services:
  lidarr-helper:
    build: .
    container_name: lidarr-helper
    restart: unless-stopped
    ports:
      - "8900:8900"
    environment:
      # Lidarr
      - LIDARR_URL=http://192.168.2.130:8686/lidarr
      - LIDARR_API_KEY=808f18b659524305adb93077cabf5567
      # Prowlarr
      - PROWLARR_URL=http://192.168.2.130:9696/prowlarr
      - PROWLARR_API_KEY=ac89dddd92ae4a119d7a3be1e50476b8
      # qBittorrent
      - QBIT_URL=http://192.168.2.130:8080
      - QBIT_USER=admin
      - QBIT_PASS=changeme
      - QBIT_SAVE_PATH=/downloads/servarr-downloads
      # VGMDB
      - VGMDB_URL=http://192.168.2.172:8008
      # MusicBrainz
      - MB_USER_AGENT=LidarrHelper/1.0 (kwasi@local)
      # Discord webhooks
      - DISCORD_WEBHOOK_ARTIST=https://discord.com/api/webhooks/...
      - DISCORD_WEBHOOK_ENRICH=https://discord.com/api/webhooks/...
      - DISCORD_WEBHOOK_MB_SEED_DL=https://discord.com/api/webhooks/...
      - DISCORD_WEBHOOK_MB_SEED_BEETS=https://discord.com/api/webhooks/...
      # GitHub Gist (for Picard export)
      - GITHUB_TOKEN=ghp_...
      - GIST_ID=826a54380a971b030ea5126d2b99cd86
      # Beets
      - BEET_BIN=/usr/local/bin/beet
      # Schedule (cron format)
      - SCAN_CRON=0 2 * * 0
      - ENRICH_CRON=0 3 * * 0
    volumes:
      - lidarr-helper-data:/data           # persistent state files
      - ~/Music:/music                     # music library (read-write)
      - ~/.local:/home/user/.local:ro      # beets bin + config
    networks:
      - arrs_default

volumes:
  lidarr-helper-data:

networks:
  arrs_default:
    external: true
```

---

## Integration Points

### Lidarr Custom Scripts

The existing `on_artist_add.py` and `on_album_download.py` in Lidarr become **thin HTTP wrappers** that call the helper service API:

**`lidarr-scripts/on_album_download.py`** (new slim version):
```python
#!/usr/bin/env python3
import os, requests

HELPER_URL = os.environ.get('LIDARR_HELPER_URL', 'http://lidarr-helper:8900')

payload = {
    'artist_name':   os.environ.get('lidarr_artist_name', ''),
    'album_title':   os.environ.get('lidarr_album_title', ''),
    'mb_release_id': os.environ.get('lidarr_albumrelease_mbid', ''),
    'track_paths':   os.environ.get('lidarr_addedtrackpaths', '').split('|'),
    'event_type':    os.environ.get('lidarr_eventtype', ''),
}
requests.post(f'{HELPER_URL}/api/v1/enrich/album', json=payload, timeout=600)
```

**`lidarr-scripts/on_artist_add.py`** (new slim version):
```python
#!/usr/bin/env python3
import os, requests

HELPER_URL = os.environ.get('LIDARR_HELPER_URL', 'http://lidarr-helper:8900')

payload = {
    'artist_id':   os.environ.get('lidarr_artist_id', ''),
    'mb_id':       os.environ.get('lidarr_artist_mbid', ''),
    'artist_name': os.environ.get('lidarr_artist_name', ''),
    'artist_path': os.environ.get('lidarr_artist_path', ''),
    'event_type':  os.environ.get('lidarr_eventtype', ''),
}
requests.post(f'{HELPER_URL}/api/v1/artist/fix-path', json=payload, timeout=60)
```

Both scripts are tiny — the logic lives in the container.

### Picard Post-Tagging Action

Replace the current `export_mbids.py` call with:
```
python /config/scripts/picard_trigger.py --artist "%directory%"
```

Where `picard_trigger.py` is a slim wrapper:
```python
#!/usr/bin/env python3
import sys, requests, os

HELPER_URL = os.environ.get('LIDARR_HELPER_URL', 'http://192.168.2.130:8900')
artist_folder = sys.argv[sys.argv.index('--artist') + 1] if '--artist' in sys.argv else ''
requests.post(f'{HELPER_URL}/api/v1/picard/export', json={'artist_folder': artist_folder}, timeout=30)
```

### Caddy Reverse Proxy

Add to `Caddyfile` under `kaneservarr.duckdns.org`:
```caddy
handle /lidarr-helper* {
    reverse_proxy 192.168.2.130:8900
}

# Music search now served under the helper
handle /music-search* {
    reverse_proxy 192.168.2.130:8900
}
```

---

## Implementation Phases

### Phase 1 — Core API (no UI)
> Everything works via API. Lidarr and Picard integration restored.

- [ ] FastAPI app scaffold with config from env vars
- [ ] `LibraryScanner` — port `scan-library.py` logic
- [ ] `ArtistFixer` — port `on_artist_add.py` + `fix_artist_paths.py`
- [ ] `VGMDBMapper` — port VGMDB search (MB URL → catalog → barcode → title)
- [ ] `BeetsEnricher` — port `on_album_download.py` + `beets-enrich.py`
- [ ] `MBLinkChecker` — port `mb_vgmdb_link.py`
- [ ] `Notifier` — Discord webhook sender
- [ ] `PicardExporter` — port `export_mbids.py`
- [ ] JSON data store (read/write mapping/enriched/album_list files)
- [ ] Slim Lidarr wrapper scripts (`on_album_download.py`, `on_artist_add.py`)
- [ ] APScheduler cron jobs (scan + enrich)
- [ ] Dockerfile + docker-compose.yml
- [ ] README with setup instructions

### Phase 2 — Web UI
> Visual interface for everything currently done via SSH.

- [ ] Dashboard with live stats and recent activity
- [ ] Mappings page — browse unmapped, search VGMDB inline, set/skip
- [ ] Enrichment page — run with filters, live progress stream via SSE
- [ ] Library page — browse album_list with search/filter
- [ ] Logs page — enrichment + artist fix history
- [ ] Music Search — migrate existing `music-search.html` into the UI

### Phase 3 — Polish
> Quality of life improvements.

- [ ] Authentication (simple API key header for external access)
- [ ] Export/import mappings (backup and restore `vgmdb_mapping.json`)
- [ ] Dark/light theme
- [ ] Notification settings UI (configure Discord webhooks in the web UI)
- [ ] Health check endpoint (`GET /health`)
- [ ] GitHub Actions CI to build and push Docker image to GHCR

---

## Tech Stack Recommendation

| Layer | Choice | Reason |
|-------|--------|--------|
| **API framework** | FastAPI | Async, auto-docs at `/docs`, Pydantic validation |
| **Task runner** | APScheduler | Lightweight, no Redis needed, cron + background tasks |
| **Web UI** | Jinja2 templates + vanilla JS | No build step, matches existing music-search style |
| **Data store** | JSON files + SQLite | Portable, no separate DB container, easy to inspect |
| **HTTP client** | httpx (async) | Works well with FastAPI async endpoints |
| **Container base** | `python:3.12-slim` | Small, has pip, no unnecessary extras |
| **Process manager** | uvicorn directly | Simple, production-ready for single-service deployments |

---

## Key Design Decisions

**1. JSON files as the source of truth** — `vgmdb_mapping.json`, `enriched_albums.json`, and `album_list.json` remain plain JSON files in a Docker volume. This means the CLI scripts in `/scripts/` still work directly if you SSH in and prefer that workflow.

**2. Background jobs via APScheduler** — enrichment runs are background tasks. The API returns a `job_id` immediately; the client polls `/api/v1/enrich/jobs/{job_id}` or receives live updates via Server-Sent Events.

**3. Slim Lidarr wrapper scripts** — the scripts Lidarr calls stay tiny (< 20 lines). All logic lives in the container. Updating the enrichment logic only requires updating the container image, not reconfiguring Lidarr.

**4. Music volume is read-write** — the enrichment pipeline legitimately modifies the library on disk: `beet` writes tags during imports, `beets-enrich.py` rewrites non-Latin artist tags directly via mutagen, `on_artist_add.py` and `fix_artist_paths.py` rename artist folders, and `scan-library.py`'s cleanup pass deletes empty/audio-less folders. A read-only mount would break all of those. The mount is therefore read-write, and accidental modification is guarded against at the *application* layer instead: destructive operations (folder cleanup, bulk path fixes) support a `dry_run` mode and the cleanup pass is opt-in rather than automatic.

**5. Beets runs inside the container** — beets and the vgmdb plugin are installed in the container image. No dependency on the host's `~/.local/bin/beet`.

**6. Single port** — everything on port `8900`. The proxy routes, web UI, API, and music search all served from one FastAPI app. Caddy routes `/lidarr-helper/*` to it.

---

*This plan assumes the existing workflow documented in `music-pipeline-workflow.md` remains unchanged — the helper service is a packaging of that workflow, not a redesign.*

*Last updated: May 2026 — music mount changed from read-only to read-write (design decision #4).*
