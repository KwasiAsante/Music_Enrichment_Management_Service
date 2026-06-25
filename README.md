# Music Enrichment Management Service

A self-contained Docker service ("music-lib-helper") that consolidates a
Lidarr/Picard music-library enrichment pipeline into one FastAPI app.
It's tuned for soundtrack/VGM (video game & anime music) collections,
where MusicBrainz metadata is often thin and [VGMDB](https://vgmdb.net)
is the better source.

What it does:

- Scans the music library and tracks which albums have MusicBrainz IDs.
- Resolves albums against VGMDB (MB URL → catalog → barcode → title)
  and lets you record/curate the mapping.
- Runs `beet import` with a custom VGMDB beets plugin to tag albums,
  including rewriting non-Latin artist tags to their English alias.
- Fixes Lidarr artist folder paths that come in kanji/native script.
- Exports MusicBrainz artist IDs for Picard's Post-Tagging Action,
  optionally syncing to a GitHub Gist.
- Sends Discord notifications and runs the above on a weekly schedule.

## Documentation

- [`docs/music-lib-helper-service-plan.md`](docs/music-lib-helper-service-plan.md)
  — full architecture, API reference, and design decisions.
- [`docs/PHASE-1-COMPLETE.md`](docs/PHASE-1-COMPLETE.md) — status
  snapshot of what's built (Phase 1, core API) and what's next (Phase 2,
  the web UI).
- [`lidarr-scripts/README.md`](lidarr-scripts/README.md) — installing
  the Lidarr custom-script wrappers.
- [`picard-scripts/README.md`](picard-scripts/README.md) — installing
  the Picard Post-Tagging Action wrapper.

## Quick start

1. Copy `.env.example` to `.env` and fill in every `PLACEHOLDER_ME`
   (Lidarr/Prowlarr API keys, qBittorrent password, Discord webhooks,
   GitHub token + Gist id).
2. **Drop your custom VGMDB plugin** at `app/beets_plugins/VGMplug.py`
   (rename your `VGMplug_custom.py`). The Dockerfile installs every
   `.py` in that directory into the container's `beetsplug/` namespace
   so beets discovers it via `plugins: - VGMplug` in `config.yaml`. The
   build **fails fast** if the directory has no `.py` files — the
   helper is useless without the plugin.
3. `docker compose up -d --build`
4. Check `GET /health` (reports any env vars still left as
   `PLACEHOLDER_ME`) and `GET /docs` for the interactive API reference.

Point Lidarr's custom scripts and Picard's Post-Tagging Action at the
running container per the READMEs linked above.
