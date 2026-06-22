# Picard Post-Tagging Action wrapper

A tiny stdlib-only Python script Picard runs after every album save.
It POSTs the artist folder to the helper service's
`POST /api/v1/picard/export`, which re-reads the album-artist MB id
from the tags and upserts `artists_mbids.json` (and syncs to a GitHub
Gist if a token is configured).

## Installation

1. **Copy the script onto the machine where Picard runs.** Picard is
   usually a desktop app, not a container, so this goes wherever
   Picard's scripts live on your host. A common convention:

       ~/.config/picard/scripts/picard_trigger.py

   or, if you run Picard in a Docker container with a config mount:

       /config/scripts/picard_trigger.py

2. **Tell the wrapper where the helper lives.** Picard isn't on the
   Docker network, so the default `localhost:8900` won't work. Set
   `LIDARR_HELPER_URL` in Picard's environment (or just edit the
   `HELPER_URL` default in the script):

       export LIDARR_HELPER_URL=http://192.168.2.130:8900

3. **Add the Post-Tagging Action in Picard:**

   - Options → File naming → Post-Tagging actions, OR
   - Options → Scripts → Post-Tagging actions
     (varies by Picard version)

   Add this single command line:

       python "/config/scripts/picard_trigger.py" --artist "%directory%"

   The `%directory%` token expands to the folder Picard just wrote
   into (the album folder). The wrapper climbs one level to get the
   artist folder before forwarding.

## What happens on each tag-save

1. Picard saves edited tags.
2. Picard invokes the wrapper with the album directory.
3. Wrapper POSTs to the helper.
4. Helper reads the first audio file's `MUSICBRAINZ_ALBUMARTISTID` tag,
   upserts the entry in `artists_mbids.json`, and (if `GITHUB_TOKEN`
   and `GIST_ID` are set in `.env`) PATCHes the gist.
5. Tools that consume the gist (your own scripts, external services)
   pick up the new entry on their next refresh.

## Logs

`picard_trigger.log` next to the script. Lines look like:

    [2026-05-19T18:00:00] POST http://192.168.2.130:8900/api/v1/picard/export  artist_folder='/storage/synced_music/Artist/Kenji Kawai'
      → 200: {"ok":true,"artist":"Kenji Kawai","mb_id":"...","is_new":false,"gist_updated":true}

## Failure handling

The wrapper always exits 0. If the helper is unreachable Picard's
tag-save still succeeds — re-running `POST /api/v1/picard/export/full`
later rebuilds the export from scratch.
