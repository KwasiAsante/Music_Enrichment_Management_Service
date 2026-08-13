"""Read and parse feature log files for the web UI.

Log files use the format configured in :mod:`app.logging_config`::

    2026-08-13 10:20:13,088 INFO    music-lib-helper.scanner: message
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.config import settings
from app.logging_config import FEATURE_LOGGERS, LOGGER_TO_FEATURE

LOG_LINE_RE = re.compile(
    r"^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2},\d{3}) (\w+)\s+([^:]+): (.*)$",
)

LEVEL_VALUES: dict[str, int] = {
    "DEBUG": 10,
    "INFO": 20,
    "WARNING": 30,
    "ERROR": 40,
    "CRITICAL": 50,
}

# activity_log categories → feature keys (matches log file basenames).
ACTIVITY_CATEGORY_TO_FEATURE: dict[str, str] = {
    "scan": "scan",
    "scan_summary": "scan",
    "mapping": "mapping",
    "enrich": "enrich",
    "enrich_summary": "enrich",
    "artist_fix": "artist_fix",
    "picard": "picard",
    "settings": "admin",
    "backup": "admin",
}

KNOWN_FEATURES: list[str] = sorted({
    filename.removesuffix(".log") for filename in FEATURE_LOGGERS.values()
})


def normalize_level(level: str) -> str:
    return (level or "INFO").upper()


def level_meets_minimum(level: str, minimum: str | None) -> bool:
    if not minimum:
        return True
    return (
        LEVEL_VALUES.get(normalize_level(level), 0)
        >= LEVEL_VALUES.get(normalize_level(minimum), 0)
    )


def effective_minimum(requested: str | None, floor: str) -> str:
    """Return the more restrictive of a UI filter and ``APP_WEB_UI_LOG_LEVEL``."""
    if not requested:
        return normalize_level(floor)
    req = normalize_level(requested)
    fl = normalize_level(floor)
    return req if LEVEL_VALUES.get(req, 0) >= LEVEL_VALUES.get(fl, 0) else fl


def activity_feature(category: str) -> str:
    return ACTIVITY_CATEGORY_TO_FEATURE.get(category, category)


def parse_log_timestamp(ts: str) -> datetime:
    if "T" in ts:
        return datetime.fromisoformat(ts.replace("Z", "+00:00"))
    return datetime.strptime(ts[:23], "%Y-%m-%d %H:%M:%S,%f").replace(tzinfo=timezone.utc)


def _tail_text(path: Path, max_bytes: int) -> str:
    if not path.is_file():
        return ""
    size = path.stat().st_size
    with path.open("rb") as fh:
        if size > max_bytes:
            fh.seek(-max_bytes, 2)
            fh.readline()
        return fh.read().decode("utf-8", errors="replace")


def _parse_log_text(text: str, *, source_file: str) -> list[dict[str, Any]]:
    feature = source_file.removesuffix(".log")
    entries: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None

    for line in text.splitlines():
        match = LOG_LINE_RE.match(line)
        if match:
            ts, level, logger_name, message = match.groups()
            current = {
                "id": None,
                "ts": ts,
                "source": "diagnostic",
                "feature": LOGGER_TO_FEATURE.get(logger_name.strip(), feature),
                "category": logger_name.strip(),
                "level": level.lower(),
                "artist": None,
                "album": None,
                "message": message,
            }
            entries.append(current)
            continue
        if current is not None and line.strip():
            current["message"] = f"{current['message']}\n{line}"

    return entries


def _log_files_for_feature(feature: str | None) -> list[Path]:
    log_dir = settings.app_log_dir
    if feature:
        path = log_dir / f"{feature}.log"
        return [path] if path.name in {f"{f}.log" for f in KNOWN_FEATURES} else []
    seen: set[str] = set()
    paths: list[Path] = []
    for filename in FEATURE_LOGGERS.values():
        if filename in seen:
            continue
        seen.add(filename)
        paths.append(log_dir / filename)
    return sorted(paths)


def read_diagnostic_entries(
    *,
    feature: str | None = None,
    min_level: str = "INFO",
    limit: int = 500,
    tail_bytes: int = 256_000,
) -> list[dict[str, Any]]:
    """Return recent parsed entries from feature log files, newest first."""
    if not settings.app_log_to_files:
        return []

    per_file_bytes = tail_bytes if feature else max(tail_bytes // 4, 32_000)
    entries: list[dict[str, Any]] = []

    for path in _log_files_for_feature(feature):
        entries.extend(
            _parse_log_text(
                _tail_text(path, per_file_bytes),
                source_file=path.name,
            ),
        )

    filtered = [
        e for e in entries
        if level_meets_minimum(e["level"], min_level)
        and (not feature or e["feature"] == feature)
    ]
    filtered.sort(key=lambda e: parse_log_timestamp(e["ts"]), reverse=True)
    return filtered[:limit]
