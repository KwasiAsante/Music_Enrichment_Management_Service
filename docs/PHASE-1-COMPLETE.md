# Phase 1 — Complete

Status snapshot for the next conversation. Pair this with
[`lidarr-helper-service-plan.md`](./lidarr-helper-service-plan.md) for
the full architecture.

---

## What's built

Every Phase 1 checklist item from the plan is implemented and unit-tested.

### Core services (`app/core/`)
| Module | Replaces | Notes |
|---|---|---|
| `library_scanner.py` | `scan-library.py` | `LibraryScanner.scan(dry_run, cleanup)`. Cleanup opt-in, non-interactive, gracefully degrades on read-only mount. |
| `artist_fixer.py` | `on_artist_add.py` + `fix_artist_paths.py` | Three-strategy chain: `mapped_track` → `mb_id_map` → `mb_alias_rename`. `fix_one`, `fix_all`, `suggest_all`. |
| `vgmdb_mapper.py` | `generate-mappings-template.py` + `map-vgmdb.py` | Four-step search: MB URL → catalog → barcode → title. Plus CRUD over `vgmdb_mapping.json`. |
| `beets_enricher.py` | `beets-enrich.py` + `on_album_download.py` | `enrich_album(allow_search=…)` + `run_bulk(...)`. Should-enrich heuristic, match-% parsing, non-Latin tag rewrite, MB seed URLs. |
| `mb_link.py` | `mb_vgmdb_link.py` | `MBLinkChecker.check()`. |
| `picard_export.py` | `export_mbids.py` | `export_one(folder)` + `export_all()`. Optional Gist sync. |
| `notifier.py` | (new) | Discord embed sender, one method per webhook category. |
| `mb_client.py` | (new) | MusicBrainz client with on-disk caching + 1.1s rate limit. |
| `lidarr_client.py` | (new) | Lidarr v1 API wrapper. |
| `vgmdb_client.py` | (new) | Local vgmdb-api search client. |

### Storage (`app/storage/`)
- `json_store.py` — atomic-write wrapper over the five JSON state files
  (`album_list`, `vgmdb_mapping`, `enriched_albums`, `mb_artist_cache`,
  `skipped_albums`, plus the Picard export's `artists_mbids`). One
  module-level `store` singleton.
- `db.py` — SQLite for `jobs` (operational history) and `activity_log`
  (chronological feed for the Logs page). Sync `sqlite3` + WAL mode.

### API routers (`app/api/`)
| Router | Endpoints |
|---|---|
| `library` | `POST /scan`, `GET /albums`, `GET /stats` |
| `artist` | `POST /fix-path`, `POST /fix-all-paths`, `GET /paths` |
| `mapping` | `GET /`, `GET /unmapped`, `POST /search`, `PUT /{id}`, `DELETE /{id}` |
| `enrich` | `POST /album`, `POST /run` (bg thread), `GET /jobs/{id}`, `GET /log` |
| `mb` | `GET /vgmdb-link/{rid}`, `GET /artist-alias/{aid}` |
| `picard` | `POST /export`, `POST /export/full` |
| `proxy` | `/proxy/prowlarr/*`, `/proxy/lidarr/*`, `/proxy/qbit/*`, `/music-search` |

### Scheduler (`app/scheduler.py`)
APScheduler `BackgroundScheduler` with two crontab jobs reading
`SCAN_CRON` and `ENRICH_CRON` from settings. Empty string disables a
job. Each fire creates a `jobs` row and writes an activity-log
summary.

### External wrapper scripts
| Path | Caller | Endpoint |
|---|---|---|
| `lidarr-scripts/on_album_download.py` | Lidarr OnImport/OnUpgrade | `POST /api/v1/enrich/album` |
| `lidarr-scripts/on_artist_add.py` | Lidarr OnArtistAdd | `POST /api/v1/artist/fix-path` |
| `picard-scripts/picard_trigger.py` | Picard Post-Tagging Action | `POST /api/v1/picard/export` |

All three are stdlib-only and always exit 0.

### Docker
- `Dockerfile` (python:3.12-slim + ffmpeg + beets + custom VGMplug + tini)
- `docker-compose.yml` (env_file `.env`, named `lidarr-helper-data` volume, music **bind-mount read-write** per design-decision #4, external `arrs_default` network, healthcheck on `/health`)
- `.env.example` (every variable documented; `.env` already exists with placeholders)

---

## Before you `docker compose up --build`

1. **Fill in secrets.** Open `.env` and replace every `PLACEHOLDER_ME`:
   `LIDARR_API_KEY`, `PROWLARR_API_KEY`, `QBIT_PASS`, the four Discord
   webhook URLs, `GITHUB_TOKEN`, `GIST_ID`. The `/health` endpoint
   reports which fields are still placeholder.

2. **Drop your custom beets plugin into place.** Rename your
   `VGMplug_custom.py` to `VGMplug.py` and put it in
   `app/beets_plugins/`. The Dockerfile dynamically locates the
   `beetsplug` namespace package inside site-packages and copies every
   `.py` file from that directory in, overriding any module of the
   same name. **The build fails fast if no `.py` files are present.**
   The `beets-vgmdb` pip package is intentionally NOT installed, to
   avoid shadowing your custom file.

3. **(Optional) Drop `music-search.html` into `app/ui/static/`** if you
   want `/music-search` to serve it. Missing file → 404 with a clear
   message, nothing else breaks.

4. **(Optional) Create the external network** if you're not on the
   *arr stack's existing one:

       docker network create arrs_default

5. **`docker compose up -d --build`** and check `/health` and `/docs`.

---

## Phase 2 — Web UI (next conversation)

From the plan, in priority order:

- [ ] Base Jinja2 layout (header, nav, footer) + minimal CSS
- [ ] Dashboard (`/`) — live stats from `/library/stats` + recent
      `/enrich/log` activity
- [ ] Mappings page (`/mappings`) — browse `/mapping/unmapped`, search
      VGMDB inline via `/mapping/search`, set/skip via `/mapping/{id}`
- [ ] Enrichment page (`/enrich`) — kick off `/enrich/run`, show
      progress via SSE on top of the existing background-thread job model
- [ ] Library page (`/library`) — `/library/albums` with filter/search
- [ ] Logs page (`/logs`) — paginated view over the activity log
- [ ] Wire `app/ui/templates/` and `app/ui/static/` into FastAPI
      (`StaticFiles` + `Jinja2Templates`)
- [ ] Migrate the existing `music-search.html` into the UI

Phase 3 polish items (auth, dark/light theme, mapping export/import,
GHCR CI) are explicitly out-of-scope for the next chat unless we
finish Phase 2 quickly.

---

## Verification status

Every service has a focused TestClient-driven smoke test that I ran
in-session. Coverage:

| Suite | Highlights |
|---|---|
| Storage | Atomic writes, corrupt-file safety, enriched-set contract |
| Library API | scan dry-run + cleanup + writable cleanup, filters, pagination, stats, job recording |
| ArtistFixer | Event ignored, already-Latin skip, Strategy 2/3 hits, folder rename, Discord, fix-all |
| VGMDBMapper | All 3 search-pipeline branches, CRUD, backfill, `skip` sentinel, activity log |
| MB endpoints | Both `vgmdb.net` + `vgmdb.info` link detection, seed URL with rel UUID, alias |
| PicardExporter | New + update + missing folder, gist POST vs PATCH, full-library walk |
| Proxy | URL+query+headers forwarding, cookie pass-through, CORS, OPTIONS preflight, path-traversal block |
| BeetsEnricher | Mapping hit + beet success + seed posted, already-enriched short-circuit, `skip` sentinel, no-map fallback, bulk dry-run, 404 on unknown job |
| Scheduler | Job registration, manual fire, both-empty-crons short-circuit |
| Wrappers | All 3 scripts as subprocesses against a real `BaseHTTPRequestHandler` |

The one thing I couldn't verify in-sandbox is the real `beet` CLI
invocation against a real beets install — `_subprocess_run` was always
mocked. Worth shaking out with a real container build.

---

## Known quirks

- **Cowork mount staleness.** Across this build, in-place edits to
  `app/main.py`, `app/core/library_scanner.py`, and
  `app/core/artist_fixer.py` occasionally lagged behind the host file
  in the sandbox's bash mount — workaround was to reconnect the folder
  via `request_cowork_directory` or reconstruct in `/tmp/`. Host
  files on `D:\` were always correct. **Docker builds and your IDE
  see the real files.** Reopen the folder in Cowork if a new session
  reports stale content.

- **Hardcoded artist allow/block lists.** `BeetsEnricher.SKIP_ARTISTS`
  and `ENRICH_ARTISTS` are class constants — same set as the original
  scripts. If you want to grow them at runtime, that's a Phase 3 item
  (config UI).

- **`subprocess.run` indirection.** `app.core.beets_enricher._subprocess_run`
  is the only call site for the `beet` binary. Tests monkeypatch this
  symbol. Don't replace it with a direct `subprocess.run` import
  elsewhere or tests will silently miss it.

- **MB seed-URL routing.** `enrich_album(seed_category="mb_seed_dl")` is
  the default — Lidarr's OnAlbumDownload hook posts seed URLs to
  `DISCORD_WEBHOOK_MB_SEED_DL`. `run_bulk()` overrides to
  `"mb_seed_beets"` so bulk-run seeds land on
  `DISCORD_WEBHOOK_MB_SEED_BEETS`. If you add a new caller, pick the
  appropriate channel explicitly rather than relying on the default.

- **The plan's design-decision #4 was reversed.** Music mount is now
  read-write (not read-only). The reasoning is in the plan doc itself.

---

## How to brief the next conversation

The repo *is* the documentation. The fastest cold-start prompt:

> I'm continuing work on the Music Enrichment Management Service
> Lidarr helper. Phase 1 is complete. Please read
> `docs/PHASE-1-COMPLETE.md` and `docs/lidarr-helper-service-plan.md`
> in this workspace folder, then we'll start on Phase 2 (the Web UI).

That alone gives the next assistant the full picture without you
having to recap.
