"""VGMDB API client.

Talks to the user's local ``vgmdb-api`` instance (configured via
``settings.vgmdb_url``). Three search endpoints — by free-text title, by
catalog number, and by barcode — each returning a normalised list of
:class:`VGMDBHint` dicts.

The local vgmdb-api search endpoint is path-based, e.g.::

    GET {VGMDB_URL}/search/albums/{quoted-query}?format=json

so the query is URL-encoded as a *path segment* (``urllib.parse.quote``),
not as a query parameter.

Failures (network, HTTP, malformed JSON) return an empty list and log a
warning. Callers should treat "no results" and "search failed" the same
way — fall through to the next search strategy.
"""

from __future__ import annotations

import logging
from typing import TypedDict
from urllib.parse import quote

import httpx

from app.config import settings

log = logging.getLogger("music-lib-helper.vgmdb")


class VGMDBHint(TypedDict, total=False):
    """One search result. ``vgmdb_id`` and ``title`` are always present;
    ``catalog``, ``barcode``, and ``date`` may be empty strings."""
    vgmdb_id: str
    title:    str
    catalog:  str
    barcode:  str
    date:     str


def _pick_title(titles: dict) -> str:
    """Pick the best available title from a VGMDB ``titles`` object.

    Priority: English → romanised Japanese → Japanese → ``?``. Mirrors
    the original ``generate-mappings-template.py``.
    """
    return (titles.get("en") or titles.get("ja-latn")
            or titles.get("ja") or "?")


def _hint_from_result(r: dict) -> VGMDBHint:
    """Convert one raw VGMDB API result into a normalised :class:`VGMDBHint`."""
    link = r.get("link", "") or ""
    vgmdb_id = link.replace("album/", "") if link.startswith("album/") else link
    return VGMDBHint(
        vgmdb_id=vgmdb_id,
        title=_pick_title(r.get("titles", {}) or {}),
        catalog=r.get("catalog", "") or "",
        barcode=r.get("barcode", "") or "",
        date=r.get("release_date", "") or "",
    )


class VGMDBClient:
    """Sync VGMDB search client."""

    def __init__(self, timeout: float = 30.0) -> None:
        self._base = settings.vgmdb_url.rstrip("/")
        self._timeout = timeout

    # ── internal ────────────────────────────────────────────────────────
    def _search_albums(self, query: str, *, limit: int) -> list[VGMDBHint]:
        """Hit ``/search/albums/{query}`` and return up to ``limit`` hints."""
        if not query:
            return []
        url = f"{self._base}/search/albums/{quote(query, safe='')}"
        try:
            r = httpx.get(url, params={"format": "json"}, timeout=self._timeout)
            r.raise_for_status()
            results = (r.json().get("results") or {}).get("albums") or []
        except httpx.HTTPError as exc:
            log.warning("vgmdb search %r failed: %s", query, exc)
            return []
        except ValueError as exc:
            log.warning("vgmdb search %r returned invalid JSON: %s", query, exc)
            return []
        return [_hint_from_result(r) for r in results[:limit]]

    # ── public ──────────────────────────────────────────────────────────
    def search(self, query: str, *, limit: int = 5) -> list[VGMDBHint]:
        """Free-text album title search."""
        return self._search_albums(query, limit=limit)

    def search_by_catalog(self, catalog: str, *, limit: int = 5) -> list[VGMDBHint]:
        """Search by catalog number. Returns hints filtered to ones whose
        ``catalog`` field is present (matches that the caller will then
        normalise and compare)."""
        if not catalog:
            return []
        return self._search_albums(catalog, limit=limit)

    def search_by_barcode(self, barcode: str, *, limit: int = 5) -> list[VGMDBHint]:
        """Search by barcode. The vgmdb-api search endpoint accepts a
        barcode string the same way as any other query; we then filter to
        results whose ``barcode`` field exactly matches (and fall back to
        all hits if none match exactly, so the caller still sees candidates).
        """
        if not barcode:
            return []
        hints = self._search_albums(barcode, limit=limit)
        exact = [h for h in hints if h.get("barcode") == barcode]
        return exact or hints
