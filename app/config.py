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

from pydantic import Field, HttpUrl
from pydantic_settings import BaseSettings, SettingsConfigDict


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
    qbit_save_path: str = "/downloads/servarr-downloads"

    # ── VGMDB ───────────────────────────────────────────────────────────────
    vgmdb_url: str = "http://192.168.2.130:8008"

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
    app_port: int = 8900

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

    # ── Convenience predicates ──────────────────────────────────────────────
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
        }
        return [k for k, v in candidates.items() if v == "PLACEHOLDER_ME"]


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Cached settings accessor.

    Call this from FastAPI dependencies. Direct module-level access via
    `from app.config import settings` works too; the cache makes both
    equivalent.
    """
    return Settings()


settings = get_settings()
