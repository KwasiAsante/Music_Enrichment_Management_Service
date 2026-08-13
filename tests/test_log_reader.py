"""log_reader and GET /api/v1/logs filtering."""

from __future__ import annotations

import pytest

from app.config import settings
from app.core.log_reader import (
    _parse_log_text,
    effective_minimum,
    level_meets_minimum,
    read_diagnostic_entries,
)

SAMPLE_LOG = """\
2026-08-13 10:20:13,088 DEBUG   music-lib-helper.beets_enricher: vgmdb resolve: mapping hit abc → 12345
2026-08-13 10:20:14,100 INFO    music-lib-helper.beets_enricher: enriching Artist — Album with vgmdb:12345 (source=mapping)
2026-08-13 10:20:16,300 ERROR   music-lib-helper.api.enrich: enrich job crashed
"""


def test_parse_log_text_extracts_levels_and_features():
    entries = _parse_log_text(SAMPLE_LOG, source_file="enrich.log")
    assert len(entries) == 3
    assert entries[0]["level"] == "debug"
    assert entries[0]["feature"] == "enrich"
    assert entries[-1]["level"] == "error"
    assert entries[-1]["source"] == "diagnostic"


def test_level_meets_minimum_uses_inclusive_floor():
    assert level_meets_minimum("debug", "debug")
    assert level_meets_minimum("info", "debug")
    assert not level_meets_minimum("debug", "info")
    assert level_meets_minimum("error", "warning")


def test_effective_minimum_respects_configured_floor():
    assert effective_minimum(None, "INFO") == "INFO"
    assert effective_minimum("debug", "INFO") == "INFO"
    assert effective_minimum("warning", "INFO") == "WARNING"


@pytest.fixture
def log_files(isolated_env, monkeypatch):
    log_dir = isolated_env.data_dir / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    (log_dir / "enrich.log").write_text(SAMPLE_LOG, encoding="utf-8")
    (log_dir / "scan.log").write_text(
        "2026-08-13 10:20:15,200 WARNING music-lib-helper.scanner: could not read tags\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(settings, "app_log_to_files", True)
    monkeypatch.setattr(settings, "app_web_ui_log_level", "DEBUG")
    return log_dir


def test_read_diagnostic_entries_filters_by_feature_and_level(log_files):
    all_debug = read_diagnostic_entries(feature=None, min_level="DEBUG", limit=50)
    assert len(all_debug) == 4

    enrich_only = read_diagnostic_entries(feature="enrich", min_level="DEBUG", limit=50)
    assert len(enrich_only) == 3
    assert all(e["feature"] == "enrich" for e in enrich_only)

    scan_only = read_diagnostic_entries(feature="scan", min_level="DEBUG", limit=50)
    assert len(scan_only) == 1
    assert scan_only[0]["feature"] == "scan"

    warnings_up = read_diagnostic_entries(feature=None, min_level="WARNING", limit=50)
    assert len(warnings_up) == 2
    assert all(e["level"] in ("warning", "error") for e in warnings_up)


def test_logs_api_merges_activity_and_diagnostic(client, isolated_env, log_files, auth):
    from app.storage import db

    db.add_activity("enrich", "enriched vgmdb:99 (95%)", artist="Test Artist", album="Test Album")
    db.add_activity("scan", "scan complete — 10 albums")

    r = client.get("/api/v1/logs/?limit=50&source=all", auth=auth)
    assert r.status_code == 200
    rows = r.json()
    sources = {row["source"] for row in rows}
    assert "activity" in sources
    assert "diagnostic" in sources

    r = client.get("/api/v1/logs/?feature=enrich&source=diagnostic&level=debug", auth=auth)
    assert r.status_code == 200
    assert all(row["feature"] == "enrich" for row in r.json())

    r = client.get("/api/v1/logs/?feature=scan&source=activity", auth=auth)
    assert r.status_code == 200
    assert all(row["feature"] == "scan" for row in r.json())


def test_web_ui_log_level_caps_diagnostic_detail(client, isolated_env, log_files, monkeypatch, auth):
    monkeypatch.setattr(settings, "app_web_ui_log_level", "WARNING")
    r = client.get("/api/v1/logs/?source=diagnostic&limit=50", auth=auth)
    rows = r.json()
    assert rows
    assert all(row["level"] in ("warning", "error") for row in rows)
