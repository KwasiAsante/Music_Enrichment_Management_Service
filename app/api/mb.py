"""``/api/v1/mb/*`` — MusicBrainz helper endpoints.

Two read-only lookups:

* ``GET /vgmdb-link/{mb_release_id}`` — does this MB release already
  carry a VGMDB URL relationship? If not and a ``vgmdb_id`` query is
  supplied, also returns a MB release-editor seed URL that pre-fills the
  VGMDB link so the user can one-click-submit it.

* ``GET /artist-alias/{mb_artist_id}`` — resolve a MusicBrainz artist's
  preferred English name using the same primary→any-English→romanised
  fallback chain the rest of the service uses.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Path, Query

from app.core.mb_client import MBClient
from app.core.mb_link import MBLinkChecker
from app.models.mb import ArtistAliasResult, VGMDBLinkResult

log = logging.getLogger("music-lib-helper.api.mb")

router = APIRouter(prefix="/api/v1/mb", tags=["musicbrainz"])


# ── GET /mb/vgmdb-link/{mb_release_id} ─────────────────────────────────────
@router.get("/vgmdb-link/{mb_release_id:path}", response_model=VGMDBLinkResult)
def vgmdb_link(
    mb_release_id: str = Path(..., description="MusicBrainz release id."),
    vgmdb_id: str | None = Query(
        default=None,
        description="Optional. If provided and MB doesn't already have a "
        "link, the response includes a release-editor seed URL pre-filling "
        "the VGMDB link.",
    ),
) -> VGMDBLinkResult:
    result = MBLinkChecker().check(mb_release_id, vgmdb_id=vgmdb_id)
    return VGMDBLinkResult(
        mb_release_id=mb_release_id,
        has_link=result.has_link,
        vgmdb_url=result.vgmdb_url,
        existing_url=result.existing_url,
        seed_url=result.seed_url,
    )


# ── GET /mb/artist-alias/{mb_artist_id} ────────────────────────────────────
@router.get("/artist-alias/{mb_artist_id:path}", response_model=ArtistAliasResult)
def artist_alias(
    mb_artist_id: str = Path(..., description="MusicBrainz artist id."),
) -> ArtistAliasResult:
    mb = MBClient()
    data = mb.get_artist(mb_artist_id) or {}
    return ArtistAliasResult(
        mb_artist_id=mb_artist_id,
        english_name=mb.english_alias(mb_artist_id),
        aliases=data.get("aliases") or [],
    )
