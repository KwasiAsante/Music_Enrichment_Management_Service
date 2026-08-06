# Phase 2 — Complete

Status snapshot for the next conversation. Pair this with
[`lidarr-helper-service-plan.md`](./lidarr-helper-service-plan.md) for the
full architecture and [`PHASE-1-COMPLETE.md`](./PHASE-1-COMPLETE.md) for
where the API layer stood going into this phase.

---

## What's built

Every Phase 2 checklist item from the plan is implemented and verified
against the real codebase (`TestClient`-driven, not just read-through).

### Pages (`app/ui/templates/`, `app/ui/static/js/`)

| Page | Path | Real data | Live actions |
|---|---|---|---|
| Dashboard | `/` | `library/stats` equivalent + last 15 `activity_log` rows (filterable by category) | Scan Library (+ opt-in cleanup), Fix Artist Paths, Run Enrichment |
| Mappings | `/mappings` | `VGMDBMapper.list_unmapped()` | Artist filter (debounced), Search VGMDB, Set, Skip |
| Enrich | `/enrich` | Recent `enrich`-category activity | Run form (artist/album/redo filters, redo-skipped, dry-run) → live job polling → log view |
| Library | `/library` | `app.api.library.list_albums()`, reused directly (not reimplemented) | Artist filter, unmapped-only, pagination |
| Logs | `/logs` | `app.api.logs.list_logs()` (new — see below) | Category/level/artist filters |
| Music Search | `/music-search` | — | MB search → add to Lidarr; Prowlarr/Nyaa search → send to qBittorrent |

`base.html` + `base.css` carry the shared sidebar, design tokens, and
component library (`.btn*`, `.badge*`, `.result-card`, `.info*`, `.log*`,
`.cf`/`.filter-bar`, `.empty*`) every page builds on — extracted from the
original `music-search.html`, not invented fresh.

### New/changed API surface

```
GET  /api/v1/logs/          # new — generic activity-log browsing,
                             #       filters: category, level, artist, limit
```
`app/storage/db.py`'s `list_activity()` gained a `level` filter (same
pattern as its existing `category`/`artist` filters) to support it.

`/proxy/prowlarr/*`, `/proxy/lidarr/*`, `/proxy/qbit/*` — same paths as
Phase 1, but now:
- inject real credentials server-side (`settings.lidarr_api_key` /
  `prowlarr_api_key` / `qbit_user` / `qbit_pass`) on every request,
  stripping whatever the client sent instead of trusting it
- require the same login as the rest of the UI

`GET /music-search` moved from `app.api.proxy` (a `FileResponse` over a
static file) to `app.ui.router` (a real Jinja template).

### Auth (`app/ui/auth.py`)

HTTP Basic Auth, one shared username/password
(`WEB_UI_USER`/`WEB_UI_PASS`), timing-safe comparison. Applied via
`dependencies=[Depends(require_login)]` on both `app.ui.router` (the six
pages) and `app.api.proxy` (since Music Search's actual download flow
lives there — gating the page alone wasn't enough). Refuses every request
while `WEB_UI_PASS` is still the `PLACEHOLDER_ME` sentinel, rather than
allowing a guessable default.

**Deliberately still open:** `/api/v1/*` and `/health`. Two real
integrations can't answer a login prompt —
`lidarr-scripts/on_album_download.py` calls `POST /api/v1/enrich/album`
directly from the Lidarr host, and Docker's healthcheck hits `/health`
with plain `curl`. Someone with API knowledge and LAN access can still
reach the API directly; that's an accepted trade-off for a
single-operator, LAN-only deployment, not an oversight.

### A security fix that fell out of the Music Search conversion

The original `music-search.html` shipped a real Lidarr API key, a real
Prowlarr API key, and a real qBittorrent **password** as plaintext default
HTML attribute values — readable via view-source, and headed for this
repo's git history. All four now live only in `.env`, injected
server-side by `app/api/proxy.py`. Verified with a mocked `httpx` client
that client-supplied credentials never reach the upstream request,
regardless of what's sent. **`app/ui/static/music-search.html` (the old
file) should be deleted** — it's dead code that still contains the leaked
values until removed.

---

## Before you use it

1. **Set `WEB_UI_USER`/`WEB_UI_PASS` in `.env`.** Every page 401s until
   this is a real value, not the placeholder.
2. **Delete `app/ui/static/music-search.html`** if it's still present —
   superseded by `app/ui/templates/music_search.html`, and still contains
   the old leaked credentials until removed.
3. `docker compose up -d --build` — no Dockerfile/`.dockerignore` changes
   were needed; `COPY app/ ./app/` already picks up everything under
   `app/ui/`.
4. Open `http://<host>:8900/` and log in.

(`README.md` also documents a no-Docker local-dev path, including the
`BEETSDIR`-must-be-a-real-env-var gotcha — see "Running without Docker".)

---

## Verification status

Everything above was checked against the real repo with `TestClient` —
mocked upstreams (Lidarr/Prowlarr/qBittorrent/beets), real routers,
models, and storage layer. Highlights:

| Area | What was verified |
|---|---|
| Every page | Renders 200, correct template hooks present, no leaked secrets in page source |
| Mappings | Search/Set/Skip payload shapes match `SearchRequest`/`SetMappingRequest` exactly; artist filter narrows results correctly |
| Enrich | Full run → poll → log cycle against real job storage; field names match `EnrichJobStatus` exactly |
| Library | 60-album fixture — pagination split, artist filter, unmapped filter all correct |
| Logs | Category/level/artist filters each independently verified against seeded real activity rows |
| Dashboard | Scan (including a real error path — no music folder in-sandbox), Fix Artist Paths (including a real error path — Lidarr unreachable), Enrichment quick-action, category-filtered activity refresh |
| Proxy credential injection | Mocked `httpx.AsyncClient.request` — confirmed attacker-supplied `apikey`/qBittorrent credentials never reach the upstream call, regardless of what's sent |
| Auth | Placeholder password → always 401; wrong password → 401; correct → 200 on every UI page *and* every `/proxy/*` route; `/api/v1/*` and `/health` confirmed to stay open throughout |

Not verified (needs you, not sandboxable): the real `beet` CLI, real
Lidarr/Prowlarr/qBittorrent instances, and the credential injection
working end-to-end against your actual `.env` values rather than mocks.

---

## Known, deliberate non-gaps

- **Enrichment progress is polling (1.5s), not SSE.** The Phase 2
  checklist bullet says "via SSE," but the plan's own Key Design
  Decisions section frames polling and SSE as interchangeable ("polls...
  or receives... via SSE"), and the API Endpoints spec only ever defines
  the polling endpoint — no SSE route exists anywhere in the concrete
  spec. Left as polling; the only real difference for a single-operator
  LAN tool is latency.
- **`/api/v1/*` stays unauthenticated** — see Auth, above. Known, not
  forgotten.

---

## Phase 3 — Polish (next conversation, if wanted)

From the plan, adjusted for what Phase 2 already covered:

- [x] ~~Authentication~~ — done in Phase 2 (HTTP Basic, not the
      API-key-header the plan originally sketched, but the same intent)
- [ ] Export/import mappings (backup/restore `vgmdb_mapping.json`)
- [ ] Dark/light theme toggle (currently dark-only by design choice)
- [ ] Notification settings UI (configure Discord webhooks in-app)
- [x] Health check endpoint — already existed since Phase 1
- [ ] GitHub Actions CI → GHCR

None of these were Phase 2 scope; none are blocking.

---

## How to brief the next conversation

> I'm continuing work on the Music Enrichment Management Service
> (Music Library Helper). Phases 1 and 2 are complete. Please read
> `docs/PHASE-1-COMPLETE.md`, `docs/PHASE-2-COMPLETE.md`, and
> `docs/lidarr-helper-service-plan.md` in this workspace folder — we're
> starting on Phase 3 polish (or: [whatever's actually next]).
