"""``/api/v1/tags/*`` — album tag view/edit + per-field enrichment locks.

Distinct from ``/api/v1/overrides`` (see :mod:`app.api.field_overrides`):
an override redirects what the *next* enrichment writes, while a save
here writes the file(s) immediately, and a lock protects a field from
every future enrichment run rather than picking what value lands there.
See :mod:`app.core.tag_editor` and :mod:`app.core.tag_locks`.

``PUT /track-locks`` is a separate, parallel lock pair for ``title`` and
track-level ``artist`` — the two per-track fields ``PUT /locks`` refuses
to touch (they'd need one shared value written album-wide, overwriting
every track's own title/artist). Each track keeps its own value; the
lock is just "don't let enrichment change it."

``GET /locks/export``/``POST /locks/import`` operate on the whole
``locked_fields.json`` file at once, same export/import-a-backup-file
pattern as ``/api/v1/mapping/export``.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone

from fastapi import APIRouter, File, HTTPException, Query, UploadFile
from fastapi.responses import JSONResponse

from app.core.tag_editor import TagEditorService
from app.core.tag_locks import TagLockService
from app.models.tag_editor import (
    AlbumTagsView,
    ImportLockedFieldsResult,
    SetLockRequest,
    SetLockResult,
    SetTrackLockRequest,
    SetTrackLockResult,
    TrackLockState,
    UpdateAlbumTagsRequest,
    UpdateAlbumTagsResult,
)
from app.storage import db

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


# ── PUT /tags/track-locks ─────────────────────────────────────────────────
@router.put("/track-locks", response_model=SetTrackLockResult)
def set_track_lock(
    req: SetTrackLockRequest,
    folder: str = Query(..., description="An AlbumEntry.folder value."),
) -> SetTrackLockResult:
    """Lock or unlock track titles or track-level artist tags against
    every future enrichment run for this album. Unlike ``PUT /tags/locks``,
    each track keeps its own value rather than all tracks sharing one —
    see ``TagLockService.snapshot_tracks``/``restore_tracks``."""
    try:
        fields = TagLockService().set_track_lock(folder, req.field, req.locked)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return SetTrackLockResult(
        folder=folder,
        track_locks=TrackLockState(title="title" in fields, artist="artist" in fields),
    )


# ── GET /tags/locks/export ──────────────────────────────────────────────────
@router.get("/locks/export")
def export_locks() -> JSONResponse:
    """Download the full ``locked_fields.json`` as a timestamped backup file."""
    locks = TagLockService().export_locks()
    now = datetime.now(timezone.utc)
    payload = {
        "exported_at": now.isoformat(),
        "count": len(locks),
        "locked_fields": locks,
    }
    db.add_activity("tags", f"exported {len(locks)} tag lock entry(ies)")
    filename = f"locked_fields-{now:%Y%m%d-%H%M%S}.json"
    return JSONResponse(
        content=payload,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


# ── POST /tags/locks/import ─────────────────────────────────────────────────
@router.post("/locks/import", response_model=ImportLockedFieldsResult)
async def import_locks(
    file: UploadFile = File(
        ...,
        description="A file from GET /tags/locks/export, or a raw locked_fields.json.",
    ),
    mode: str = Query(
        default="merge",
        pattern="^(merge|replace)$",
        description="'merge' adds/overwrites on top of the current locks. "
        "'replace' makes the imported file the entire set — anything not "
        "in it is dropped.",
    ),
    dry_run: bool = Query(
        default=False,
        description="Report what would change without writing locked_fields.json.",
    ),
) -> ImportLockedFieldsResult:
    """Restore (or merge in) tag locks from a previously exported backup."""
    raw = await file.read()
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise HTTPException(400, f"not valid JSON: {exc}") from exc

    if isinstance(data, dict) and isinstance(data.get("locked_fields"), dict):
        incoming = data["locked_fields"]
    elif isinstance(data, dict):
        incoming = data
    else:
        raise HTTPException(
            400,
            "expected a JSON object — either an export from GET "
            "/tags/locks/export, or a raw locked_fields.json.",
        )

    try:
        result = TagLockService().import_locks(incoming, mode=mode, dry_run=dry_run)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc

    if not dry_run:
        db.add_activity(
            "tags",
            f"imported tag locks (mode={mode}): +{result['added']} added, "
            f"{result['updated']} updated, {result['removed']} removed, "
            f"{result['skipped_invalid']} skipped",
        )
    return ImportLockedFieldsResult(**result)
