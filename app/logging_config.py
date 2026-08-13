"""Feature-specific file logging for diagnostics.

Stdout receives ``APP_LOG_LEVEL`` messages (what operators see in docker
logs). Feature log files under ``{APP_DATA_DIR}/logs/`` receive
``APP_LOG_FILE_LEVEL`` messages (default DEBUG) — more verbose than the
web UI's ``activity_log`` feed and job progress panels.
"""

from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path

from app.config import settings

LOG_FORMAT = "%(asctime)s %(levelname)-7s %(name)s: %(message)s"

# Logger name → log file. Multiple loggers may share one file.
FEATURE_LOGGERS: dict[str, str] = {
    "music-lib-helper.beets_enricher": "enrich.log",
    "music-lib-helper.api.enrich": "enrich.log",
    "music-lib-helper.scanner": "scan.log",
    "music-lib-helper.api.library": "scan.log",
    "music-lib-helper.vgmdb_mapper": "mapping.log",
    "music-lib-helper.vgmdb": "mapping.log",
    "music-lib-helper.api.mapping": "mapping.log",
    "music-lib-helper.scheduler": "scheduler.log",
    "music-lib-helper.artist_fixer": "artist_fix.log",
    "music-lib-helper.api.artist": "artist_fix.log",
    "music-lib-helper.picard": "picard.log",
    "music-lib-helper.api.picard": "picard.log",
    "music-lib-helper.mb": "mb.log",
    "music-lib-helper.mb_link": "mb.log",
    "music-lib-helper.api.mb": "mb.log",
    "music-lib-helper.cover_art": "mb.log",
    "music-lib-helper.album_details": "mb.log",
    "music-lib-helper.proxy": "proxy.log",
    "music-lib-helper.api.settings": "admin.log",
    "music-lib-helper.api.backup": "admin.log",
    "music-lib-helper.settings_store": "admin.log",
    "music-lib-helper.ui": "ui.log",
    "music-lib-helper": "app.log",
    "music-lib-helper.db": "app.log",
    "music-lib-helper.storage": "app.log",
    "music-lib-helper.notifier": "app.log",
    "music-lib-helper.lidarr": "app.log",
    "music-lib-helper.beets_config": "app.log",
}

# Job types (see db.create_job) → logger for mirroring append_log lines.
JOB_TYPE_TO_LOGGER: dict[str, str] = {
    "scan": "music-lib-helper.scanner",
    "scan_scheduled": "music-lib-helper.scheduler",
    "enrich_album": "music-lib-helper.beets_enricher",
    "enrich_run": "music-lib-helper.beets_enricher",
    "enrich_run_scheduled": "music-lib-helper.scheduler",
    "fix_path": "music-lib-helper.artist_fixer",
    "fix_all_paths": "music-lib-helper.artist_fixer",
    "picard_export": "music-lib-helper.picard",
    "picard_export_full": "music-lib-helper.picard",
}

# Reverse lookup: logger name → feature key (log file basename).
LOGGER_TO_FEATURE: dict[str, str] = {
    logger_name: filename.removesuffix(".log")
    for logger_name, filename in FEATURE_LOGGERS.items()
}

# Populated by db.create_job so append_log mirroring avoids extra queries.
_job_types: dict[str, str] = {}


def register_job_type(job_id: str, job_type: str) -> None:
    """Remember a job's type for log mirroring."""
    _job_types[job_id] = job_type


def log_job_progress(job_id: str, message: str) -> None:
    """Mirror a job progress line to the matching feature log file."""
    job_type = _job_types.get(job_id)
    if not job_type:
        return
    logger_name = JOB_TYPE_TO_LOGGER.get(job_type, "music-lib-helper")
    logging.getLogger(logger_name).debug("[job %s] %s", job_id[:8], message.rstrip("\n"))


def setup_logging() -> None:
    """Configure stdout and optional per-feature rotating log files."""
    console_level = getattr(logging, settings.app_log_level.upper(), logging.INFO)
    file_level = getattr(
        logging, settings.app_log_file_level.upper(), logging.DEBUG,
    )

    root = logging.getLogger()
    root.handlers.clear()
    root.setLevel(
        min(console_level, file_level)
        if settings.app_log_to_files
        else console_level,
    )

    formatter = logging.Formatter(LOG_FORMAT)

    console = logging.StreamHandler()
    console.setLevel(console_level)
    console.setFormatter(formatter)
    root.addHandler(console)

    if not settings.app_log_to_files:
        return

    log_dir = settings.app_log_dir
    log_dir.mkdir(parents=True, exist_ok=True)

    # One RotatingFileHandler per unique filename, shared by its loggers.
    files_to_loggers: dict[str, list[str]] = {}
    for logger_name, filename in FEATURE_LOGGERS.items():
        files_to_loggers.setdefault(filename, []).append(logger_name)

    for filename, logger_names in files_to_loggers.items():
        handler = RotatingFileHandler(
            log_dir / filename,
            maxBytes=10 * 1024 * 1024,
            backupCount=5,
            encoding="utf-8",
        )
        handler.setLevel(file_level)
        handler.setFormatter(formatter)
        for name in logger_names:
            feature_logger = logging.getLogger(name)
            feature_logger.setLevel(file_level)
            feature_logger.addHandler(handler)

    logging.getLogger("music-lib-helper").info(
        "feature log files enabled at %s (file level=%s, console level=%s)",
        log_dir, settings.app_log_file_level.upper(), settings.app_log_level.upper(),
    )


def list_log_files() -> list[Path]:
    """Return known feature log file paths (may not all exist yet)."""
    log_dir = settings.app_log_dir
    seen: set[str] = set()
    paths: list[Path] = []
    for filename in FEATURE_LOGGERS.values():
        if filename not in seen:
            seen.add(filename)
            paths.append(log_dir / filename)
    return sorted(paths)
