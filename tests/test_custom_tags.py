"""app.core.custom_tags — raw ID3 TXXX / MP4 freeform read/write for
formats whose mutagen "easy" interface can't see or accept an
unregistered tag key. FLAC/OGG/OPUS/APE need none of this (their easy
interface is already a free-form dict), so those are just no-ops here.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from mutagen.id3 import TXXX
from mutagen.mp4 import MP4FreeForm

import app.core.custom_tags as ct


class _FakeID3Tags(dict):
    """Mimics real mutagen.id3.ID3 well enough for our purposes:
    setall()/delall() key a list of frames by hash-key, and .values()
    yields the frame objects themselves (flattened), matching how a real
    ID3 object's .values() behaves."""

    def setall(self, key, framelist) -> None:
        self[key] = list(framelist)

    def delall(self, key) -> None:
        self.pop(key, None)

    def values(self):
        out = []
        for framelist in dict.values(self):
            out.extend(framelist)
        return out


class _FakeAudio:
    def __init__(self, tags=None, make_tags=None) -> None:
        self.tags = tags
        self._make_tags = make_tags
        self.saved = False

    def add_tags(self) -> None:
        self.tags = self._make_tags() if self._make_tags else {}

    def save(self) -> None:
        self.saved = True


# ── read_custom_tags ─────────────────────────────────────────────────────
def test_read_custom_tags_collects_txxx_frames_on_mp3():
    tags = _FakeID3Tags()
    tags.setall("TXXX:MyTag", [TXXX(encoding=3, desc="MyTag", text=["hello"])])
    audio = _FakeAudio(tags=tags)

    with patch.object(ct, "MutagenFile", return_value=audio):
        result = ct.read_custom_tags(Path("song.mp3"))

    assert result == {"MyTag": "hello"}


def test_read_custom_tags_collects_freeform_atoms_on_m4a():
    tags = {"----:com.apple.iTunes:MyTag": [MP4FreeForm(b"hello")]}
    audio = _FakeAudio(tags=tags)

    with patch.object(ct, "MutagenFile", return_value=audio):
        result = ct.read_custom_tags(Path("song.m4a"))

    assert result == {"MyTag": "hello"}


def test_read_custom_tags_empty_for_native_custom_formats():
    """FLAC/OGG/etc. never even open the file — the easy interface's own
    dict already covers arbitrary keys for those."""
    with patch.object(ct, "MutagenFile") as mock_mutagen:
        result = ct.read_custom_tags(Path("song.flac"))

    assert result == {}
    mock_mutagen.assert_not_called()


def test_read_custom_tags_no_tags_returns_empty():
    audio = _FakeAudio(tags=None)
    with patch.object(ct, "MutagenFile", return_value=audio):
        assert ct.read_custom_tags(Path("song.mp3")) == {}


# ── write_custom_tag ─────────────────────────────────────────────────────
def test_write_custom_tag_sets_txxx_frame_on_mp3():
    tags = _FakeID3Tags()
    audio = _FakeAudio(tags=tags)

    with patch.object(ct, "MutagenFile", return_value=audio):
        ok = ct.write_custom_tag(Path("song.mp3"), "MyTag", "value")

    assert ok is True
    assert audio.saved is True
    frame = tags["TXXX:MyTag"][0]
    assert frame.desc == "MyTag"
    assert frame.text == ["value"]


def test_write_custom_tag_sets_freeform_atom_on_m4a():
    tags: dict = {}
    audio = _FakeAudio(tags=tags)

    with patch.object(ct, "MutagenFile", return_value=audio):
        ok = ct.write_custom_tag(Path("song.m4a"), "MyTag", "value")

    assert ok is True
    assert audio.saved is True
    assert bytes(tags["----:com.apple.iTunes:MyTag"][0]) == b"value"


def test_write_custom_tag_adds_tags_container_if_missing():
    audio = _FakeAudio(tags=None, make_tags=_FakeID3Tags)

    with patch.object(ct, "MutagenFile", return_value=audio):
        ok = ct.write_custom_tag(Path("song.mp3"), "MyTag", "value")

    assert ok is True
    assert "TXXX:MyTag" in audio.tags


def test_write_custom_tag_unsupported_format_is_noop():
    with patch.object(ct, "MutagenFile") as mock_mutagen:
        ok = ct.write_custom_tag(Path("song.flac"), "MyTag", "value")

    assert ok is False
    mock_mutagen.assert_not_called()


# ── delete_custom_tag ────────────────────────────────────────────────────
def test_delete_custom_tag_removes_txxx_frame():
    tags = _FakeID3Tags()
    tags.setall("TXXX:MyTag", [TXXX(encoding=3, desc="MyTag", text=["value"])])
    audio = _FakeAudio(tags=tags)

    with patch.object(ct, "MutagenFile", return_value=audio):
        ok = ct.delete_custom_tag(Path("song.mp3"), "MyTag")

    assert ok is True
    assert "TXXX:MyTag" not in tags


def test_delete_custom_tag_removes_freeform_atom():
    tags = {"----:com.apple.iTunes:MyTag": [MP4FreeForm(b"value")]}
    audio = _FakeAudio(tags=tags)

    with patch.object(ct, "MutagenFile", return_value=audio):
        ok = ct.delete_custom_tag(Path("song.m4a"), "MyTag")

    assert ok is True
    assert tags == {}


def test_delete_custom_tag_missing_key_returns_false():
    audio = _FakeAudio(tags={})
    with patch.object(ct, "MutagenFile", return_value=audio):
        assert ct.delete_custom_tag(Path("song.m4a"), "Nope") is False
