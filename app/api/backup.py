"""``/api/v1/backup/*`` — full-state backup, not just the VGMDB mapping.

``POST /mapping/export`` (see app.api.mapping) already covers
``vgmdb_mapping.json`` on its own — the single highest-effort piece of
state, since it represents actual search/matching decisions. This is
the "everything else" complement: one ZIP with every other JSON state
file, the SQLite database (jobs + activity log), and a manifest
describing what's in it.

Sits behind login, same reasoning as ``/api/v1/settings/*`` (see that
module's docstring): unlike the rest of ``/api/v1/*``, which is
deliberately open for Lidarr/Picard's own scripts, nothing external has
any legitimate reason to bulk-download the app's entire runtime state,
and this endpoint doesn't need to answer to a script with no browser.

Secrets are never included in cleartext. ``settings_override.json``
entries for secret-shaped fields (api keys, passwords, Discord webhook
URLs — see ``app.api.settings.SECRET_FIELDS``) are replaced with a
``{"redacted": true, "was_configured": bool}`` marker rather than their
real value. A backup is meant to be portable and occasionally shared
with future-you (or pasted into a support request); credentials aren't
something that file should be trusted to carry, and re-entering them
once via ``.env`` or the Settings page after a restore is a small price
for never having a plaintext API key sitting in a downloads folder.
"""

from __future__ import annotations

import io
import json
import logging
import zipfile
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse

from app import __version__
from app.config import settings
from app.core import settings_store
from app.storage import db
from app.storage.json_store import store
from app.ui.auth import require_login

log = logging.getLogger("music-lib-helper.api.backup")

router = APIRouter(prefix="/api/v1/backup", tags=["backup"], dependencies=[Depends(require_login)])

# The JSON state files bundled into the backup, keyed by the filename
# they're written into the ZIP under. Kept as a plain list of
# (archive_name, JsonFile) pairs rather than hardcoding attribute access
# inline, so adding a new state file to json_store.py later is a
# one-line addition here too.
_JSON_STATE_FILES = [
    ("vgmdb_mapping.json", "vgmdb_mapping"),
    ("album_list.json", "album_list"),
    ("enriched_albums.json", "enriched_albums"),
    ("mb_artist_cache.json", "mb_artist_cache"),
    ("excluded_artists.json", "excluded_artists"),
    ("skipped_albums.json", "skipped_albums"),
    ("artists_mbids.json", "artists_mbids"),
]


def _redact_overrides(overrides: dict[str, Any]) -> dict[str, Any]:
    """Replace secret-field values in a settings-override dict with a
    plain was-it-configured marker. Local import of SECRET_FIELDS to
    avoid a circular import at module load time (app.api.settings also
    imports from app.core, and this module living under app.api
    shouldn't need to care about import order beyond "inside the
    function is late enough")."""
    from app.api.settings import SECRET_FIELDS

    redacted: dict[str, Any] = {}
    for key, value in overrides.items():
        if key in SECRET_FIELDS:
            redacted[key] = {
                "redacted": True,
                "was_configured": bool(value) and value != "PLACEHOLDER_ME",
            }
        else:
            redacted[key] = value
    return redacted


@router.get("/export")
def export_backup() -> StreamingResponse:
    """Download a ZIP with every JSON state file, the SQLite database,
    and a manifest. Best-effort per file — one unreadable/corrupt file
    is noted in the manifest and skipped rather than failing the whole
    export, since "everything except the one broken piece" is a much
    more useful backup than none at all.
    """
    now = datetime.now(timezone.utc)
    buf = io.BytesIO()
    included: list[str] = []
    warnings: list[str] = []

    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for archive_name, attr in _JSON_STATE_FILES:
            json_file = getattr(store, attr, None)
            if json_file is None:
                warnings.append(f"{archive_name}: not available (no such store attribute)")
                continue
            try:
                data = json_file.read()
            except Exception as exc:  # noqa: BLE001 — one bad file shouldn't sink the export
                log.warning("backup: could not read %s: %s", archive_name, exc)
                warnings.append(f"{archive_name}: could not be read ({exc}) — skipped")
                continue
            zf.writestr(archive_name, json.dumps(data, indent=2, ensure_ascii=False))
            included.append(archive_name)

        try:
            overrides = _redact_overrides(settings_store.read_overrides())
            zf.writestr("settings_override.json", json.dumps(overrides, indent=2))
            included.append("settings_override.json (secrets redacted)")
        except Exception as exc:  # noqa: BLE001
            log.warning("backup: could not read settings overrides: %s", exc)
            warnings.append(f"settings_override.json: could not be read ({exc}) — skipped")

        try:
            db_path = settings.db_path
            if db_path.exists():
                zf.write(db_path, arcname="app.db")
                included.append("app.db (jobs + activity log)")
            else:
                warnings.append("app.db: does not exist yet — skipped")
        except Exception as exc:  # noqa: BLE001
            log.warning("backup: could not include app.db: %s", exc)
            warnings.append(f"app.db: could not be included ({exc}) — skipped")

        manifest = {
            "exported_at": now.isoformat(),
            "app_version": __version__,
            "included": included,
            "warnings": warnings,
            "note": (
                "Secret fields in settings_override.json are redacted to "
                "was_configured true/false markers, not real values — "
                "reconfigure credentials via .env or the Settings page "
                "after restoring from this backup."
            ),
        }
        zf.writestr("manifest.json", json.dumps(manifest, indent=2))

    buf.seek(0)
    filename = f"music-lib-helper-backup-{now:%Y%m%d-%H%M%S}.zip"
    log.info("full backup exported: %d file(s), %d warning(s)", len(included), len(warnings))
    db.add_activity("backup", f"full backup exported: {len(included)} file(s), {len(warnings)} warning(s)")

    return StreamingResponse(
        buf,
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
