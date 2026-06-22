"""Lidarr API client.

Wraps just the slice of Lidarr's v1 API the helper service uses today:
listing artists, fetching one, updating one (path fix), and fetching an
artist's track files (for the "derive path from mapped track" strategy
in :class:`ArtistFixer`).

The base URL and key come from ``settings.lidarr_url`` /
``settings.lidarr_api_key``. The original scripts hard-coded these; we
centralise here so a wrong URL only needs fixing in one place.
"""

from __future__ import annotations

import logging
from typing import Any

import httpx

from app.config import settings

log = logging.getLogger("lidarr-helper.lidarr")


class LidarrClient:
    """Sync Lidarr v1 API client.

    All methods raise ``httpx.HTTPError`` on transport or HTTP errors —
    callers decide whether to swallow, log, or propagate. There is no
    silent fallback here on purpose: a failed Lidarr update is a real
    failure the user should see.
    """

    def __init__(self, timeout: float = 15.0) -> None:
        self._base = settings.lidarr_url.rstrip("/") + "/api/v1"
        self._headers = {
            "X-Api-Key":    settings.lidarr_api_key,
            "Content-Type": "application/json",
        }
        self._timeout = timeout

    # ── private ─────────────────────────────────────────────────────────
    def _get(self, path: str, **params: Any) -> Any:
        url = f"{self._base}/{path.lstrip('/')}"
        r = httpx.get(url, headers=self._headers, params=params or None, timeout=self._timeout)
        r.raise_for_status()
        return r.json()

    def _put(self, path: str, json_body: Any) -> Any:
        url = f"{self._base}/{path.lstrip('/')}"
        r = httpx.put(url, headers=self._headers, json=json_body, timeout=self._timeout)
        r.raise_for_status()
        return r.json()

    # ── public ──────────────────────────────────────────────────────────
    def list_artists(self) -> list[dict]:
        """Return every artist Lidarr knows about."""
        return self._get("artist")

    def get_artist(self, artist_id: int | str) -> dict:
        """Return the full artist record."""
        return self._get(f"artist/{artist_id}")

    def update_artist(self, artist_id: int | str, artist: dict) -> dict:
        """PUT an updated artist record (used for path fixes)."""
        return self._put(f"artist/{artist_id}", artist)

    def get_track_files(self, artist_id: int | str) -> list[dict]:
        """Return track files Lidarr has registered for an artist.

        Used by the "derive correct artist path from a mapped track file"
        strategy in :class:`ArtistFixer`.
        """
        return self._get("trackfile", artistId=artist_id)
