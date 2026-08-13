"""Connectivity checks for Settings-page "Test Connection" buttons.

Each helper accepts the credentials currently typed into the form (or,
when a field is blank, whatever is already configured in ``settings``)
and returns ``(ok, message)`` — same contract as
:meth:`app.core.notifier.Notifier.send_test`, so the Settings API can
report *why* a test failed rather than a bare bool.
"""

from __future__ import annotations

import httpx

from app.core.vgmdb_client import _HEADERS


def _is_configured(value: str) -> bool:
    return bool(value) and value != "PLACEHOLDER_ME"


def test_lidarr(url: str, api_key: str) -> tuple[bool, str]:
    if not url:
        return False, "Lidarr URL is not set."
    if not _is_configured(api_key):
        return False, "Lidarr API key is not set."

    base = url.rstrip("/") + "/api/v1"
    try:
        r = httpx.get(
            f"{base}/system/status",
            headers={"X-Api-Key": api_key},
            timeout=10.0,
        )
        r.raise_for_status()
        version = (r.json() or {}).get("version", "?")
        return True, f"Connected — Lidarr v{version}."
    except httpx.HTTPStatusError as exc:
        return False, (
            f"Lidarr rejected the request (HTTP {exc.response.status_code}) "
            "— check the URL and API key."
        )
    except httpx.HTTPError as exc:
        return False, f"Request failed: {exc}"


def test_prowlarr(url: str, api_key: str) -> tuple[bool, str]:
    if not url:
        return False, "Prowlarr URL is not set."
    if not _is_configured(api_key):
        return False, "Prowlarr API key is not set."

    base = url.rstrip("/") + "/api/v1"
    try:
        r = httpx.get(
            f"{base}/system/status",
            params={"apikey": api_key},
            timeout=10.0,
        )
        r.raise_for_status()
        version = (r.json() or {}).get("version", "?")
        return True, f"Connected — Prowlarr v{version}."
    except httpx.HTTPStatusError as exc:
        return False, (
            f"Prowlarr rejected the request (HTTP {exc.response.status_code}) "
            "— check the URL and API key."
        )
    except httpx.HTTPError as exc:
        return False, f"Request failed: {exc}"


def test_qbit(url: str, username: str, password: str) -> tuple[bool, str]:
    if not url:
        return False, "qBittorrent URL is not set."
    if not username:
        return False, "qBittorrent username is not set."
    if not _is_configured(password):
        return False, "qBittorrent password is not set."

    base = url.rstrip("/")
    try:
        login = httpx.post(
            f"{base}/api/v2/auth/login",
            data={"username": username, "password": password},
            timeout=10.0,
        )
        if login.text.strip() == "Fails.":
            return False, "Login failed — check the username and password."
        login.raise_for_status()

        version = httpx.get(
            f"{base}/api/v2/app/version",
            cookies=login.cookies,
            timeout=10.0,
        )
        version.raise_for_status()
        return True, f"Connected — qBittorrent v{version.text.strip()}."
    except httpx.HTTPStatusError as exc:
        return False, (
            f"qBittorrent rejected the request (HTTP {exc.response.status_code}) "
            "— check the URL and credentials."
        )
    except httpx.HTTPError as exc:
        return False, f"Request failed: {exc}"


def test_vgmdb(url: str) -> tuple[bool, str]:
    if not url:
        return False, "VGMDB API URL is not set."

    base = url.rstrip("/")
    try:
        r = httpx.get(
            f"{base}/search/albums/a",
            params={"format": "json"},
            headers=_HEADERS,
            timeout=10.0,
        )
        r.raise_for_status()
        r.json()
        return True, "Connected — VGMDB API responded."
    except httpx.HTTPStatusError as exc:
        return False, (
            f"VGMDB API rejected the request (HTTP {exc.response.status_code}) "
            "— check the URL."
        )
    except httpx.HTTPError as exc:
        return False, f"Request failed: {exc}"
    except ValueError:
        return False, "VGMDB API returned invalid JSON — check the URL."
