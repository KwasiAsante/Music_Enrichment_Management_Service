"""app.core.tag_fields — shared standard+custom tag read/write used by
both TagEditorService and TagLockService.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

import app.core.tag_fields as tf


class _FakeTags(dict):
    pass


class _FakeAudio:
    def __init__(self, tags: dict | None = None) -> None:
        self.tags = tags if tags is not None else _FakeTags()
        self.saved = False

    def save(self) -> None:
        self.saved = True


def _make_album(tmp_path, *filenames: str):
    album_dir = tmp_path / "album"
    album_dir.mkdir()
    for name in filenames:
        (album_dir / name).touch()
    return album_dir


# ── STANDARD_FIELDS ──────────────────────────────────────────────────────
def test_standard_fields_extend_field_overrides_with_album_and_date():
    assert "artist" in tf.STANDARD_FIELDS
    assert "genre" in tf.STANDARD_FIELDS
    assert "album" in tf.STANDARD_FIELDS
    assert "date" in tf.STANDARD_FIELDS


# ── classify_tag_source ───────────────────────────────────────────────────
def test_classify_tag_source_standard_field():
    assert tf.classify_tag_source("genre") == "standard"
    assert tf.classify_tag_source("catalog") == "standard"


def test_classify_tag_source_musicbrainz_vorbis_style():
    assert tf.classify_tag_source("musicbrainz_albumid") == "musicbrainz"
    assert tf.classify_tag_source("MUSICBRAINZ_ALBUMARTISTID") == "musicbrainz"


def test_classify_tag_source_musicbrainz_id3_desc_style():
    """The literal ID3 TXXX/MP4-freeform desc beets/Picard write is
    space-separated title case, e.g. 'MusicBrainz Album Id' — not the
    lowercase/underscore Vorbis-comment spelling."""
    assert tf.classify_tag_source("MusicBrainz Album Id") == "musicbrainz"


def test_classify_tag_source_vgmdb():
    assert tf.classify_tag_source("vgmdb_id") == "vgmdb"
    assert tf.classify_tag_source("VGMDB Album Id") == "vgmdb"


def test_classify_tag_source_custom():
    assert tf.classify_tag_source("MyCustomTag") == "custom"
    assert tf.classify_tag_source("conductor") == "custom"


def test_per_track_only_keys_include_musicbrainz_recording_identifiers():
    """A library tagged by Picard before adopting this tool will already
    carry these — they must never be treated as editable album-wide
    fields, same reasoning as 'title'."""
    assert "musicbrainz_trackid" in tf.PER_TRACK_ONLY_KEYS
    assert "musicbrainz_releasetrackid" in tf.PER_TRACK_ONLY_KEYS
    assert "musicbrainz_workid" in tf.PER_TRACK_ONLY_KEYS


# ── iter_audio_files / first_audio_file ──────────────────────────────────
def test_iter_audio_files_sorted_and_filtered(tmp_path):
    album_dir = _make_album(tmp_path, "02.flac", "01.flac", "notes.txt")
    files = tf.iter_audio_files(album_dir)
    assert [f.name for f in files] == ["01.flac", "02.flac"]


def test_first_audio_file_none_for_empty_folder(tmp_path):
    album_dir = tmp_path / "empty"
    album_dir.mkdir()
    assert tf.first_audio_file(album_dir) is None


# ── read_field_value ──────────────────────────────────────────────────────
def test_read_field_value_standard_field(tmp_path):
    audio = _FakeAudio(_FakeTags({"genre": ["Rock"]}))
    with patch.object(tf, "MutagenFile", return_value=audio):
        assert tf.read_field_value(tmp_path / "01.flac", "genre") == "Rock"


def test_read_field_value_custom_field_native_dict(tmp_path):
    audio = _FakeAudio(_FakeTags({"conductor": ["Someone"]}))
    with patch.object(tf, "MutagenFile", return_value=audio):
        assert tf.read_field_value(tmp_path / "01.flac", "conductor") == "Someone"


def test_read_field_value_custom_field_falls_back_to_raw(tmp_path):
    audio = _FakeAudio(_FakeTags())  # empty — easy interface has nothing
    with patch.object(tf, "MutagenFile", return_value=audio), \
         patch.object(tf.custom_tags, "read_custom_tags", return_value={"MyTag": "hi"}):
        assert tf.read_field_value(tmp_path / "01.mp3", "MyTag") == "hi"


# ── read_album_fields ─────────────────────────────────────────────────────
def test_read_album_fields_includes_all_standard_fields_even_when_absent(tmp_path):
    album_dir = _make_album(tmp_path, "01.flac")
    audio = _FakeAudio(_FakeTags({"genre": ["Rock"]}))
    with patch.object(tf, "MutagenFile", return_value=audio):
        fields = tf.read_album_fields(album_dir)

    assert fields["genre"] == "Rock"
    assert "artist" in fields and fields["artist"] is None
    assert "date" in fields and fields["date"] is None


def test_read_album_fields_surfaces_a_custom_vorbis_key(tmp_path):
    album_dir = _make_album(tmp_path, "01.flac")
    audio = _FakeAudio(_FakeTags({"genre": ["Rock"], "mycustomfield": ["Value"]}))
    with patch.object(tf, "MutagenFile", return_value=audio):
        fields = tf.read_album_fields(album_dir)

    assert fields["mycustomfield"] == "Value"


def test_read_album_fields_merges_raw_custom_tags_on_mp3(tmp_path):
    album_dir = _make_album(tmp_path, "01.mp3")
    audio = _FakeAudio(_FakeTags({"genre": ["Rock"]}))
    with patch.object(tf, "MutagenFile", return_value=audio), \
         patch.object(tf.custom_tags, "read_custom_tags", return_value={"MyTag": "hi"}):
        fields = tf.read_album_fields(album_dir)

    assert fields["MyTag"] == "hi"


def test_read_album_fields_standard_fields_still_come_from_first_file_only(tmp_path):
    """Standard fields keep the existing "first file is representative"
    convention — only the custom-field scan needs every file, to check
    for per-track variation (see the tests below)."""
    album_dir = _make_album(tmp_path, "01.flac", "02.flac")
    audios = iter([
        _FakeAudio(_FakeTags({"genre": ["Rock"]})),
        _FakeAudio(_FakeTags({"genre": ["Jazz"]})),
    ])

    with patch.object(tf, "MutagenFile", side_effect=lambda path, easy=True: next(audios)):
        fields = tf.read_album_fields(album_dir)

    assert fields["genre"] == "Rock"


def test_read_album_fields_excludes_a_per_track_key_like_title(tmp_path):
    """The exact bug this guards against: 'title' differs on every
    track, so it must never be offered as a single editable/lockable
    album-wide value — saving it would overwrite every track's title."""
    album_dir = _make_album(tmp_path, "01.flac", "02.flac")
    audios = iter([
        _FakeAudio(_FakeTags({"title": ["Papercut"]})),
        _FakeAudio(_FakeTags({"title": ["One Step Closer"]})),
    ])

    with patch.object(tf, "MutagenFile", side_effect=lambda path, easy=True: next(audios)):
        fields = tf.read_album_fields(album_dir)

    assert "title" not in fields


def test_read_album_fields_excludes_a_custom_key_that_varies_per_track(tmp_path):
    album_dir = _make_album(tmp_path, "01.flac", "02.flac")
    audios = iter([
        _FakeAudio(_FakeTags({"mynote": ["first"]})),
        _FakeAudio(_FakeTags({"mynote": ["second"]})),
    ])

    with patch.object(tf, "MutagenFile", side_effect=lambda path, easy=True: next(audios)):
        fields = tf.read_album_fields(album_dir)

    assert "mynote" not in fields


def test_read_album_fields_includes_a_custom_key_uniform_across_tracks(tmp_path):
    album_dir = _make_album(tmp_path, "01.flac", "02.flac")
    audios = iter([
        _FakeAudio(_FakeTags({"mood": ["Energetic"]})),
        _FakeAudio(_FakeTags({"mood": ["Energetic"]})),
    ])

    with patch.object(tf, "MutagenFile", side_effect=lambda path, easy=True: next(audios)):
        fields = tf.read_album_fields(album_dir)

    assert fields["mood"] == "Energetic"


def test_read_album_fields_empty_folder_still_has_standard_keys(tmp_path):
    album_dir = tmp_path / "empty"
    album_dir.mkdir()
    fields = tf.read_album_fields(album_dir)
    assert set(tf.STANDARD_FIELDS) <= set(fields)
    assert all(v is None for v in fields.values())


# ── write_album_field ─────────────────────────────────────────────────────
def test_write_album_field_rejects_a_per_track_key(tmp_path):
    album_dir = _make_album(tmp_path, "01.flac")
    with patch.object(tf, "MutagenFile") as mock_mutagen:
        with pytest.raises(ValueError):
            tf.write_album_field(album_dir, "title", "New Title")

    mock_mutagen.assert_not_called()


def test_write_album_field_writes_standard_field_across_files(tmp_path):
    album_dir = _make_album(tmp_path, "01.flac", "02.flac")
    fakes: list[_FakeAudio] = []

    def _fake_mutagen(path, easy=True):  # noqa: ARG001
        audio = _FakeAudio()
        fakes.append(audio)
        return audio

    with patch.object(tf, "MutagenFile", side_effect=_fake_mutagen):
        touched = tf.write_album_field(album_dir, "genre", "Rock")

    assert touched == 2
    assert all(f.tags["genre"] == ["Rock"] and f.saved for f in fakes)


def test_write_album_field_empty_value_deletes_key(tmp_path):
    album_dir = _make_album(tmp_path, "01.flac")
    audio = _FakeAudio(_FakeTags({"genre": ["Rock"]}))

    with patch.object(tf, "MutagenFile", return_value=audio):
        touched = tf.write_album_field(album_dir, "genre", "")

    assert touched == 1
    assert "genre" not in audio.tags
    assert audio.saved is True


def test_write_album_field_standard_field_never_falls_back_to_custom(tmp_path):
    """A standard field a format's easy interface rejects for every
    candidate key is just skipped — it must never land in a TXXX/freeform
    tag instead, or it'd live somewhere the enrichment pipeline doesn't
    look."""
    album_dir = _make_album(tmp_path, "01.m4a")

    class _PickyTags(_FakeTags):
        def __setitem__(self, key, value):
            raise KeyError(f"{key!r} not supported")

    audio = _FakeAudio(_PickyTags())
    with patch.object(tf, "MutagenFile", return_value=audio), \
         patch.object(tf.custom_tags, "write_custom_tag") as mock_write:
        touched = tf.write_album_field(album_dir, "composer", "Someone")

    assert touched == 0
    mock_write.assert_not_called()


def test_write_album_field_custom_field_native_dict_skips_raw_fallback(tmp_path):
    album_dir = _make_album(tmp_path, "01.flac")
    audio = _FakeAudio(_FakeTags())

    with patch.object(tf, "MutagenFile", return_value=audio), \
         patch.object(tf.custom_tags, "write_custom_tag") as mock_write:
        touched = tf.write_album_field(album_dir, "MyCustom", "value")

    assert touched == 1
    assert audio.tags["MyCustom"] == ["value"]
    mock_write.assert_not_called()


def test_write_album_field_custom_field_falls_back_to_raw_on_mp3(tmp_path):
    album_dir = _make_album(tmp_path, "01.mp3")

    class _RestrictedTags(_FakeTags):
        def __setitem__(self, key, value):
            raise KeyError(f"{key!r} not a registered easy key")

    audio = _FakeAudio(_RestrictedTags())
    with patch.object(tf, "MutagenFile", return_value=audio), \
         patch.object(tf.custom_tags, "write_custom_tag", return_value=True) as mock_write:
        touched = tf.write_album_field(album_dir, "MyCustom", "value")

    assert touched == 1
    mock_write.assert_called_once_with(album_dir / "01.mp3", "MyCustom", "value")


def test_write_album_field_custom_field_empty_value_deletes_via_raw_fallback(tmp_path):
    album_dir = _make_album(tmp_path, "01.mp3")

    class _RestrictedTags(_FakeTags):
        def __setitem__(self, key, value):
            raise KeyError("not registered")

    audio = _FakeAudio(_RestrictedTags())
    with patch.object(tf, "MutagenFile", return_value=audio), \
         patch.object(tf.custom_tags, "delete_custom_tag", return_value=True) as mock_delete:
        touched = tf.write_album_field(album_dir, "MyCustom", "")

    assert touched == 1
    mock_delete.assert_called_once_with(album_dir / "01.mp3", "MyCustom")


# ── read_track_field_by_position / write_track_field_by_position ─────────
def test_track_lockable_fields_are_title_and_artist():
    assert tf.TRACK_LOCKABLE_FIELDS == ("title", "artist")


def test_read_track_field_by_position_keys_by_disc_and_track(tmp_path):
    album_dir = _make_album(tmp_path, "01.flac", "02.flac")
    audios = iter([
        _FakeAudio(_FakeTags({"title": ["One"], "tracknumber": ["1"], "discnumber": ["1"]})),
        _FakeAudio(_FakeTags({"title": ["Two"], "tracknumber": ["2"], "discnumber": ["1"]})),
    ])

    with patch.object(tf, "MutagenFile", side_effect=lambda path, easy=True: next(audios)):
        values = tf.read_track_field_by_position(album_dir, "title")

    assert values == {("1", "1"): "One", ("1", "2"): "Two"}


def test_read_track_field_by_position_defaults_missing_discnumber_to_one(tmp_path):
    album_dir = _make_album(tmp_path, "01.flac")
    audio = _FakeAudio(_FakeTags({"title": ["Solo"], "tracknumber": ["1"]}))

    with patch.object(tf, "MutagenFile", return_value=audio):
        values = tf.read_track_field_by_position(album_dir, "title")

    assert values == {("1", "1"): "Solo"}


def test_read_track_field_by_position_uses_bare_artist_key_not_albumartist(tmp_path):
    """Track-level artist locking must never be confused with the
    album-wide 'artist' field (which also writes albumartist) — only the
    plain ARTIST tag is read/written here."""
    album_dir = _make_album(tmp_path, "01.flac")
    audio = _FakeAudio(_FakeTags({
        "artist": ["Track Artist"], "albumartist": ["Album Artist"], "tracknumber": ["1"],
    }))

    with patch.object(tf, "MutagenFile", return_value=audio):
        values = tf.read_track_field_by_position(album_dir, "artist")

    assert values == {("1", "1"): "Track Artist"}


def test_write_track_field_by_position_writes_each_track_its_own_value(tmp_path):
    album_dir = _make_album(tmp_path, "01.flac", "02.flac")
    fakes = {
        "01.flac": _FakeAudio(_FakeTags({"tracknumber": ["1"], "discnumber": ["1"]})),
        "02.flac": _FakeAudio(_FakeTags({"tracknumber": ["2"], "discnumber": ["1"]})),
    }

    with patch.object(tf, "MutagenFile", side_effect=lambda path, easy=True: fakes[Path(path).name]):
        touched = tf.write_track_field_by_position(
            album_dir, "title", {("1", "1"): "One (restored)", ("1", "2"): "Two (restored)"},
        )

    assert touched == 2
    assert fakes["01.flac"].tags["title"] == ["One (restored)"]
    assert fakes["02.flac"].tags["title"] == ["Two (restored)"]


def test_write_track_field_by_position_skips_a_track_with_no_snapshotted_value(tmp_path):
    """A track added to the album since the snapshot was taken (or one
    the snapshot just doesn't know about) is left untouched rather than
    guessed at."""
    album_dir = _make_album(tmp_path, "01.flac")
    audio = _FakeAudio(_FakeTags({"tracknumber": ["1"], "discnumber": ["1"], "title": ["Untouched"]}))

    with patch.object(tf, "MutagenFile", return_value=audio):
        touched = tf.write_track_field_by_position(album_dir, "title", {("1", "2"): "Someone Else's Track"})

    assert touched == 0
    assert audio.tags["title"] == ["Untouched"]


def test_write_track_field_by_position_matches_by_position_even_if_renamed(tmp_path):
    """The whole point of keying by (disc, track) rather than file path:
    beets may rename the file itself based on the newly-written title, but
    the track's own position tag stays put, so restore still finds it."""
    album_dir = _make_album(tmp_path, "01 - New Title.flac")
    audio = _FakeAudio(_FakeTags({"tracknumber": ["1"], "discnumber": ["1"]}))

    with patch.object(tf, "MutagenFile", return_value=audio):
        touched = tf.write_track_field_by_position(album_dir, "title", {("1", "1"): "Old Title"})

    assert touched == 1
    assert audio.tags["title"] == ["Old Title"]
