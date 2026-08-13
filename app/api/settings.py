"""``/api/v1/settings/*`` — view and edit runtime configuration.

Two real constraints shape this module:

* ``Settings`` is read once per process, at import time, by roughly a
  dozen other modules (``from app.config import settings``) — there's no
  hot-reload mechanism anywhere in this codebase. Every change here
  needs a process restart to actually take effect; there's no such thing
  as a "no restart needed" field yet, so the UI says that plainly rather
  than pretending some fields are exempt.
* Secret fields (API keys, passwords, Discord webhook URLs — which are
  themselves bearer credentials) are write-only from this API's
  perspective. ``GET`` never echoes a real secret value back, only
  whether one is currently configured.

Unlike the rest of ``/api/v1/*`` (deliberately open — see app/ui/auth.py
for why Lidarr/Picard's own scripts need that), this router sits behind
the same login as the web UI: nothing outside the Settings page itself
has any reason to call it, and it can change credentials, disable login
entirely, and restart the process, so the usual "external scripts can't
present a login prompt" argument for leaving it open doesn't apply here.

One endpoint is an exception to the "restart required" rule above:
``POST /test-notification`` sends a real Discord message immediately,
using whatever URL is currently in the browser (saved or not) — there's
nothing to persist or restart for, it's just a way to verify a webhook
works before committing to it.
"""

from __future__ import annotations

import asyncio
import logging
import os
import signal
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import ValidationError

from app.config import Settings, settings
from app.core import connection_tests
from app.core import settings_store
from app.core.notifier import Notifier
from app.models.settings import (
    RestartResult,
    SettingField,
    SettingsGroup,
    SettingsResponse,
    SettingsUpdateRequest,
    SettingsUpdateResult,
    TestConnectionRequest,
    TestConnectionResult,
    TestNotificationRequest,
    TestNotificationResult,
)
from app.storage import db
from app.ui.auth import require_login

log = logging.getLogger("music-lib-helper.api.settings")

router = APIRouter(
    prefix="/api/v1/settings", tags=["settings"], dependencies=[Depends(require_login)],
)

# Bearer-credential-shaped fields — masked in every response, only ever
# written, never read back. Discord webhook URLs are included: the URL
# itself IS the credential (anyone with it can post to the channel).
SECRET_FIELDS: frozenset[str] = frozenset({
    "lidarr_api_key", "prowlarr_api_key", "qbit_pass", "qbit_api_key", "web_ui_pass",
    "github_token", "discord_webhook_artist", "discord_webhook_enrich",
    "discord_webhook_mb_seed_dl", "discord_webhook_mb_seed_beets",
})

# The subset of SECRET_FIELDS that POST /test-notification will accept —
# spelled out explicitly (rather than e.g. "any key containing
# 'discord_webhook'") so this can't accidentally be tricked into treating
# some other secret field as a webhook URL and POSTing it somewhere.
DISCORD_WEBHOOK_FIELDS: frozenset[str] = frozenset({
    "discord_webhook_artist", "discord_webhook_enrich",
    "discord_webhook_mb_seed_dl", "discord_webhook_mb_seed_beets",
})

# Which settings fields each POST /test-connection service reads. The
# button lives on one "anchor" field per service (see settings.html),
# but the test always uses the whole set.
CONNECTION_SERVICES: dict[str, tuple[str, ...]] = {
    "lidarr": ("lidarr_url", "lidarr_api_key"),
    "prowlarr": ("prowlarr_url", "prowlarr_api_key"),
    "qbit": ("qbit_url", "qbit_user", "qbit_pass", "qbit_api_key"),
    "vgmdb": ("vgmdb_url",),
}

# Filesystem/mount/port-level settings, deliberately NOT exposed for
# editing here — changing them via this page without also updating the
# actual Docker volume mounts or published port would leave the UI
# claiming something that isn't true of the running container.
READ_ONLY_FIELDS: frozenset[str] = frozenset({
    "app_data_dir", "app_music_dir", "beetsdir", "beet_bin", "app_port",
})

# (key, label, group, field_type, options, help_text) — the single
# source of truth both GET and PUT work from. field_type is a display
# hint for the frontend; validation itself is always the real
# `Settings` model, not this catalog.
FIELD_CATALOG: list[tuple[str, str, str, str, list[str] | None, str | None]] = [
    ("url_base", "URL Base", "General", "text", None,
     "Serve the whole app under a path prefix, e.g. /music-helper — the "
     "same idea as Sonarr/Radarr/Lidarr's URL Base. Leave blank to serve "
     "at the root."),
    ("app_log_level", "Log Level", "General", "select",
     ["DEBUG", "INFO", "WARNING", "ERROR"], None),
    ("tz", "Timezone", "General", "text", None,
     "IANA timezone name, e.g. America/Toronto — used for the schedule below."),

    ("scan_cron", "Library Scan Schedule", "Schedule", "text", None,
     'Standard 5-field cron: <code>minute hour day-of-month month day-of-week</code>. '
     'Each field accepts a number, <code>*</code> for "every", a range like <code>1-5</code>, '
     'a list like <code>1,3,5</code>, or a step like <code>*/6</code>. '
     'Day-of-week is 0–6 (0 = Sunday) or 7 for Sunday again.<br>'
     'To run at a specific day/time: set hour and minute to that time and day-of-week to '
     'that day, leave day-of-month and month as <code>*</code>.<br>'
     'Examples: <code>0 2 * * 0</code> → every Sunday at 2:00 AM &nbsp;·&nbsp; '
     '<code>30 3 * * *</code> → every day at 3:30 AM &nbsp;·&nbsp; '
     '<code>0 9 * * 1-5</code> → 9:00 AM on weekdays &nbsp;·&nbsp; '
     '<code>0 */6 * * *</code> → every 6 hours.<br>'
     'Leave blank to disable the scheduled scan.'),
    ("enrich_cron", "Enrichment Schedule", "Schedule", "text", None,
     'Same format as the scan schedule above: <code>minute hour day-of-month month day-of-week</code>, '
     'day-of-week 0–6 with 0 = Sunday.<br>'
     'Examples: <code>0 3 * * 0</code> → every Sunday at 3:00 AM &nbsp;·&nbsp; '
     '<code>0 4 * * *</code> → every day at 4:00 AM &nbsp;·&nbsp; '
     '<code>0 20 * * 5</code> → 8:00 PM every Friday.<br>'
     'Leave blank to disable the scheduled enrichment run.'),

    ("lidarr_url", "Lidarr URL", "Lidarr", "text", None, None),
    ("lidarr_api_key", "Lidarr API Key", "Lidarr", "password", None, None),

    ("prowlarr_url", "Prowlarr URL", "Prowlarr", "text", None, None),
    ("prowlarr_api_key", "Prowlarr API Key", "Prowlarr", "password", None, None),

    ("qbit_url", "qBittorrent URL", "qBittorrent", "text", None, None),
    ("qbit_user", "qBittorrent Username", "qBittorrent", "text", None, None),
    ("qbit_pass", "qBittorrent Password", "qBittorrent", "password", None, None),
    ("qbit_api_key", "qBittorrent API Key", "qBittorrent", "password", None,
     "Optional. qBittorrent v5.2+ — generate in Web UI → Preferences → Web UI. "
     "When set, API key auth is used instead of username/password."),
    ("qbit_save_path", "qBittorrent Save Path", "qBittorrent", "text", None, None),

    ("vgmdb_url", "VGMDB API URL", "VGMDB", "text", None, None),

    ("mb_user_agent", "MusicBrainz User-Agent", "MusicBrainz", "text", None,
     "MusicBrainz requires a descriptive User-Agent identifying your app "
     "and a contact method — see their API rate-limiting docs."),

    ("web_ui_user", "Web UI Username", "Web UI", "text", None, None),
    ("web_ui_pass", "Web UI Password", "Web UI", "password", None, None),
    ("disable_web_ui_auth", "Disable Web UI Login", "Web UI", "bool", None,
     "Turns off the login prompt entirely. Only for local development — "
     "never enable this on a deployment reachable outside your own machine."),

    ("discord_webhook_artist", "New Artist Notification", "Discord Webhooks", "password", None,
     "Leave blank to disable this notification."),
    ("discord_webhook_enrich", "Enrichment Notification", "Discord Webhooks", "password", None,
     "Leave blank to disable this notification."),
    ("discord_webhook_mb_seed_dl", "MB Seed Download Notification", "Discord Webhooks", "password", None,
     "Leave blank to disable this notification."),
    ("discord_webhook_mb_seed_beets", "MB Seed Beets Notification", "Discord Webhooks", "password", None,
     "Leave blank to disable this notification."),

    ("github_token", "GitHub Token", "GitHub Gist", "password", None,
     "Used to publish the Picard MBID export gist."),
    ("gist_id", "Gist ID", "GitHub Gist", "text", None, None),
]


def _serialize(value: Any) -> Any:
    """JSON-safe form of a Settings field value, for persisting to the
    override file (Path -> str; everything else already round-trips
    through json.dumps as-is)."""
    if isinstance(value, Path):
        return str(value)
    return value


def _format_validation_error(exc: ValidationError) -> str:
    parts = []
    for err in exc.errors():
        field = ".".join(str(loc) for loc in err["loc"])
        parts.append(f"{field}: {err['msg']}")
    return "; ".join(parts)


# ── GET /settings ────────────────────────────────────────────────────────
@router.get("", response_model=SettingsResponse)
def get_settings_view() -> SettingsResponse:
    """Every editable (and read-only, for reference) setting, grouped."""
    overrides = settings_store.read_overrides()
    groups: dict[str, list[SettingField]] = {}
    any_restart_pending = False

    for key, label, group, field_type, options, help_text in FIELD_CATALOG:
        is_secret = key in SECRET_FIELDS
        live_value = getattr(settings, key, None)
        overridden = key in overrides
        restart_pending = overridden and overrides.get(key) != _serialize(live_value)
        any_restart_pending = any_restart_pending or restart_pending

        field = SettingField(
            key=key,
            label=label,
            group=group,
            field_type=field_type,
            options=options,
            value=None if is_secret else live_value,
            is_secret=is_secret,
            is_set=bool(live_value) and live_value != "PLACEHOLDER_ME" if is_secret else False,
            editable=True,
            overridden=overridden,
            restart_pending=restart_pending,
            help_text=help_text,
        )
        groups.setdefault(group, []).append(field)

    # Read-only fields, for reference — same treatment, just editable=False
    # and never counted toward restart_required (nothing to apply).
    for key, label, group in [
        ("app_data_dir", "Data Directory", "Paths (read-only)"),
        ("app_music_dir", "Music Directory", "Paths (read-only)"),
        ("beetsdir", "Beets Config Directory", "Paths (read-only)"),
        ("beet_bin", "beet Binary Path", "Paths (read-only)"),
        ("app_port", "App Port", "Paths (read-only)"),
    ]:
        groups.setdefault(group, []).append(SettingField(
            key=key, label=label, group=group, field_type="text",
            value=str(getattr(settings, key, "")), editable=False,
            help_text="Set via the environment / docker-compose.yml, not editable here — "
                       "changing it here wouldn't match the actual container mount/port.",
        ))

    return SettingsResponse(
        groups=[SettingsGroup(name=name, fields=fields) for name, fields in groups.items()],
        restart_required=any_restart_pending,
    )


# ── PUT /settings ────────────────────────────────────────────────────────
@router.put("", response_model=SettingsUpdateResult)
def update_settings(req: SettingsUpdateRequest) -> SettingsUpdateResult:
    """Save changes to the override file. Always requires a restart to
    take effect — see ``POST /settings/restart``."""
    catalog_keys = {f[0] for f in FIELD_CATALOG}
    current_overrides = settings_store.read_overrides()
    candidate = dict(current_overrides)
    changed_keys: list[str] = []

    for key, value in req.values.items():
        if key not in catalog_keys:
            raise HTTPException(400, f"unknown setting: {key!r}")
        if key in READ_ONLY_FIELDS:
            raise HTTPException(400, f"{key!r} is not editable from this page")

        if key in SECRET_FIELDS and (value is None or value == ""):
            continue  # blank/omitted secret = "leave unchanged"

        candidate[key] = value
        changed_keys.append(key)

    if not changed_keys:
        restart_required = any(
            current_overrides.get(k) != _serialize(getattr(settings, k, None))
            for k in current_overrides
        )
        return SettingsUpdateResult(saved=[], restart_required=restart_required)

    try:
        validated = Settings(**candidate)
    except ValidationError as exc:
        raise HTTPException(400, _format_validation_error(exc)) from exc

    # Persist post-validation values (e.g. url_base normalised) for every
    # overridden key, not just the ones just changed — keeps the file
    # internally consistent and makes future restart_pending comparisons
    # exact rather than comparing pre- vs post-validation strings.
    normalized = {k: _serialize(getattr(validated, k)) for k in candidate}
    settings_store.write_overrides(normalized)

    log.warning("settings changed via Settings page: %s (restart required)", ", ".join(changed_keys))
    db.add_activity("settings", f"settings changed: {', '.join(changed_keys)} — restart required")

    return SettingsUpdateResult(saved=changed_keys, restart_required=True)


# ── POST /settings/restart ──────────────────────────────────────────────
@router.post("/restart", response_model=RestartResult)
async def restart_app() -> RestartResult:
    """Gracefully exit the process — Docker's ``restart: unless-stopped``
    policy (production) or uvicorn's ``--reload`` supervisor (local dev)
    brings it back up, at which point it re-reads the override file and
    picks up whatever was saved. There's a short delay before the actual
    signal so this response has time to flush to the browser first.
    """
    log.warning("restart requested via Settings page")
    db.add_activity("settings", "app restart requested")

    async def _do_restart() -> None:
        await asyncio.sleep(0.5)
        os.kill(os.getpid(), signal.SIGTERM)

    asyncio.create_task(_do_restart())
    return RestartResult(status="restarting")


# ── POST /settings/test-notification ────────────────────────────────────
@router.post("/test-notification", response_model=TestNotificationResult)
def test_notification(req: TestNotificationRequest) -> TestNotificationResult:
    """Send a one-off test message to a Discord webhook — the URL
    currently typed into that field's input, even if it hasn't been
    saved yet, or (if that's blank) whatever's currently configured.
    Lets a webhook be verified without waiting for a real event to
    trigger it, or saving a value that turns out to be wrong.
    """
    if req.key not in DISCORD_WEBHOOK_FIELDS:
        raise HTTPException(400, f"{req.key!r} is not a Discord webhook field")

    url = (req.url or "").strip() or getattr(settings, req.key, "")
    ok, message = Notifier().send_test(url)

    db.add_activity(
        "settings", f"test notification ({req.key}): {'sent' if ok else 'failed'} — {message}",
        level="info" if ok else "warning",
    )
    return TestNotificationResult(ok=ok, message=message)


def _resolve_connection_value(key: str, values: dict[str, str | None]) -> str:
    """Form value if non-blank, otherwise the live configured value."""
    raw = values.get(key)
    if raw is not None and str(raw).strip() != "":
        return str(raw).strip()
    live = getattr(settings, key, "")
    return str(live or "").strip()


# ── POST /settings/test-connection ────────────────────────────────────────
@router.post("/test-connection", response_model=TestConnectionResult)
def test_connection(req: TestConnectionRequest) -> TestConnectionResult:
    """Verify Lidarr/Prowlarr/qBittorrent/VGMDB connectivity using the
    credentials currently in the browser, even if unsaved — same idea as
    ``POST /test-notification`` for Discord webhooks.
    """
    if req.service not in CONNECTION_SERVICES:
        raise HTTPException(400, f"unknown service: {req.service!r}")

    resolved = {
        key: _resolve_connection_value(key, req.values)
        for key in CONNECTION_SERVICES[req.service]
    }

    if req.service == "lidarr":
        ok, message = connection_tests.test_lidarr(
            resolved["lidarr_url"], resolved["lidarr_api_key"],
        )
    elif req.service == "prowlarr":
        ok, message = connection_tests.test_prowlarr(
            resolved["prowlarr_url"], resolved["prowlarr_api_key"],
        )
    elif req.service == "qbit":
        ok, message = connection_tests.test_qbit(
            resolved["qbit_url"],
            resolved["qbit_user"],
            resolved["qbit_pass"],
            resolved["qbit_api_key"],
        )
    else:
        ok, message = connection_tests.test_vgmdb(resolved["vgmdb_url"])

    db.add_activity(
        "settings",
        f"test connection ({req.service}): {'ok' if ok else 'failed'} — {message}",
        level="info" if ok else "warning",
    )
    return TestConnectionResult(ok=ok, message=message)
