"""app.logging_config — feature log file setup and job progress mirroring."""

from __future__ import annotations

import logging

import pytest

from app.config import settings
from app.logging_config import log_job_progress, register_job_type, setup_logging


@pytest.fixture
def logging_env(tmp_path, monkeypatch):
    """Isolated log directory with file logging enabled."""
    log_dir = tmp_path / "logs"
    monkeypatch.setattr(settings, "app_data_dir", tmp_path)
    monkeypatch.setattr(settings, "app_log_to_files", True)
    monkeypatch.setattr(settings, "app_log_level", "INFO")
    monkeypatch.setattr(settings, "app_log_file_level", "DEBUG")
    setup_logging()
    return log_dir


def test_setup_logging_creates_feature_files(logging_env):
    logger = logging.getLogger("music-lib-helper.beets_enricher")
    logger.debug("beet subprocess detail")
    logger.info("enrichment started")

    enrich_log = logging_env / "enrich.log"
    assert enrich_log.exists()
    content = enrich_log.read_text(encoding="utf-8")
    assert "beet subprocess detail" in content
    assert "enrichment started" in content


def test_console_handler_filters_debug(logging_env):
    root = logging.getLogger()
    stream_handlers = [
        h for h in root.handlers
        if type(h) is logging.StreamHandler
    ]
    assert stream_handlers
    assert stream_handlers[0].level == logging.INFO

    logger = logging.getLogger("music-lib-helper.scanner")
    logger.debug("per-album scan detail")

    scan_log = (logging_env / "scan.log").read_text(encoding="utf-8")
    assert "per-album scan detail" in scan_log


def test_file_logging_disabled(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(settings, "app_data_dir", tmp_path)
    monkeypatch.setattr(settings, "app_log_to_files", False)
    monkeypatch.setattr(settings, "app_log_level", "INFO")
    setup_logging()

    logger = logging.getLogger("music-lib-helper.beets_enricher")
    logger.info("only stdout")

    assert not (tmp_path / "logs" / "enrich.log").exists()
    captured = capsys.readouterr()
    assert "only stdout" in captured.out + captured.err


def test_job_progress_mirrored_to_feature_log(logging_env):
    job_id = "abc123def456"
    register_job_type(job_id, "enrich_run")
    log_job_progress(job_id, "started (dry_run=False)\n")

    content = (logging_env / "enrich.log").read_text(encoding="utf-8")
    assert "[job abc123de]" in content
    assert "started (dry_run=False)" in content
