"""Application settings.

Single source of truth for runtime configuration. Every value comes from
the environment (and therefore from .env via docker-compose's env_file).

Usage:

    from app.config import settings
    print(settings.lidarr_url)

The intent is that no module elsewhere in the codebase reads os.environ
directly — they import `settings` instead. That keeps env-var typos
caught at startup rather than at use-time.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import Field, HttpUrl, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from app.core.settings_store import read_overrides


class Settings(BaseSettings):
    """Runtime configuration loaded from environment variables.

    Field names map to env vars by upper-casing (pydantic-settings default).
    All fields that correspond to real secrets default to a sentinel string
    so that misconfiguration is loud rather than silent.
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # ── Lidarr ──────────────────────────────────────────────────────────────
    lidarr_url: str = "http://192.168.2.130:8686/lidarr"
    lidarr_api_key: str = Field(default="PLACEHOLDER_ME")

    # ── Prowlarr ────────────────────────────────────────────────────────────
    prowlarr_url: str = "http://192.168.2.130:9696/prowlarr"
    prowlarr_api_key: str = Field(default="PLACEHOLDER_ME")

    # ── qBittorrent ─────────────────────────────────────────────────────────
    qbit_url: str = "http://192.168.2.130:8080"
    qbit_user: str = "admin"
    qbit_pass: str = Field(default="PLACEHOLDER_ME")
    # Optional — qBittorrent v5.2+ supports stateless Bearer auth. When set,
    # username/password login is skipped (see app/api/proxy.py).
    qbit_api_key: str = ""
    qbit_save_path: str = "/downloads/servarr-downloads"

    # ── VGMDB ───────────────────────────────────────────────────────────────
    vgmdb_url: str = "http://192.168.2.130:8008"

    # ── Web UI login (Phase 2) ──────────────────────────────────────────────
    # HTTP Basic Auth on the browser-facing pages only (app.ui.router) — the
    # REST API under /api/v1/* stays open so lidarr-scripts/on_album_download.py
    # (which runs on the Lidarr host, not a browser) keeps working with no
    # changes. See app/ui/auth.py.
    web_ui_user: str = "admin"
    web_ui_pass: str = Field(default="PLACEHOLDER_ME")
    # Dev-only escape hatch: some embedded browser webviews (e.g. Cursor's
    # built-in browser) don't render the native Basic Auth popup a
    # WWW-Authenticate challenge triggers, so there's no way to type
    # credentials in. Set to skip auth entirely for local dev — never set
    # this in a real deployment. See app/ui/auth.py.
    disable_web_ui_auth: bool = False

    # ── MusicBrainz ─────────────────────────────────────────────────────────
    mb_user_agent: str = "MusicLibHelper/1.0 (you@example.com)"

    # ── Discord webhooks ────────────────────────────────────────────────────
    discord_webhook_artist: str = ""
    discord_webhook_enrich: str = ""
    discord_webhook_mb_seed_dl: str = ""
    discord_webhook_mb_seed_beets: str = ""

    # ── GitHub Gist (Picard MBID export) ────────────────────────────────────
    github_token: str = Field(default="PLACEHOLDER_ME")
    gist_id: str = Field(default="PLACEHOLDER_ME")

    # ── Beets ───────────────────────────────────────────────────────────────
    beet_bin: str = "/usr/local/bin/beet"
    beetsdir: Path = Path("/config/beets")

    # ── Schedule (cron format) ──────────────────────────────────────────────
    scan_cron: str = "0 2 * * 0"
    enrich_cron: str = "0 3 * * 0"
    tz: str = "UTC"

    # ── Paths (inside the container) ────────────────────────────────────────
    app_data_dir: Path = Path("/data")
    app_music_dir: Path = Path("/music")

    # ── Service ─────────────────────────────────────────────────────────────
    app_log_level: str = "INFO"
    # Per-feature log files under APP_DATA_DIR/logs/ — more verbose than the
    # web UI activity feed. Set APP_LOG_TO_FILES=false to disable.
    app_log_to_files: bool = True
    app_log_file_level: str = "DEBUG"
    # Minimum log level exposed in the web UI (activity + diagnostic logs).
    app_web_ui_log_level: str = "INFO"
    app_port: int = 8900
    # Same idea as Sonarr/Radarr/Lidarr's "URL Base" setting: serve the
    # entire app (UI, API, static assets, docs) under this path prefix,
    # so a reverse proxy can pass a subpath straight through with no
    # rewriting. Empty (default) = served at the root, unchanged from
    # before this setting existed. e.g. "/music-helper" ->
    # http://host:8900/music-helper/... Normalised below: leading slash
    # required, no trailing slash, "" stays "".
    url_base: str = ""

    @field_validator("url_base")
    @classmethod
    def _normalise_url_base(cls, v: str) -> str:
        v = (v or "").strip().rstrip("/")
        if not v:
            return ""
        if not v.startswith("/"):
            v = f"/{v}"
        return v

    # ── Derived data-file locations ─────────────────────────────────────────
    @property
    def vgmdb_mapping_file(self) -> Path:
        return self.app_data_dir / "vgmdb_mapping.json"

    @property
    def enriched_albums_file(self) -> Path:
        return self.app_data_dir / "enriched_albums.json"

    @property
    def album_list_file(self) -> Path:
        return self.app_data_dir / "album_list.json"

    @property
    def mb_artist_cache_file(self) -> Path:
        return self.app_data_dir / "mb_artist_cache.json"

    @property
    def db_path(self) -> Path:
        return self.app_data_dir / "app.db"

    @property
    def app_log_dir(self) -> Path:
        return self.app_data_dir / "logs"

    # ── Convenience predicates ──────────────────────────────────────────────
    def qbit_uses_api_key(self) -> bool:
        """True when a real qBittorrent Web API key is configured."""
        return bool(self.qbit_api_key) and self.qbit_api_key != "PLACEHOLDER_ME"

    def placeholder_fields(self) -> list[str]:
        """Return the names of any required secret fields still left as
        the PLACEHOLDER_ME sentinel. Used by main.py at startup to log a
        loud warning so misconfiguration is obvious.
        """
        candidates = {
            "lidarr_api_key": self.lidarr_api_key,
            "prowlarr_api_key": self.prowlarr_api_key,
            "qbit_pass": self.qbit_pass,
            "github_token": self.github_token,
            "gist_id": self.gist_id,
            "web_ui_pass": self.web_ui_pass,
        }
        return [k for k, v in candidates.items() if v == "PLACEHOLDER_ME"]


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Cached settings accessor.

    Call this from FastAPI dependencies. Direct module-level access via
    `from app.config import settings` works too; the cache makes both
    equivalent.

    Layers ``settings_override.json`` (see app.core.settings_store — the
    Settings page's saved changes) on top of the environment: pydantic-
    settings gives constructor kwargs priority over env vars by default,
    so anything in the override file wins. A field not present in the
    override file falls through to its normal env var / .env / default,
    unchanged from before this layer existed.
    """
    return Settings(**read_overrides())


settings = get_settings()
