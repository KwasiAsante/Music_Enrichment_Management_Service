# Lidarr custom-script wrappers

Two slim Python scripts Lidarr's **Connect → Custom Script** feature invokes for the events we care about. Each one is a thin HTTP forwarder to the helper service running at `${MUSIC_LIB_HELPER_URL}` (default `http://music-lib-helper:8900`); all logic lives in the container.

| Script | Lidarr event | Endpoint | Timeout |
| --- | --- | --- | --- |
| `on_album_download.py` | On Import + On Upgrade | `POST /api/v1/enrich/album` | 600s |
| `on_artist_add.py` | On Artist Add | `POST /api/v1/artist/fix-path` | 60s |

Both are **stdlib-only** — no `pip install` needed inside Lidarr's container — and both **always exit 0**. A failed POST is logged but never marks Lidarr's import broken; re-enrichment is always available via the helper's `/api/v1/enrich/run` bulk endpoint.

## Installation

1. **Copy the scripts into Lidarr's scripts folder.** With the default `linuxserver/lidarr` layout that's `/config/scripts/` inside the container. From the host:

       cp lidarr-scripts/*.py /path/to/lidarr/config/scripts/
       chmod +x /path/to/lidarr/config/scripts/*.py

2. **(Optional) Tell the wrappers where the helper lives.** If you're running Lidarr and the helper on the same Docker network with the default service name, nothing to do. Otherwise set `MUSIC_LIB_HELPER_URL` in Lidarr's environment (compose file):

       environment:
         - MUSIC_LIB_HELPER_URL=http://192.168.2.130:8900

3. **Configure each script in Lidarr's UI:**

   - **VGMDB Enrich on Download**
     - Settings → Connect → `+` → Custom Script
     - On Import: ✓  /  On Upgrade: ✓  /  all others: ☐
     - Path: `/config/scripts/on_album_download.py`
     - Click **Test** — should log a `test event — skipping POST`
       message in `/config/scripts/on_album_download.log` and return
       success.

   - **Fix Artist Path on Add**
     - Settings → Connect → `+` → Custom Script
     - On Artist Add: ✓  /  all others: ☐
     - Path: `/config/scripts/on_artist_add.py`
     - Click **Test**.

## Logs

Each script appends to its own log next to the script itself:

    /config/scripts/on_album_download.log
    /config/scripts/on_artist_add.log

These complement the helper service's own activity log at `GET /api/v1/enrich/log` — the wrapper logs show that Lidarr fired the event and what payload went out; the service log shows what happened inside the container.

## Updating

When you bump the helper image, the wrapper scripts in `lidarr-scripts/` rarely need to change — the only thing they have to agree on with the service is the request/response shape, and that's versioned via the `/api/v1/` URL prefix. If the shape ever does change in a breaking way, the helper will accept the old shape for at least one release with a deprecation warning in the activity log.
