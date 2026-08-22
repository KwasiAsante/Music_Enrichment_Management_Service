"""app.core.field_overrides — per-album manual tag-field overrides.

Covers the read side (candidate values built from a VGMDB payload, plus
warnings when there's no VGMDB mapping), the CRUD write side, and the
apply_overrides() tag-write step BeetsEnricher calls post-import.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

import app.core.field_overrides as fo
from app.core.vgmdb_client import VGMDBClient
from app.storage.json_store import store

VGMDB_PAYLOAD = {
    "catalog": "ABC-001",
    "category": "Video Game Music",
    "publisher": {"names": {"en": "Square Enix"}},
    "composers": [{"names": {"en": "Some Session Composer"}}],
    "performers": [{"names": {"en": "Real Band Name", "ja": "リアルバンド"}}],
    "arrangers": [{"names": {"en": "An Arranger"}}],
    "lyricists": [],
}


def _seed_album(mb_release_id: str = "mb-1", vgmdb_id: str | None = "999") -> None:
    store.album_list.write({
        "TestAlbum": {
            "artist": "Real Band Name", "album": "Test OST",
            "mb_release_id": mb_release_id, "folder": "Real Band Name/Test OST",
        },
    })
    if vgmdb_id:
        store.vgmdb_mapping.write({
            mb_release_id: {
                "vgmdb_id": vgmdb_id, "artist": "Real Band Name", "album": "Test OST",
                "folder": "Real Band Name/Test OST", "source": "manual",
            },
        })


# ── get_options ──────────────────────────────────────────────────────────
def test_get_options_unknown_folder_raises(isolated_env):
    with pytest.raises(ValueError):
        fo.FieldOverrideService().get_options("Nope/Nope")


def test_get_options_builds_candidates_from_vgmdb(isolated_env):
    _seed_album()
    with patch.object(VGMDBClient, "get_album", return_value=VGMDB_PAYLOAD), \
         patch.object(fo.FieldOverrideService, "_read_current_tags", return_value={}):
        options = fo.FieldOverrideService().get_options("Real Band Name/Test OST")

    # "artist" pools composers + performers + arrangers — the actual fix
    # case: the real band name is credited as a *performer*, not composer.
    artist_field = next(f for f in options["fields"] if f["field"] == "artist")
    values = {c["value"] for c in artist_field["candidates"]}
    assert "Some Session Composer" in values
    assert "Real Band Name" in values
    assert "リアルバンド" in values

    catalog_field = next(f for f in options["fields"] if f["field"] == "catalog")
    assert catalog_field["candidates"] == [{"value": "ABC-001", "label": "VGMDB catalog"}]

    label_field = next(f for f in options["fields"] if f["field"] == "label")
    assert label_field["candidates"] == [{"value": "Square Enix", "label": "VGMDB label (English)"}]


def test_get_options_no_vgmdb_mapping_warns_and_has_no_candidates(isolated_env):
    _seed_album(vgmdb_id=None)
    with patch.object(fo.FieldOverrideService, "_read_current_tags", return_value={}):
        options = fo.FieldOverrideService().get_options("Real Band Name/Test OST")

    assert options["mapped"] is False
    assert any("no VGMDB mapping" in w for w in options["warnings"])
    assert all(f["candidates"] == [] for f in options["fields"])


def test_get_options_surfaces_saved_override(isolated_env):
    _seed_album()
    store.field_overrides.write({
        "Real Band Name/Test OST": {
            "mb_release_id": "mb-1", "artist": "Real Band Name", "album": "Test OST",
            "fields": {"artist": "Real Band Name"},
        },
    })
    with patch.object(VGMDBClient, "get_album", return_value=VGMDB_PAYLOAD), \
         patch.object(fo.FieldOverrideService, "_read_current_tags", return_value={}):
        options = fo.FieldOverrideService().get_options("Real Band Name/Test OST")

    artist_field = next(f for f in options["fields"] if f["field"] == "artist")
    assert artist_field["override_value"] == "Real Band Name"


def test_get_options_vgmdb_failure_degrades_gracefully(isolated_env):
    _seed_album()
    with patch.object(VGMDBClient, "get_album", side_effect=Exception("connection refused")), \
         patch.object(fo.FieldOverrideService, "_read_current_tags", return_value={}):
        options = fo.FieldOverrideService().get_options("Real Band Name/Test OST")

    assert any("VGMDB lookup failed" in w for w in options["warnings"])
    assert all(f["candidates"] == [] for f in options["fields"])


# ── set / delete ─────────────────────────────────────────────────────────
def test_set_overrides_merges_and_persists(isolated_env):
    _seed_album()
    svc = fo.FieldOverrideService()
    entry = svc.set_overrides("Real Band Name/Test OST", {"artist": "Real Band Name"})
    assert entry["fields"] == {"artist": "Real Band Name"}

    entry = svc.set_overrides("Real Band Name/Test OST", {"genre": "Rock"})
    assert entry["fields"] == {"artist": "Real Band Name", "genre": "Rock"}


def test_set_overrides_empty_value_clears_one_field(isolated_env):
    _seed_album()
    svc = fo.FieldOverrideService()
    svc.set_overrides("Real Band Name/Test OST", {"artist": "X", "genre": "Y"})
    entry = svc.set_overrides("Real Band Name/Test OST", {"artist": ""})
    assert entry["fields"] == {"genre": "Y"}


def test_set_overrides_clearing_last_field_drops_the_whole_entry(isolated_env):
    _seed_album()
    svc = fo.FieldOverrideService()
    svc.set_overrides("Real Band Name/Test OST", {"artist": "X"})
    svc.set_overrides("Real Band Name/Test OST", {"artist": ""})
    assert store.field_overrides.read() == {}


def test_set_overrides_rejects_unknown_field(isolated_env):
    _seed_album()
    with pytest.raises(ValueError):
        fo.FieldOverrideService().set_overrides("Real Band Name/Test OST", {"bogus": "x"})


def test_set_overrides_unknown_album_raises(isolated_env):
    with pytest.raises(ValueError):
        fo.FieldOverrideService().set_overrides("Nope/Nope", {"artist": "x"})


def test_delete_overrides(isolated_env):
    _seed_album()
    svc = fo.FieldOverrideService()
    svc.set_overrides("Real Band Name/Test OST", {"artist": "X"})
    assert svc.delete_overrides("Real Band Name/Test OST") is True
    assert svc.delete_overrides("Real Band Name/Test OST") is False


# ── apply_overrides (post beet-import tag-write step) ─────────────────────
class _FakeTags(dict):
    pass


class _FakeAudio:
    def __init__(self) -> None:
        self.tags = _FakeTags()
        self.saved = False

    def save(self) -> None:
        self.saved = True


def test_apply_overrides_writes_expected_tag_keys(isolated_env, tmp_path):
    album_dir = tmp_path / "album"
    album_dir.mkdir()
    (album_dir / "01.flac").touch()
    (album_dir / "02.flac").touch()

    store.field_overrides.write({
        "Real Band Name/Test OST": {
            "mb_release_id": "mb-1", "artist": "Real Band Name", "album": "Test OST",
            "fields": {"artist": "Real Band Name", "genre": "Rock"},
        },
    })

    fakes: list[_FakeAudio] = []

    def _fake_mutagen(path, easy=True):  # noqa: ARG001
        audio = _FakeAudio()
        fakes.append(audio)
        return audio

    with patch.object(fo, "MutagenFile", side_effect=_fake_mutagen):
        fixed = fo.FieldOverrideService().apply_overrides(album_dir, "Real Band Name/Test OST")

    assert fixed == 2
    assert len(fakes) == 2 and all(f.saved for f in fakes)
    for f in fakes:
        assert f.tags["albumartist"] == ["Real Band Name"]
        assert f.tags["artist"] == ["Real Band Name"]
        assert f.tags["genre"] == ["Rock"]


def test_apply_overrides_is_noop_when_nothing_saved(isolated_env, tmp_path):
    album_dir = tmp_path / "album"
    album_dir.mkdir()
    (album_dir / "01.flac").touch()

    with patch.object(fo, "MutagenFile") as mock_mutagen:
        fixed = fo.FieldOverrideService().apply_overrides(album_dir, "Nothing/Saved")

    assert fixed == 0
    mock_mutagen.assert_not_called()


def test_apply_overrides_tolerates_a_tag_key_the_format_rejects(isolated_env, tmp_path):
    """A format that rejects one tag key (e.g. M4A's restricted easy-tag
    set rejecting 'composer') shouldn't block the rest of that file's
    overrides or the album — mirrors
    BeetsEnricher._fix_non_latin_artist_tags' per-file tolerance."""
    album_dir = tmp_path / "album"
    album_dir.mkdir()
    (album_dir / "01.m4a").touch()

    store.field_overrides.write({
        "Some/Folder": {"fields": {"artist": "X", "composer": "Y"}},
    })

    class _PickyTags(_FakeTags):
        def __setitem__(self, key, value):
            if key == "composer":
                raise ValueError("unsupported easy key")
            super().__setitem__(key, value)

    class _PickyAudio(_FakeAudio):
        def __init__(self) -> None:
            super().__init__()
            self.tags = _PickyTags()

    with patch.object(fo, "MutagenFile", return_value=_PickyAudio()):
        fixed = fo.FieldOverrideService().apply_overrides(album_dir, "Some/Folder")

    assert fixed == 1  # albumartist/artist still got written despite composer failing
