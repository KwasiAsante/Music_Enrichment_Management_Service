#!/usr/bin/env python3
"""Picard Post-Tagging Action wrapper.

Picard invokes this after every album tag-save with the folder Picard
just wrote into as ``%folderpath%``. The wrapper forwards that to the
helper service's ``/api/v1/picard/export`` endpoint, which extracts the
MB album-artist id from the audio tags, upserts ``artists_mbids.json``,
and optionally syncs it to a private GitHub Gist.

Wiring in Picard:
    Options → Plugins (or Options → File naming/Scripts → Post-Tagging
    Actions, depending on version):

        python "/config/scripts/picard_trigger.py" --artist "%folderpath%"

    Use ``%folderpath%`` (the full path to the folder), **not**
    ``%directory%`` — that token is only the folder *name* (e.g.
    ``CD 01``), which the helper cannot resolve. For multi-disc albums
    the path may point at a disc subfolder; the helper walks up to the
    artist folder.

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


def _looks_like_bare_folder_name(raw: str) -> bool:
    """True when ``raw`` is a single path component (no directory separators).

    Picard's ``%directory%`` expands to just the folder name — e.g.
    ``CD 01`` — which the helper cannot locate on disk.
    """
    if not raw or raw in {".", ".."}:
        return False
    normalized = raw.replace("\\", "/")
    return "/" not in normalized and not Path(raw).is_absolute()


def main() -> int:
    raw = _parse_artist(sys.argv)
    if not raw:
        _log("no --artist value provided — nothing to do")
        return 0

    if _looks_like_bare_folder_name(raw):
        _log(
            f"WARNING: {raw!r} looks like a folder name, not a path — "
            "use %folderpath% in Picard, not %directory%"
        )

    # Forward Picard's %folderpath% as-is. The helper walks up from disc
    # or album subfolders to the artist folder under synced_music/Artist/.
    artist_folder = raw

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
