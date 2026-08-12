"""Persisted settings overrides — ``{APP_DATA_DIR}/settings_override.json``.

A user-editable layer on top of ``.env``/environment variables: whatever's
saved here wins over the environment at the *next* process boot (see
``app.config.get_settings``), so the Settings page can actually change
behaviour without requiring someone to find and hand-edit a ``.env`` file
that may not even be mounted writable into the container. Applying a
change still needs a process restart either way — see
``POST /api/v1/settings/restart`` — since every module that reads
``settings`` does so once, at import time; there's no hot-reload
mechanism anywhere in this codebase to layer on top of.

Deliberately reads ``APP_DATA_DIR`` straight from ``os.environ`` rather
than through ``app.config.settings`` — ``settings`` is itself constructed
*from* this file's contents, so going through it here would be circular.
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any

log = logging.getLogger("music-lib-helper.settings_store")


def _override_path() -> Path:
    data_dir = os.environ.get("APP_DATA_DIR", "/data")
    return Path(data_dir) / "settings_override.json"


def read_overrides() -> dict[str, Any]:
    """Everything currently overridden, or ``{}`` if nothing has ever
    been saved (or the file is missing/corrupt — treated the same as
    "nothing saved" rather than an error, since this is just a
    convenience layer, not the source of truth)."""
    path = _override_path()
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text())
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError) as exc:
        log.warning("could not read %s: %s", path, exc)
        return {}


def write_overrides(overrides: dict[str, Any]) -> None:
    """Atomic overwrite — write-to-temp-then-rename, same convention as
    the JSON state files in app.storage.json_store, so a crash mid-write
    can never leave a truncated/corrupt override file behind."""
    path = _override_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(overrides, indent=2, default=str))
    tmp.replace(path)
