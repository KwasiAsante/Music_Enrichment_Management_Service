"""HTTP Basic Auth for the browser-facing web UI.

Deliberately scoped to ``app.ui.router`` only — see the note in
``app/config.py`` next to ``web_ui_user``/``web_ui_pass`` for why the REST
API under ``/api/v1/*`` is NOT behind this: ``lidarr-scripts/on_album_download.py``
calls ``POST /api/v1/enrich/album`` directly from the Lidarr host, with no
browser involved, and Docker's healthcheck hits ``/health`` with plain
``curl``. Neither can present a login prompt. This means someone on the LAN
who goes straight at the API (rather than through a page) isn't stopped by
this — that's a known, accepted gap for this pass, not an oversight.

One shared username/password, not per-user accounts — this matches the
project's single-operator, LAN-only deployment model. If ``web_ui_pass`` is
still the ``PLACEHOLDER_ME`` sentinel, every request is rejected with a
clear 401 detail rather than silently accepting a default password.
"""

from __future__ import annotations

import secrets

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPBasic, HTTPBasicCredentials

from app.config import settings

# auto_error=False: with the default (True), this dependency itself raises
# the 401 + WWW-Authenticate challenge whenever no Authorization header is
# sent, before require_login's body ever runs — which would make the
# disable_web_ui_auth escape hatch below unreachable.
_security = HTTPBasic(auto_error=False)


def require_login(
    credentials: HTTPBasicCredentials | None = Depends(_security),
) -> str:
    """FastAPI dependency — raises 401 unless credentials match settings.

    Uses ``secrets.compare_digest`` for both fields (timing-safe), the same
    pattern FastAPI's own docs recommend for HTTP Basic Auth, so a slow
    string-equality check can't leak how many leading characters matched.
    """
    if settings.disable_web_ui_auth:
        return settings.web_ui_user

    if credentials is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated.",
            headers={"WWW-Authenticate": "Basic"},
        )

    if settings.web_ui_pass == "PLACEHOLDER_ME":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=(
                "WEB_UI_PASS is not configured (still the placeholder). "
                "Set WEB_UI_USER / WEB_UI_PASS in .env before using the web UI."
            ),
            headers={"WWW-Authenticate": "Basic"},
        )

    user_ok = secrets.compare_digest(credentials.username, settings.web_ui_user)
    pass_ok = secrets.compare_digest(credentials.password, settings.web_ui_pass)
    if not (user_ok and pass_ok):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect username or password.",
            headers={"WWW-Authenticate": "Basic"},
        )
    return credentials.username
