#!/usr/bin/env python3
"""Lidarr OnArtistAdd custom-script wrapper.

Slim version. All path-fixing logic lives in the helper service; this
script just POSTs Lidarr's environment to ``/api/v1/artist/fix-path``.

Wiring in Lidarr:
    Settings → Connect → Custom Script
      Name:    Fix Artist Path on Add
      On Artist Add: Yes  /  all others: No
      Path:    /config/scripts/on_artist_add.py

Environment:
    LIDARR_HELPER_URL   default: http://lidarr-helper:8900

Failure handling: always exits 0. See on_album_download.py for the
rationale. Stdlib only.
"""

from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path

HELPER_URL = os.environ.get("LIDARR_HELPER_URL", "http://lidarr-helper:8900").rstrip("/")
ENDPOINT   = f"{HELPER_URL}/api/v1/artist/fix-path"
TIMEOUT    = 60
LOG_FILE   = Path("/config/scripts/on_artist_add.log")


def _getenv(key: str) -> str:
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

    if event_type.lower() in ("", "test"):
        _log("test event — skipping POST")
        return 0

    payload = {
        "artist_id":   _getenv("Lidarr_Artist_Id"),
        "mb_id":       _getenv("Lidarr_Artist_MBId"),
        "artist_name": _getenv("Lidarr_Artist_Name"),
        "artist_path": _getenv("Lidarr_Artist_Path"),
        "event_type":  event_type,
    }
    _log(f"POST {ENDPOINT}  artist={payload['artist_name']!r}  "
         f"mb={payload['mb_id']}  path={payload['artist_path']!r}")

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
    except Exception as exc:  # noqa: BLE001
        _log(f"  → unexpected error: {exc}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
