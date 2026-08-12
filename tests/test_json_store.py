"""app.storage.json_store — atomic writes and the default-seeding fix.

test_fresh_default_is_deep_copy is a regression test for a real bug:
JsonFile._fresh_default() used to always return a truly empty [] or {}
regardless of what `default` actually contained, discarding any seed
content (e.g. excluded_artists.json's Western-acts seed list) the first
time the file was read before ever being written. Fixed by deep-copying
the real default instead.
"""

from __future__ import annotations

from pathlib import Path

from app.storage.json_store import JsonFile


def test_read_missing_file_returns_default(tmp_path: Path):
    f = JsonFile(tmp_path / "nope.json", {"a": 1})
    assert f.read() == {"a": 1}


def test_fresh_default_is_deep_copy(tmp_path: Path):
    """A non-empty default (e.g. excluded_artists.json's seed list) must
    actually come back on first read, not be silently discarded."""
    f = JsonFile(tmp_path / "seeded.json", ["Linkin Park", "Thousand Foot Krutch"])
    assert f.read() == ["Linkin Park", "Thousand Foot Krutch"]


def test_mutating_one_read_does_not_leak_into_next(tmp_path: Path):
    f = JsonFile(tmp_path / "nope.json", {})
    first = f.read()
    first["polluted"] = True
    second = f.read()
    assert second == {}


def test_write_then_read_round_trips(tmp_path: Path):
    f = JsonFile(tmp_path / "data.json", {})
    f.write({"hello": "world", "n": 3})
    assert f.read() == {"hello": "world", "n": 3}


def test_write_is_atomic_no_tmp_file_left_behind(tmp_path: Path):
    f = JsonFile(tmp_path / "data.json", {})
    f.write({"a": 1})
    leftovers = list(tmp_path.glob("*.tmp"))
    assert leftovers == []


def test_corrupt_file_falls_back_to_default(tmp_path: Path):
    path = tmp_path / "corrupt.json"
    path.write_text("{not valid json")
    f = JsonFile(path, {"fallback": True})
    assert f.read() == {"fallback": True}
