"""Shared fixtures.

The one non-obvious thing every test in this suite relies on is
`isolated_env` (and, underneath it, `_settings_at_class_defaults`) below,
so it's worth spelling out why they look the way they do.

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

What *does* work, and is what the fixtures below do: mutate the existing
singleton objects' attributes in place, rather than trying to replace
them. Every module holds a reference to the *same* `settings`/`store`
object, so an in-place attribute change is visible everywhere
immediately — this is the standard pattern for isolating tests around a
shared singleton, and it's why `monkeypatch` (which reverts everything
automatically at test teardown) rather than manual mutation is used
throughout.

A real, initially-missed subtlety this file used to get wrong: `Settings`
reads `.env` (relative to cwd) by default, and `pytest_configure` used to
try disabling that by setting `Settings.model_config["env_file"] = None`.
That *looks* right but doesn't actually work for the module-level
`settings` singleton — this file's own top-level `from app.config import
Settings, settings` runs (constructing that singleton, reading whatever
.env exists on disk at that moment) *before* pytest ever calls the
pytest_configure hook, since Python executes a module's imports at load
time, ahead of any hook function pytest goes on to invoke. So the flag
only ever affected *fresh* `Settings()` constructions made later
elsewhere (e.g. inside `update_settings`'s validate-before-save step) —
the shared singleton itself stayed built from real `.env` content the
whole time. Caught when a test that assumed `discord_webhook_artist`
defaulted to empty instead hit a real (leftover test) Discord webhook
URL from this checkout's own `.env` and got back a genuine HTTP 403.

`_settings_at_class_defaults` below is the actual fix: reset every field
on the shared singleton to its true class-declared default —
`Settings.model_construct()` builds an instance from field defaults
alone, bypassing env vars and `.env` entirely, so it's a reliable source
of "what this field is with zero external influence" — before
`isolated_env`/`auth_credentials` layer their own specific values on top.
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
    """Belt-and-suspenders: still disables `.env` loading for any *fresh*
    `Settings()` construction a test or the app code makes during the
    run (the module-level singleton itself is handled by
    `_settings_at_class_defaults` below, not this — see this file's
    docstring for why those are two different problems).
    """
    Settings.model_config["env_file"] = None


class IsolatedEnv(NamedTuple):
    data_dir: Path
    music_dir: Path


@pytest.fixture
def _settings_at_class_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    """Reset every field on the shared `settings` singleton to its true
    class default, so a test starts from known, reproducible values
    regardless of whatever real `.env` file happens to exist wherever
    the suite is being run — see this file's docstring for why this is
    necessary and `pytest_configure` alone doesn't cover it.

    A dependency of `isolated_env`/`auth_credentials` (both of which
    nearly every test uses via the `client` fixture) rather than a
    standalone `autouse` fixture, so it's guaranteed to run before either
    of those layer their own specific values on top — relying on
    autouse-vs-autouse ordering between independent fixtures isn't
    something to lean on.
    """
    defaults = Settings.model_construct()
    for field_name in Settings.model_fields:
        monkeypatch.setattr(settings, field_name, getattr(defaults, field_name))


@pytest.fixture
def isolated_env(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, _settings_at_class_defaults: None,
) -> IsolatedEnv:
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
        ("field_overrides", "field_overrides.json"),
    ]:
        json_file = getattr(store, attr, None)
        if json_file is not None:
            monkeypatch.setattr(json_file, "path", data_dir / filename)

    return IsolatedEnv(data_dir=data_dir, music_dir=music_dir)


@pytest.fixture
def auth_credentials(
    monkeypatch: pytest.MonkeyPatch, _settings_at_class_defaults: None,
) -> tuple[str, str]:
    """Deterministic, known-good web UI credentials for this test —
    mutating both the live singleton (most code paths) and the env var
    (anywhere a fresh `Settings()` gets constructed, e.g. the Settings
    page's validate-before-save step) so both stay consistent.

    Depends on `_settings_at_class_defaults` explicitly — same as
    `isolated_env` — even though neither fixture depends on the other:
    both being downstream of that single, once-only reset is what
    guarantees it can't run *between* them and clobber whichever one
    happened to set its values first. See this file's docstring.
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