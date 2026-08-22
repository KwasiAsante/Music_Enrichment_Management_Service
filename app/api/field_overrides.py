"""``/api/v1/overrides/*`` — per-album manual tag-field override CRUD.

Backs the "Field Overrides" page: search happens against the existing
``GET /api/v1/library/albums`` (see its ``q`` parameter), and these three
endpoints handle one selected album at a time — see
:mod:`app.core.field_overrides` for why this exists and how a saved
override actually takes effect.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query

from app.core.field_overrides import FieldOverrideService
from app.models.field_override import (
    AlbumFieldOptions,
    DeleteFieldOverrideResult,
    FieldOverrideEntry,
    SetFieldOverridesRequest,
)

router = APIRouter(prefix="/api/v1/overrides", tags=["overrides"])


# ── GET /overrides/options ──────────────────────────────────────────────────
@router.get("/options", response_model=AlbumFieldOptions)
def get_field_options(
    folder: str = Query(..., description="An AlbumEntry.folder value."),
) -> AlbumFieldOptions:
    """Current tag values, VGMDB candidate values, and any saved override
    for every overridable field of one album."""
    try:
        options = FieldOverrideService().get_options(folder)
    except ValueError as exc:
        raise HTTPException(404, str(exc)) from exc
    return AlbumFieldOptions(**options)


# ── PUT /overrides ───────────────────────────────────────────────────────────
@router.put("", response_model=FieldOverrideEntry)
def set_field_overrides(
    req: SetFieldOverridesRequest,
    folder: str = Query(..., description="An AlbumEntry.folder value."),
) -> FieldOverrideEntry:
    """Save (or clear, for fields mapped to ``""``) field overrides for
    one album. Takes effect the next time that album is (re-)enriched —
    see :meth:`app.core.field_overrides.FieldOverrideService.apply_overrides`."""
    try:
        entry = FieldOverrideService().set_overrides(folder, req.fields)
    except ValueError as exc:
        code = 404 if str(exc).startswith("no album found") else 400
        raise HTTPException(code, str(exc)) from exc
    return FieldOverrideEntry(**entry)


# ── DELETE /overrides ────────────────────────────────────────────────────────
@router.delete("", response_model=DeleteFieldOverrideResult)
def delete_field_overrides(
    folder: str = Query(..., description="An AlbumEntry.folder value."),
) -> DeleteFieldOverrideResult:
    """Remove every saved override for one album."""
    deleted = FieldOverrideService().delete_overrides(folder)
    return DeleteFieldOverrideResult(deleted=deleted)
