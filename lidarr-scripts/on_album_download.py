#!/usr/bin/env python3
"""Lidarr OnAlbumDownload custom-script wrapper.

This is the slim version. All the actual enrichment logic lives in the
helper service; this script just collects Lidarr's environment variables
and POSTs them to ``${MUSIC_LIB_HELPER_URL}/api/v1/enrich/album``.

Wiring in Lidarr:
    Settings → Connect → Custom Script
      Name:   VGMDB Enrich on Download
      On Import: Yes  /  On Upgrade: Yes  /  others: No
      Path:   /config/scripts/on_album_download.py

Environment:
    MUSIC_LIB_HELPER_URL   default: http://music-lib-helper:8900
                        Override if you run the helper elsewhere on the LAN.

Failure handling:
    Always exits 0. A failed POST is logged but never marks the Lidarr
    import broken — enrichment is a nice-to-have that can be retried
    later via /api/v1/enrich/run.

Stdlib only. No `pip install` required inside Lidarr's container.
"""

from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path

HELPER_URL = os.environ.get("MUSIC_LIB_HELPER_URL", "http://music-lib-helper:8900").rstrip("/")
ENDPOINT   = f"{HELPER_URL}/api/v1/enrich/album"
TIMEOUT    = 600  # seconds — enrichment can take a while
LOG_FILE   = Path("/config/scripts/on_album_download.log")


def _getenv(key: str) -> str:
    """Lidarr is inconsistent about case; check both."""
    return (os.environ.get(key.lower(), "")
            or os.environ.get(key, "")).strip()


def _log(msg: str) -> None:
    line = f"[{datetime.now().isoformat(timespec='seconds')}] {msg}"
    print(line, flush=True)
    try:
        LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except OSError:
        pass


def main() -> int:
    event_type = _getenv("Lidarr_EventType")
    _log(f"event={event_type or '(none)'}")

    # Lidarr fires a synthetic Test event from the Connect UI; skip it
    # locally rather than waste a round-trip.
    if event_type.lower() in ("", "test"):
        _log("test event — skipping POST")
        return 0

    payload = {
        "artist_name":   _getenv("Lidarr_Artist_Name"),
        "album_title":   _getenv("Lidarr_Album_Title"),
        "mb_release_id": _getenv("Lidarr_AlbumRelease_MBId"),
        "track_paths":   [p for p in _getenv("Lidarr_AddedTrackPaths").split("|") if p],
        "event_type":    event_type,
    }
    _log(f"POST {ENDPOINT}  artist={payload['artist_name']!r}  "
         f"album={payload['album_title']!r}  mb={payload['mb_release_id']}")

    req = urllib.request.Request(
        ENDPOINT,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            body = resp.read().decode("utf-8", "replace")
        _log(f"  → {resp.status}: {body[:300]}")
    except urllib.error.HTTPError as exc:
        _log(f"  → HTTP {exc.code}: {exc.read().decode('utf-8', 'replace')[:300]}")
    except urllib.error.URLError as exc:
        _log(f"  → network error: {exc.reason}")
    except Exception as exc:  # noqa: BLE001 — never break Lidarr's import
        _log(f"  → unexpected error: {exc}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
