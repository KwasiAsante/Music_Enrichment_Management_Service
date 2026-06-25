#!/usr/bin/env python3
"""Picard Post-Tagging Action wrapper.

Picard invokes this after every album tag-save with the artist folder
as ``%directory%``. The wrapper forwards that to the helper service's
``/api/v1/picard/export`` endpoint, which extracts the MB album-artist
id from the audio tags, upserts ``artists_mbids.json``, and optionally
syncs it to a private GitHub Gist.

Wiring in Picard:
    Options → Plugins (or Options → File naming/Scripts → Post-Tagging
    Actions, depending on version):

        python "/config/scripts/picard_trigger.py" --artist "%directory%"

    The `%directory%` token is the folder Picard just wrote into. Its
    *parent* is what we want (the artist folder); the wrapper handles
    that resolution.

Environment:
    MUSIC_LIB_HELPER_URL   default: http://192.168.2.130:8900
                        Picard usually runs on the desktop, not in the
                        Docker network, so set this to the helper's
                        LAN address.

Stdlib only. Always exits 0.
"""

from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path

HELPER_URL = os.environ.get(
    "MUSIC_LIB_HELPER_URL", "http://192.168.2.130:8900"
).rstrip("/")
ENDPOINT = f"{HELPER_URL}/api/v1/picard/export"
TIMEOUT  = 30
LOG_FILE = Path(__file__).resolve().parent / "picard_trigger.log"


def _log(msg: str) -> None:
    line = f"[{datetime.now().isoformat(timespec='seconds')}] {msg}"
    print(line, flush=True)
    try:
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except OSError:
        pass


def _parse_artist(argv: list[str]) -> str:
    """Pick the value following ``--artist``, or fall back to the first
    positional. Returns "" if nothing was given.
    """
    if "--artist" in argv:
        idx = argv.index("--artist")
        if idx + 1 < len(argv):
            return argv[idx + 1].strip()
    # Allow `python picard_trigger.py "/path/to/folder"` as a convenience
    extras = [a for a in argv[1:] if not a.startswith("--")]
    return extras[0].strip() if extras else ""


def main() -> int:
    raw = _parse_artist(sys.argv)
    if not raw:
        _log("no --artist value provided — nothing to do")
        return 0

    # Picard's %directory% is the album folder; the helper expects the
    # artist folder. The exporter is forgiving (it tries both the
    # literal path and falls back to its basename), but climbing one
    # level here makes intent obvious in the logs.
    p = Path(raw)
    artist_folder = str(p.parent) if p.parent.name and p.exists() else raw

    payload = {"artist_folder": artist_folder}
    _log(f"POST {ENDPOINT}  artist_folder={artist_folder!r}")

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
