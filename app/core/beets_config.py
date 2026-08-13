"""Keep beets' on-disk config aligned with this app's runtime settings.

Beets reads ``BEETSDIR/config.yaml`` directly — including
``VGMplug.baseurl`` — and ignores our pydantic ``settings.vgmdb_url``
unless we sync it here. The Docker image bakes a fallback ``baseurl``
at build time, which drifts from the live ``VGMDB_URL`` env var unless
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
        rf"\g<1>{desired}",
        text,
        count=1,
    )
    config_path.write_text(updated, encoding="utf-8")
    log.info("synced beets VGMplug.baseurl: %s -> %s", current, desired)


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
