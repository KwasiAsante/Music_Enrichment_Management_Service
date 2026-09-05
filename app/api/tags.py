"""``/api/v1/tags/*`` — album tag view/edit + per-field enrichment locks.

Distinct from ``/api/v1/overrides`` (see :mod:`app.api.field_overrides`):
an override redirects what the *next* enrichment writes, while a save
here writes the file(s) immediately, and a lock protects a field from
every future enrichment run rather than picking what value lands there.
See :mod:`app.core.tag_editor` and :mod:`app.core.tag_locks`.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query

from app.core.tag_editor import TagEditorService
from app.core.tag_locks import TagLockService
from app.models.tag_editor import (
    AlbumTagsView,
    SetLockRequest,
    SetLockResult,
    UpdateAlbumTagsRequest,
    UpdateAlbumTagsResult,
)

router = APIRouter(prefix="/api/v1/tags", tags=["tags"])


# ── GET /tags/album ──────────────────────────────────────────────────────────
@router.get("/album", response_model=AlbumTagsView)
def get_album_tags(
    folder: str = Query(..., description="An AlbumEntry.folder value."),
) -> AlbumTagsView:
    """Every standard field plus every custom tag currently on the
    album, with current value and lock state."""
    try:
        view = TagEditorService().get_album_tags(folder)
    except ValueError as exc:
        raise HTTPException(404, str(exc)) from exc
    return AlbumTagsView(**view)


# ── PUT /tags/album ──────────────────────────────────────────────────────────
@router.put("/album", response_model=UpdateAlbumTagsResult)
def update_album_tags(
    req: UpdateAlbumTagsRequest,
    folder: str = Query(..., description="An AlbumEntry.folder value."),
) -> UpdateAlbumTagsResult:
    """Write (or, for a field mapped to ``""``, delete) tags on every
    audio file in the album right now. Any field name is accepted — one
    that isn't in the standard set becomes a new custom tag."""
    service = TagEditorService()
    try:
        touched = service.update_album_fields(folder, req.fields)
        view = service.get_album_tags(folder)
    except ValueError as exc:
        code = 404 if str(exc).startswith("no album found") else 400
        raise HTTPException(code, str(exc)) from exc
    return UpdateAlbumTagsResult(folder=folder, files_touched=touched, tags=AlbumTagsView(**view))


# ── PUT /tags/locks ───────────────────────────────────────────────────────────
@router.put("/locks", response_model=SetLockResult)
def set_tag_lock(
    req: SetLockRequest,
    folder: str = Query(..., description="An AlbumEntry.folder value."),
) -> SetLockResult:
    """Lock or unlock one field (standard or custom) against every
    future enrichment run for this album."""
    try:
        fields = TagLockService().set_lock(folder, req.field, req.locked)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return SetLockResult(folder=folder, locked_fields=fields)
