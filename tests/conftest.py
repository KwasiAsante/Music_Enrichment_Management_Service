"""Shared fixtures.

The one non-obvious thing every test in this suite relies on is
`isolated_env` below, so it's worth spelling out why it looks the way it
does.

`app.config.settings` is a single module-level instance, imported by name
(`from app.config import settings`) in roughly a dozen other modules.
`app.storage.json_store.store` is the same pattern, one level deeper: its
`JsonFile`s each capture a concrete `Path` *once*, at `JsonStore.__init__`
time (module import), not read dynamically from `settings` on every call.

That means the usual pytest approach — set an env var, exercise some
code, assert — doesn't isolate anything here: by the time a test runs,
`app.main` (and everything it imports) has typically already been
imported once for the whole test session, so a fresh env var doesn't
retroactively change already-constructed singletons.

What *does* work, and is what `isolated_env` does: mutate the existing
singleton objects' attributes in place, rather than trying to replace
them. Every module holds a reference to the *same* `settings`/`store`
object, so an in-place attribute change is visible everywhere
immediately — this is the standard pattern for isolating tests around a
shared singleton, and it's why `monkeypatch` (which reverts everything
automatically at test teardown) rather than manual mutation is used
throughout.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Iterator, NamedTuple

import pytest
from fastapi.testclient import TestClient

from app.config import Settings, settings
from app.storage.json_store import store


def pytest_configure(config: pytest.Config) -> None:
    """Runs once, before any test collection/execution.

    Settings reads `.env` (relative to cwd) by default — fine for the
    running app, but it means every `Settings()` construction during a
    test run would otherwise pick up whatever this particular checkout's
    local `.env` happens to contain (timezone, cron schedules, etc.),
    silently making the suite depend on machine-specific state instead
    of the fixtures below. Disabled for the whole test session so every
    test's configuration comes only from defaults plus what a fixture
    explicitly sets — reproducible on a fresh clone or in CI with no
    `.env` file at all.
    """
    Settings.model_config["env_file"] = None


class IsolatedEnv(NamedTuple):
    data_dir: Path
    music_dir: Path


@pytest.fixture
def isolated_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> IsolatedEnv:
    """Give one test a private data dir, music dir, and JSON-store paths.

    Function-scoped deliberately — a fresh tmp_path per test is the
    correct default for isolation, and everything here is local
    filesystem I/O, cheap enough that per-test isolation doesn't need to
    be traded away for speed.
    """
    data_dir = tmp_path / "data"
    music_dir = tmp_path / "music"
    data_dir.mkdir()
    music_dir.mkdir()

    # Covers app.storage.db (reads settings.app_data_dir / settings.db_path
    # fresh on every connection) and anything else that reads `settings.*`
    # dynamically.
    monkeypatch.setattr(settings, "app_data_dir", data_dir)
    monkeypatch.setattr(settings, "app_music_dir", music_dir)

    # app.core.settings_store deliberately reads the raw env var (not
    # `settings`) to avoid a circular import — see that module's
    # docstring — so it needs its own monkeypatch to match.
    monkeypatch.setenv("APP_DATA_DIR", str(data_dir))

    # `store`'s JsonFiles captured absolute paths at import time; redirect
    # each one under the new data dir, same filenames as production.
    for attr, filename in [
        ("album_list", "album_list.json"),
        ("vgmdb_mapping", "vgmdb_mapping.json"),
        ("enriched_albums", "enriched_albums.json"),
        ("mb_artist_cache", "mb_artist_cache.json"),
        ("skipped_albums", "skipped_albums.json"),
        ("excluded_artists", "excluded_artists.json"),
        ("artists_mbids", "artists_mbids.json"),
    ]:
        json_file = getattr(store, attr, None)
        if json_file is not None:
            monkeypatch.setattr(json_file, "path", data_dir / filename)

    return IsolatedEnv(data_dir=data_dir, music_dir=music_dir)


@pytest.fixture
def auth_credentials(monkeypatch: pytest.MonkeyPatch) -> tuple[str, str]:
    """Deterministic, known-good web UI credentials for this test —
    mutating both the live singleton (most code paths) and the env var
    (anywhere a fresh `Settings()` gets constructed, e.g. the Settings
    page's validate-before-save step) so both stay consistent.
    """
    username, password = "testuser", "testpass123"
    monkeypatch.setattr(settings, "web_ui_user", username)
    monkeypatch.setattr(settings, "web_ui_pass", password)
    monkeypatch.setattr(settings, "disable_web_ui_auth", False)
    monkeypatch.setenv("WEB_UI_USER", username)
    monkeypatch.setenv("WEB_UI_PASS", password)
    monkeypatch.setenv("DISABLE_WEB_UI_AUTH", "false")
    return username, password


@pytest.fixture
def client(isolated_env: IsolatedEnv, auth_credentials: tuple[str, str]) -> Iterator[TestClient]:
    """A TestClient against the real app, real isolated storage, and
    real (known) credentials. Wrapped in `with` so the app's lifespan
    runs — db.init_db(), scheduler start/stop — matching how it actually
    boots, not a bare instantiation that'd skip all of that.
    """
    from app.main import app

    with TestClient(app) as c:
        yield c


@pytest.fixture
def auth(auth_credentials: tuple[str, str]) -> tuple[str, str]:
    """Alias with a shorter name for the common case of passing
    `auth=auth` to a request."""
    return auth_credentials


@pytest.fixture
def make_audio_file():
    """Factory: create an empty placeholder file with an audio extension.
    Enough for tests that only need "a file mutagen will be asked to
    open" — those tests mock MutagenFile itself rather than needing a
    genuinely valid audio container (see tests/test_cover_art.py and
    tests/test_album_details.py for why: constructing real, valid
    FLAC/MP3/MP4 containers byte-for-byte isn't worth it when the
    branching logic under test is "which attribute did this object
    have", not "can mutagen actually decode this format").
    """
    def _make(dir_path: Path, name: str = "01.flac") -> Path:
        dir_path.mkdir(parents=True, exist_ok=True)
        f = dir_path / name
        f.touch()
        return f
    return _make


@pytest.fixture
def sqlite_readonly(isolated_env: IsolatedEnv):
    """Factory-style fixture: call `sqlite_readonly()` after the db file
    exists (e.g. after the app's lifespan has run once) to make it
    unwritable, reproducing the exact "attempt to write a readonly
    database" failure mode this suite regression-tests against."""
    import stat

    def _make_readonly() -> None:
        db_path = settings.db_path
        db_path.chmod(stat.S_IRUSR | stat.S_IRGRP | stat.S_IROTH)
        db_path.parent.chmod(stat.S_IRUSR | stat.S_IXUSR | stat.S_IRGRP | stat.S_IXGRP)

    yield _make_readonly

    # Restore write perms so pytest's own tmp_path cleanup doesn't fail.
    try:
        db_path = settings.db_path
        db_path.parent.chmod(stat.S_IRWXU)
        db_path.chmod(stat.S_IRUSR | stat.S_IWUSR)
    except FileNotFoundError:
        pass