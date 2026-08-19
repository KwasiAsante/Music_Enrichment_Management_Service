"""MBLinkChecker — ported from ``mb_vgmdb_link.py``.

Two jobs:

1. Decide whether a MusicBrainz release already carries a VGMDB URL
   relationship.
2. When it doesn't, build a *seed URL* — a MusicBrainz release-editor
   URL that pre-fills the VGMDB external-link form, so the user can
   submit the new relationship with one click.

The seed-URL trick lets the rest of the pipeline (Notifier → Discord)
nudge the user to "complete the round trip" — every successful VGMDB
enrichment posts a seed URL so MB gets richer over time too.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from urllib.parse import urlencode

from app.core.mb_client import MBClient

log = logging.getLogger("music-lib-helper.mb_link")

# MB relationship type UUID for the "VGMdb" link on releases. Hard-coded
# because it's a fixed identifier in the MusicBrainz schema, not config.
VGMDB_REL_UUID = "6af0134a-df6a-425a-96e2-895f9cd342ba"


@dataclass(slots=True)
class LinkCheckResult:
    """Outcome of :meth:`MBLinkChecker.check`."""
    has_link:     bool
    vgmdb_url:    str | None  # the VGMDB album URL (existing or proposed)
    existing_url: str | None  # the URL MB already has, if any
    seed_url:     str | None  # MB editor URL that adds the link, or None
                              # if already linked or no vgmdb_id given


class MBLinkChecker:
    """Checks for, and helps add, a release's MB↔VGMDB URL relationship."""

    def __init__(self, mb: MBClient | None = None) -> None:
        self.mb = mb or MBClient()

    # ── public ──────────────────────────────────────────────────────────
    def check(
        self,
        mb_release_id: str,
        vgmdb_id: str | None = None,
    ) -> LinkCheckResult:
        """Inspect ``mb_release_id`` and (optionally) build a seed URL.

        * If MB already has any URL relation pointing to ``vgmdb.net`` /
          ``vgmdb.info``, returns ``has_link=True`` and that URL.
        * Otherwise, if ``vgmdb_id`` is supplied *and* the release's MB
          status is ``Official``, also builds a seed URL pre-filling the
          VGMDB album URL for the user to one-click-submit. MB editing
          guidelines reserve external database relationships for the
          release they actually describe, and VGMDB itself only catalogues
          official commercial releases — so a Promotion / Bootleg /
          Pseudo-Release / Withdrawn / Cancelled release (or one whose
          status can't be determined) never gets a seed URL.
        * If ``vgmdb_id`` is not supplied and MB has no link, returns
          ``has_link=False`` with everything else None.
        """
        if not mb_release_id or mb_release_id.startswith("vgmdb-"):
            return LinkCheckResult(False, None, None, None)

        data = self.mb.get_release(mb_release_id, includes="url-rels") or {}
        existing = self._existing_vgmdb_url(data)
        if existing is not None:
            return LinkCheckResult(
                has_link=True,
                vgmdb_url=existing,
                existing_url=existing,
                seed_url=None,
            )

        if not vgmdb_id:
            return LinkCheckResult(False, None, None, None)

        status = (data.get("status") or "").strip().lower()
        if status != "official":
            log.info(
                "Not offering a VGMDB seed URL for MB release %s: "
                "status is %r, not Official",
                mb_release_id, data.get("status"),
            )
            return LinkCheckResult(False, None, None, None)

        vgmdb_url = f"https://vgmdb.net/album/{vgmdb_id}"
        seed_url = self._build_seed_url(mb_release_id, vgmdb_url)
        return LinkCheckResult(
            has_link=False,
            vgmdb_url=vgmdb_url,
            existing_url=None,
            seed_url=seed_url,
        )

    # ── internal ────────────────────────────────────────────────────────
    @staticmethod
    def _existing_vgmdb_url(data: dict) -> str | None:
        """Return the first vgmdb.net / vgmdb.info URL relation, or None."""
        for rel in data.get("relations", []) or []:
            resource = ((rel.get("url") or {}).get("resource") or "")
            if "vgmdb.net" in resource or "vgmdb.info" in resource:
                return resource
        return None

    @staticmethod
    def _build_seed_url(mb_release_id: str, vgmdb_url: str) -> str:
        """Build the MB release-editor seed URL that pre-fills a VGMDB link."""
        params = urlencode({
            "edit-release.url.0.text":         vgmdb_url,
            "edit-release.url.0.link_type_id": VGMDB_REL_UUID,
            "edit-release.edit_note":
                f"Adding VGMDB link: {vgmdb_url} "
                "— auto-detected by MusicLibHelper",
        })
        return f"https://musicbrainz.org/release/{mb_release_id}/edit?{params}"
