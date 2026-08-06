"""Discord webhook sender.

The original scripts each had their own inline ``discord(title, desc, ok)``
helper hard-coded against one webhook URL. This consolidates that into a
single ``Notifier`` with one method per webhook category, reading the
URL from settings.

Sending is best-effort: any HTTP error is logged and swallowed. A
notification failure must never abort the enrichment / fix-path work
that triggered it.
"""

from __future__ import annotations

import logging
from typing import Literal

import httpx

from app.config import settings

log = logging.getLogger("music-lib-helper.notifier")

# Discord embed colours used across the originals: green for success,
# red for failure. Mirrored here so the look is consistent.
_COLOUR_OK = 0x34D399
_COLOUR_ERR = 0xF87171

Category = Literal["artist", "enrich", "mb_seed_dl", "mb_seed_beets"]


def _webhook_for(category: Category) -> str:
    """Resolve a category name to its configured webhook URL (or '' if unset)."""
    return {
        "artist":         settings.discord_webhook_artist,
        "enrich":         settings.discord_webhook_enrich,
        "mb_seed_dl":     settings.discord_webhook_mb_seed_dl,
        "mb_seed_beets":  settings.discord_webhook_mb_seed_beets,
    }[category]


class Notifier:
    """Posts Discord embed messages to category-specific webhooks.

    Construct once and reuse. Each call to :meth:`send` is independent —
    no shared state.
    """

    def __init__(self, timeout: float = 10.0) -> None:
        self._timeout = timeout

    def send(
        self,
        category: Category,
        title: str,
        description: str,
        *,
        success: bool = True,
    ) -> bool:
        """Post one embed to the webhook for ``category``.

        Returns True if the webhook accepted the post, False otherwise
        (including the no-webhook-configured case). Never raises.
        """
        url = _webhook_for(category)
        if not url or "PLACEHOLDER_ME" in url:
            log.debug("skip notify (%s): no webhook configured", category)
            return False

        payload = {
            "embeds": [{
                "title":       title,
                "description": description,
                "color":       _COLOUR_OK if success else _COLOUR_ERR,
            }]
        }
        try:
            r = httpx.post(url, json=payload, timeout=self._timeout)
            r.raise_for_status()
            return True
        except httpx.HTTPError as exc:
            log.warning("discord webhook (%s) failed: %s", category, exc)
            return False
