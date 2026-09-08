"""Keep beets' on-disk config aligned with this app's runtime settings.

Beets reads ``BEETSDIR/config.yaml`` directly — including
``VGMplug.baseurl`` and ``directory`` — and ignores our pydantic
``settings.vgmdb_url``/``settings.artist_root`` unless we sync them
here. The Docker image bakes fallback values at build time, which
drift from the live env vars / saved Settings-page overrides unless
patched on startup.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path

from app.config import settings

log = logging.getLogger("music-lib-helper.beets_config")


def sync_beets_vgmdb_url() -> None:
    """Write ``settings.vgmdb_url`` into ``VGMplug.baseurl`` if needed."""
    config_path = Path(settings.beetsdir) / "config.yaml"
    if not config_path.is_file():
        log.debug("beets config not found at %s — skipping VGMDB URL sync", config_path)
        return

    desired = settings.vgmdb_url.rstrip("/")
    text = config_path.read_text(encoding="utf-8")
    match = re.search(r"(?m)^(\s*baseurl:\s*)(.+)$", text)
    if not match:
        log.warning("beets config at %s has no VGMplug baseurl line", config_path)
        return

    current = match.group(2).strip().strip("'\"")
    if current == desired:
        return

    updated = re.sub(
        r"(?m)^(\s*baseurl:\s*).+$",
        lambda m: f"{m.group(1)}{desired}",
        text,
        count=1,
    )
    config_path.write_text(updated, encoding="utf-8")
    log.info("synced beets VGMplug.baseurl: %s -> %s", current, desired)


def sync_beets_directory() -> None:
    """Write ``settings.artist_root`` into beets' top-level ``directory``
    if needed — keeps ``config.yaml`` in step with
    ``ARTIST_ROOT_SUBPATH`` (or a Settings-page override) instead of
    each drifting from a value baked into the image at build time."""
    config_path = Path(settings.beetsdir) / "config.yaml"
    if not config_path.is_file():
        log.debug("beets config not found at %s — skipping directory sync", config_path)
        return

    desired = str(settings.artist_root)
    text = config_path.read_text(encoding="utf-8")
    match = re.search(r"(?m)^(directory:\s*)(.+)$", text)
    if not match:
        log.warning("beets config at %s has no top-level directory line", config_path)
        return

    current = match.group(2).strip().strip("'\"")
    if current == desired:
        return

    updated = re.sub(
        r"(?m)^(directory:\s*).+$",
        lambda m: f"{m.group(1)}{desired}",
        text,
        count=1,
    )
    config_path.write_text(updated, encoding="utf-8")
    log.info("synced beets directory: %s -> %s", current, desired)


def validate_beet_bin() -> None:
    """Log loudly when the configured ``beet`` binary is missing."""
    beet_path = Path(settings.beet_bin)
    if beet_path.is_file():
        return
    log.error(
        "beet binary not found at %s — VGMDB enrichment will fail until "
        "BEET_BIN is corrected (Docker: /usr/local/bin/beet; bare-metal: "
        "whatever resolves on PATH)",
        settings.beet_bin,
    )
