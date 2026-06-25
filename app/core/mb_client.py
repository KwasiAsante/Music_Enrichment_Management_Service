"""MusicBrainz API client.

A thin sync wrapper over the small slice of the MusicBrainz API the
enrichment scripts use:

* :meth:`get_artist`        — fetch an artist record (with aliases),
                              cached on disk in ``mb_artist_cache.json``.
* :meth:`english_alias`     — resolve an artist's preferred English name
                              using the same primary→any-English→
                              romanised-sort-name fallback that
                              ``on_artist_add.py`` and
                              ``fix_artist_paths.py`` both implemented.

Two design choices worth flagging:

* **Caching is shared on-disk.** All MB responses go through the same
  ``store.mb_artist_cache`` JSON file the original ``beets-enrich.py``
  used. That means standalone CLI scripts and the service hit the same
  cache, and the cache survives container restarts.

* **Rate limit is enforced inside the client.** MusicBrainz asks for a
  1 request/second cap. Every uncached request sleeps 1.1s after the
  response, regardless of caller — there is exactly one place to get
  the throttling right.
"""

from __future__ import annotations

import logging
import time
from typing import Any

import httpx

from app.config import settings
from app.storage.json_store import store

log = logging.getLogger("music-lib-helper.mb")

_BASE = "https://musicbrainz.org/ws/2"


class MBClient:
    """Sync MusicBrainz client with on-disk caching."""

    def __init__(self, timeout: float = 10.0, rate_sleep: float = 1.1) -> None:
        self._timeout = timeout
        self._rate_sleep = rate_sleep
        self._ua = settings.mb_user_agent

    # ── public API ──────────────────────────────────────────────────────
    def get_artist(self, mb_artist_id: str, *, force_refresh: bool = False) -> dict | None:
        """Return the full MusicBrainz artist record (with aliases), or None.

        Results are cached in ``mb_artist_cache.json`` keyed by ``mb_artist_id``
        and reused until ``force_refresh=True``. Network or HTTP failures
        return ``None`` rather than raising; callers should treat that as
        "unknown artist" and fall back to other strategies.
        """
        if not mb_artist_id:
            return None

        cache = store.mb_artist_cache.read()
        if not force_refresh and mb_artist_id in cache:
            return cache[mb_artist_id]

        url = f"{_BASE}/artist/{mb_artist_id}"
        params = {"fmt": "json", "inc": "aliases"}
        try:
            r = httpx.get(
                url,
                params=params,
                headers={"User-Agent": self._ua},
                timeout=self._timeout,
            )
            time.sleep(self._rate_sleep)
            if r.status_code != 200:
                log.warning("MB artist %s returned HTTP %s", mb_artist_id, r.status_code)
                return None
            data = r.json()
        except httpx.HTTPError as exc:
            log.warning("MB artist %s fetch failed: %s", mb_artist_id, exc)
            return None
        except ValueError as exc:  # JSON decode error
            log.warning("MB artist %s returned invalid JSON: %s", mb_artist_id, exc)
            return None

        cache[mb_artist_id] = data
        store.mb_artist_cache.write(cache)
        return data

    def english_alias(self, mb_artist_id: str) -> str | None:
        """Return the artist's preferred English name, or None if undetermined.

        Resolution order (mirrors the original scripts):
          1. Primary English alias (``locale == "en"`` and ``primary == true``)
          2. Any English "Artist name" alias
          3. ``sort-name`` flipped to "First Last" if it doesn't contain CJK
        """
        data = self.get_artist(mb_artist_id)
        if not data:
            return None

        aliases: list[dict[str, Any]] = data.get("aliases") or []

        primary_en = next(
            (a["name"] for a in aliases
             if a.get("locale") == "en" and a.get("primary")),
            None,
        )
        if primary_en:
            return primary_en

        any_en = next(
            (a["name"] for a in aliases
             if a.get("locale") == "en" and a.get("type") == "Artist name"),
            None,
        )
        if any_en:
            return any_en

        sort_name = data.get("sort-name") or ""
        if sort_name and not any("　" <= c <= "鿿" for c in sort_name):
            parts = [p.strip() for p in sort_name.split(",")]
            if len(parts) == 2:
                return f"{parts[1]} {parts[0]}"
            return sort_name

        return None

    # ── release-side helpers (no caching — release lookups are infrequent
    # and triggered per-mapping rather than per-artist) ─────────────────
    def get_release(self, mb_release_id: str, *, includes: str = "") -> dict | None:
        """Fetch a MusicBrainz release record. ``includes`` is the comma-
        separated ``inc=`` parameter (e.g. ``"label-rels"``, ``"url-rels"``).
        Returns None on any error.
        """
        if not mb_release_id or mb_release_id.startswith("vgmdb-"):
            return None
        url = f"{_BASE}/release/{mb_release_id}"
        params = {"fmt": "json"}
        if includes:
            params["inc"] = includes
        try:
            r = httpx.get(
                url,
                params=params,
                headers={"User-Agent": self._ua},
                timeout=self._timeout,
            )
            time.sleep(self._rate_sleep)
            if r.status_code != 200:
                log.warning("MB release %s returned HTTP %s",
                            mb_release_id, r.status_code)
                return None
            return r.json()
        except httpx.HTTPError as exc:
            log.warning("MB release %s fetch failed: %s", mb_release_id, exc)
            return None
        except ValueError as exc:
            log.warning("MB release %s returned invalid JSON: %s",
                        mb_release_id, exc)
            return None

    def release_catalog_barcode(
        self, mb_release_id: str
    ) -> tuple[str | None, str | None]:
        """Return ``(catalog, barcode)`` for an MB release. Either may be None.

        Catalog is taken from the first ``label-info`` entry with a
        catalog-number that isn't the placeholder ``[none]``.
        """
        data = self.get_release(mb_release_id, includes="label-rels")
        if not data:
            return None, None
        catalog = None
        for li in data.get("label-info", []) or []:
            cn = (li.get("catalog-number") or "").strip()
            if cn and cn != "[none]":
                catalog = cn
                break
        barcode = data.get("barcode") or None
        return catalog, barcode

    def release_vgmdb_link(self, mb_release_id: str) -> str | None:
        """If the MB release already has a VGMDB URL relationship, return
        the numeric VGMDB id. None otherwise.
        """
        data = self.get_release(mb_release_id, includes="url-rels")
        if not data:
            return None
        for rel in data.get("relations", []) or []:
            resource = ((rel.get("url") or {}).get("resource") or "")
            if "vgmdb.net/album/" in resource:
                vgmdb_id = resource.split("vgmdb.net/album/")[-1].strip("/")
                if vgmdb_id.isdigit():
                    return vgmdb_id
        return None
