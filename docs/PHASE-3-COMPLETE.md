# Phase 3 — Complete

Status snapshot for the next conversation. Pair this with
[`music-lib-helper-service-plan.md`](./music-lib-helper-service-plan.md) for
the full architecture and [`PHASE-1-COMPLETE.md`](./PHASE-1-COMPLETE.md) /
[`PHASE-2-COMPLETE.md`](./PHASE-2-COMPLETE.md) for how the API layer and Web
UI stood going into this phase.

This phase grew well past its original four-item scope — what started as
"polish" absorbed a full test suite, three new pages, deployment tooling,
and a mobile pass. Everything below is real and verified, not aspirational.

---

## What's built

### The original Phase 3 checklist — all four done

- [x] **Export/import mappings** — Mappings page → Backup & Restore.
      Downloads `vgmdb_mapping.json` as a timestamped JSON file; restores
      via merge (add/overwrite on top of what's there) or replace
      (confirmation-gated, since it's destructive).
- [x] **Dark/light theme toggle** — sidebar-footer button, `localStorage`
      + `data-theme="light"` on `<html>`, anti-FOUC inline script so a
      returning visitor doesn't see a flash of the wrong theme. All 15
      color tokens re-defined per theme, not just a couple.
- [x] **Notification settings UI** — the four Discord webhook fields live
      on the Settings page (masked, write-only, like every other secret),
      each with a **Send Test** button that posts a real test message
      using whatever's in the input box, saved or not.
- [x] **GitHub Actions CI → GHCR** — `.github/workflows/docker-publish.yml`.
      A `sanity-check` job (installs deps, imports the app, runs the full
      pytest suite) gates a `build-and-push` job that publishes a
      multi-arch (amd64/arm64) image to
      `ghcr.io/kwasiasante/music_enrichment_management_service`.

### New pages

| Page | Path | What it does |
|---|---|---|
| Album Detail | `/library/album` | Front/back cover art (with a flip toggle when both exist), credits (composers/performers/arrangers/lyricists), genres, description, a per-disc tracklist — tags read straight from the files, supplemented by VGMDB/MusicBrainz for whatever the tags don't carry. Never 500s on a bad upstream; degrades to a `warnings` list instead. |
| Help | `/help` | A visual step-by-step workflow guide (0–5, including the optional "find & download" step via Music Search) plus a per-page reference section and an FAQ that calls out the two unrelated "skipped" concepts by name. |
| Settings | `/settings` | View/edit every non-path config value. Secrets are masked and write-only — a GET never echoes a real value back. Saving writes to `{APP_DATA_DIR}/settings_override.json`, which wins over `.env` on the *next* boot; a **Restart Now** button sends a graceful `SIGTERM` and polls `/health` until the app answers again. |

### Library & Mappings, substantially deeper

- **Filters**: folder substring, "mapped via" (manual/auto/import) —
  Library page.
- **Group by artist** (Library) — collapsible per-artist sections,
  collapse-all/expand-all, capped at 2000 albums with a truncation notice.
- **List/grid layout toggle** (Library) — grid mode shows cover art;
  front/back detection works across FLAC, ID3 (MP3), MP4/M4A, and Ogg/Opus,
  via folder-level cover files or embedded pictures.
- **Excluded artists** (Mappings) — a persisted, editable list (seeded
  with the old hardcoded `SKIP_ARTISTS` set) that both the Unmapped list
  and `BeetsEnricher` now read from the same source, so excluding an
  artist actually keeps bulk enrichment from touching them too.
- **Skipped albums view** (Mappings) — the `vgmdb_id="skip"` entries,
  with a Restore action. Distinct from Library's "Skipped" view
  (low-confidence auto-matches) — the FAQ on the Help page exists
  specifically because this is genuinely confusing otherwise.
- **Bulk actions** — checkboxes + a select-all + a toolbar, on both
  pages. Mappings: Skip Selected, Exclude Artist(s). Library: Re-enrich
  Selected, Exclude Artist(s) (shared endpoint with Mappings). Bulk
  re-enrich needed **no new backend endpoint** — passing the same
  album-name list to the existing `redo` and `album` filters on
  `POST /enrich/run` already scopes a run to exactly the selected albums.
- **"Download Everything"** (Settings) — one ZIP with every JSON state
  file, the SQLite database, and a manifest. Secrets in
  `settings_override.json` are redacted to a `was_configured` marker,
  never included in cleartext.

### Site-wide

- **`URL_BASE`** — same idea as Sonarr/Radarr/Lidarr's URL Base. The
  whole app (UI, API, static assets) serves itself under a configurable
  path prefix for reverse-proxy subpath deployment, no rewriting needed.
  Every hardcoded internal link across every template and JS file was
  audited and prefixed — the auto-`fetch()`-patching bootstrap
  (`url-base.js`) covers the ~20 `fetch()` call sites; the rest (dynamic
  `href`/`src` construction, template links) were done by hand.
- **Collapsible sidebar** (desktop) — icon-only collapse, persisted.
- **Mobile nav** — below 860px, the sidebar becomes a proper off-canvas
  drawer (hamburger + backdrop + Escape-to-close) instead of a fixed
  220px column eating half a phone screen. Layout sweep across every
  page for wrapping/stacking at narrow widths.
- **Nav reordered** to match the actual workflow (Dashboard → Music
  Search → Mappings → Enrich → Library → Logs → Help → Settings).

### Test suite (new this phase)

`tests/` — 145 tests, pytest, CI-enforced. The load-bearing piece is
`tests/conftest.py`: this app has module-level singletons
(`app.config.settings`, `app.storage.json_store.store`) that don't
isolate the way a naive "just set an env var" test would expect — the
fixtures there mutate those shared objects' attributes in place instead
of trying to replace them, and reset every `Settings` field to its true
class default before each test so the suite doesn't silently depend on
whatever `.env` happens to exist on disk wherever it's run. Verified
hermetic by physically removing `.env` and re-running — twice, in two
separate sessions, after finding two different ways it could still leak.

Coverage is deliberately uneven: routing, auth, the settings
override/restart flow, cover-art parsing across every format, the
VGMDB/MusicBrainz aggregator's failure handling, and mapping/library CRUD
are all well-covered. `beets_enricher.py`, `library_scanner.py`, and
`artist_fixer.py`'s deeper logic sit at 13–18% — they need a real `beet`
binary and a populated library to test meaningfully, which is
integration-test territory, not something worth faking with three layers
of mocks.

### Deployment

- `docker-compose.yml` — pull-by-default (`image:` + `build:` together,
  Compose's documented pattern), no hard dependency on a pre-existing
  external Docker network.
- `requirements-dev.txt`, `pytest.ini` — kept out of the production image
  via `.dockerignore`.
- `README.md` — a genuine two-`curl`-commands quick-deploy path, a
  "Running tests" section, and an accurate Web UI page table (was stale
  by two pages before this phase).

---

## Real bugs found and fixed this phase

Called out separately because several of these were caught *by* the
testing work this phase added, not found first and then tested —
worth knowing the process actually worked, not just that boxes got
checked.

- **`db.add_activity()` could fail a request that had already
  succeeded.** A Settings save wrote its override file fine, then 500'd
  anyway because the *audit-log* write afterward hit a permissions
  issue. Fixed at the source (swallows its own `sqlite3.Error`) since
  the same unguarded pattern existed at 21 call sites — reproduced by
  chmod'ing the db file read-only and confirming the exact failure,
  then confirming the fix.
- **VGMDB `/album/{id}` requests were getting 403'd** — no `User-Agent`
  header, while `VGMplug.py` (the beets plugin) sends a browser-spoofed
  one on every request and works fine. Matched it.
- **`JsonFile`'s missing-file default silently discarded any non-empty
  seed content** — always returned a truly empty `{}`/`[]` regardless of
  what `default` actually held, which would have made the excluded-
  artists seed list vanish on first read. Fixed to deep-copy the real
  default.
- **A CSS specificity bug hid collapsed groups in grid view** — the
  grid-mode `display: grid` rule and the collapsed-group
  `display: none` rule had identical specificity, and grid happened to
  be declared later. Fixed with `:not(.collapsed)` rather than reordering
  rules, which would've just moved the landmine.
- **The Settings-page "success" message could erase itself.** The bulk
  toolbar's "hide when nothing's selected" logic also cleared the result
  banner — which fired immediately after every successful bulk action,
  since clearing selection is exactly what a success does. Found on the
  Library page, traced back to being **already present and shipped** on
  the Mappings page.
- **The light/dark theme toggle's CSS half never actually shipped** —
  the button and JS landed in one pass; the `html[data-theme="light"]`
  variable block that makes it do anything visually didn't. Caught
  mid-mobile-pass while touching the same hover-color lines for an
  unrelated reason.
- **Mobile nav rendered as a tall empty column instead of a top bar** —
  `body { display: flex }` at the base level makes every direct child a
  row-flex item; `.sidebar` gets pulled out of that via
  `position: fixed`, but `.mobile-topbar` didn't, so it stayed a flex
  sibling of `.main` and got stretched to full viewport height by
  `align-items: stretch`. Caught from a real screenshot, not testing —
  see "Known gaps" below for why.

---

## Verification status

Same standard as the last two phases: checked against the real repo,
`TestClient`-driven where it's an HTTP concern, real (mocked-network)
`httpx`/`mutagen` calls where it's a parsing/integration concern.

| Area | What was verified |
|---|---|
| Cover art | Front/back classification across real files, mocked FLAC/ID3/MP4/Ogg objects, and the actual picture-type numbering (front=3, back=4) |
| Album Detail aggregator | Tag+VGMDB merge, VGMDB-only tracklist fallback, MB genre/tag dedup, total network failure, and a garbage-shaped payload — none of them crash |
| Settings | Secret masking round-trip, override-layering precedence, restart-pending detection (computed by comparing live vs. saved state, not a flag), the restart endpoint's delayed SIGTERM (with `os.kill` mocked so the test process survives) |
| Backup export | Real ZIP, real content, secret redaction confirmed at the raw-byte level (not just the parsed JSON) |
| Bulk actions | Exact payload shapes, client-side artist dedup, partial-failure handling, and — after the bug above — that success messages actually persist |
| URL_BASE | Every `href`/`src` on every rendered page regex-audited and confirmed prefixed or genuinely external; `fetch()` patching confirmed to skip already-prefixed and absolute URLs (no double-prefixing) |
| Mobile | CSS structure (breakpoint contains the rules it's supposed to) and JS behavior (hamburger/backdrop/Escape all correctly toggle state) — see below for what this *doesn't* cover |
| Full suite | 145/145 passing with `.env` both present and physically absent |

---

## Known gaps (not fixed, flagged on purpose)

- **No real-device mobile check.** jsdom doesn't run an actual layout
  engine — it resolves CSS cascade/specificity but doesn't compute real
  flexbox pixel results, and doesn't evaluate `@media` queries against a
  settable viewport width at all. The mobile-topbar stretching bug above
  is direct proof this matters: automated testing verified the CSS
  *rules* were structurally correct and still missed the actual
  rendering consequence. A phone or browser dev tools pass would catch
  anything else in the same category.
- **Backup is export-only.** No restore-from-ZIP. A wholesale
  state-overwrite is a meaningfully different risk than the
  mapping-only import that already exists (which touches one file); left
  for a dedicated conversation about what the right safety rails should
  look like rather than rushed.
- **GHCR package visibility.** One manual step outside the codebase —
  GHCR packages default to private even on a public repo. The first
  published image needs its visibility flipped to public, or
  `docker compose pull` fails for anyone who isn't the repo owner.
- **Coverage gaps** in `beets_enricher.py`/`library_scanner.py`/
  `artist_fixer.py` — see Test suite, above. Not a blind spot, a
  deliberate boundary.

---

## How to brief the next conversation

> I'm continuing work on the Music Enrichment Management Service
> (Music Library Helper). Phases 1, 2, and 3 are complete. Please read
> `docs/PHASE-1-COMPLETE.md`, `docs/PHASE-2-COMPLETE.md`,
> `docs/PHASE-3-COMPLETE.md`, and `docs/music-lib-helper-service-plan.md`
> in this workspace folder — [whatever's actually next].
