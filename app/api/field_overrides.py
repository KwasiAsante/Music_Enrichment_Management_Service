"""``/api/v1/overrides/*`` — per-album manual tag-field override CRUD.

Backs the "Field Overrides" page: search happens against the existing
``GET /api/v1/library/albums`` (see its ``q`` parameter), and three of
these endpoints handle one selected album at a time — see
:mod:`app.core.field_overrides` for why this exists and how a saved
override actually takes effect. ``GET /export``/``POST /import`` operate
on the whole ``field_overrides.json`` file at once, same
export/import-a-backup-file pattern as ``/api/v1/mapping/export``.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone

from fastapi import APIRouter, File, HTTPException, Query, UploadFile
from fastapi.responses import JSONResponse

from app.core.field_overrides import FieldOverrideService
from app.models.field_override import (
    AlbumFieldOptions,
    DeleteFieldOverrideResult,
    FieldOverrideEntry,
    ImportFieldOverridesResult,
    SetFieldOverridesRequest,
)
from app.storage import db

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


# ── GET /overrides/export ───────────────────────────────────────────────────
@router.get("/export")
def export_overrides() -> JSONResponse:
    """Download the full ``field_overrides.json`` as a timestamped backup file."""
    overrides = FieldOverrideService().export_overrides()
    now = datetime.now(timezone.utc)
    payload = {
        "exported_at": now.isoformat(),
        "count": len(overrides),
        "field_overrides": overrides,
    }
    db.add_activity("overrides", f"exported {len(overrides)} field override(s)")
    filename = f"field_overrides-{now:%Y%m%d-%H%M%S}.json"
    return JSONResponse(
        content=payload,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


# ── POST /overrides/import ──────────────────────────────────────────────────
@router.post("/import", response_model=ImportFieldOverridesResult)
async def import_overrides(
    file: UploadFile = File(
        ...,
        description="A file from GET /overrides/export, or a raw field_overrides.json.",
    ),
    mode: str = Query(
        default="merge",
        pattern="^(merge|replace)$",
        description="'merge' adds/overwrites on top of the current overrides. "
        "'replace' makes the imported file the entire set — anything not in "
        "it is dropped.",
    ),
    dry_run: bool = Query(
        default=False,
        description="Report what would change without writing field_overrides.json.",
    ),
) -> ImportFieldOverridesResult:
    """Restore (or merge in) field overrides from a previously exported backup."""
    raw = await file.read()
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise HTTPException(400, f"not valid JSON: {exc}") from exc

    if isinstance(data, dict) and isinstance(data.get("field_overrides"), dict):
        incoming = data["field_overrides"]
    elif isinstance(data, dict):
        incoming = data
    else:
        raise HTTPException(
            400,
            "expected a JSON object — either an export from GET "
            "/overrides/export, or a raw field_overrides.json.",
        )

    try:
        result = FieldOverrideService().import_overrides(incoming, mode=mode, dry_run=dry_run)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc

    if not dry_run:
        db.add_activity(
            "overrides",
            f"imported field overrides (mode={mode}): +{result['added']} added, "
            f"{result['updated']} updated, {result['removed']} removed, "
            f"{result['skipped_invalid']} skipped",
        )
    return ImportFieldOverridesResult(**result)
