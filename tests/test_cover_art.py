"""app.core.cover_art — front/back cover detection.

Covers all four sources in priority order: folder-level cover files,
then embedded pictures in FLAC/ID3/MP4/Ogg, with front/back
classification by picture-type marker (ID3 and FLAC share the same
numbering: 3 = front, 4 = back) or positional fallback where the format
has no such marker (MP4's covr).
"""

from __future__ import annotations

import base64
from pathlib import Path
from unittest.mock import patch

import app.core.cover_art as ca

# A real, tiny, valid 1x1 white JPEG — used for the "real folder file"
# tests, where the code actually reads bytes off disk rather than going
# through a mocked mutagen object.
_TINY_JPEG = base64.b64decode(
    "/9j/4AAQSkZJRgABAQEAYABgAAD/2wBDAAgGBgcGBQgHBwcJCQgKDBQNDAsLDBkSEw8UHRofHh0a"
    "HBwgJC4nICIsIxwcKDcpLDAxNDQ0Hyc5PTgyPC4zNDL/2wBDAQkJCQwLDBgNDRgyIRwhMjIyMjIy"
    "MjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIy/8AAEQgAAQABAwEiAAIR"
    "AQMRAf/EABUAAQEAAAAAAAAAAAAAAAAAAAAI/8QAFBABAAAAAAAAAAAAAAAAAAAAAP/EABQBAQAA"
    "AAAAAAAAAAAAAAAAAAD/xAAUEQEAAAAAAAAAAAAAAAAAAAAA/9oADAMBAAIRAxEAPwCdABmX/9k="
)

FRONT_MAGIC = b"\xff\xd8\xff\xe0FRONTDATA"
BACK_MAGIC = b"\xff\xd8\xff\xe0BACKDATA"


class FakePicture:
    """Mimics both mutagen.id3.APIC and mutagen.flac.Picture — both
    expose .data / .mime / .type with identical numbering."""
    def __init__(self, data: bytes, mime: str, type_: int):
        self.data, self.mime, self.type = data, mime, type_


# ── folder-level cover files ────────────────────────────────────────────
def test_finds_front_cover_file(tmp_path: Path):
    (tmp_path / "cover.jpg").write_bytes(_TINY_JPEG)
    art = ca.find_cover_art(tmp_path)
    assert art == (_TINY_JPEG, "image/jpeg")


def test_finds_front_and_back_cover_files(tmp_path: Path):
    (tmp_path / "cover.jpg").write_bytes(b"FRONTFILE")
    (tmp_path / "back.png").write_bytes(b"BACKFILE")
    result = ca.find_album_art(tmp_path)
    assert result["front"] == (b"FRONTFILE", "image/jpeg")
    assert result["back"] == (b"BACKFILE", "image/png")


def test_no_art_anywhere_returns_empty(tmp_path: Path):
    (tmp_path / "not_a_cover.txt").write_text("hi")
    assert ca.find_album_art(tmp_path) == {}
    assert ca.find_cover_art(tmp_path) is None


def test_nonexistent_folder_returns_empty(tmp_path: Path):
    assert ca.find_album_art(tmp_path / "does_not_exist") == {}


def test_missing_back_falls_through_to_embedded(tmp_path: Path):
    (tmp_path / "folder.jpg").write_bytes(b"FRONTONLY")

    with patch.object(ca, "_first_audio_file", return_value=tmp_path / "fake.flac"), \
         patch.object(ca, "MutagenFile") as mock_mutagen:
        mock_mutagen.return_value.pictures = [FakePicture(b"EMBEDDEDBACK", "image/jpeg", 4)]
        mock_mutagen.return_value.tags = None
        result = ca.find_album_art(tmp_path)

    assert result["front"] == (b"FRONTONLY", "image/jpeg")
    assert result["back"] == (b"EMBEDDEDBACK", "image/jpeg")


# ── embedded: FLAC-style `.pictures` ─────────────────────────────────────
def test_flac_pictures_classified_by_type(tmp_path: Path):
    with patch.object(ca, "MutagenFile") as mock_mutagen:
        mock_mutagen.return_value.pictures = [
            FakePicture(BACK_MAGIC, "image/jpeg", 4),
            FakePicture(FRONT_MAGIC, "image/jpeg", 3),
        ]
        mock_mutagen.return_value.tags = None
        with patch.object(ca, "_first_audio_file", return_value=tmp_path / "x.flac"):
            result = ca._find_embedded_art(tmp_path)

    assert result["front"] == (FRONT_MAGIC, "image/jpeg")
    assert result["back"] == (BACK_MAGIC, "image/jpeg")


def test_flac_pictures_with_unknown_type_fall_back_positionally(tmp_path: Path):
    with patch.object(ca, "MutagenFile") as mock_mutagen:
        mock_mutagen.return_value.pictures = [
            FakePicture(b"OTHER", "image/jpeg", 0),  # 0 = "Other"
            FakePicture(FRONT_MAGIC, "image/jpeg", 6),  # 6 = "Media" (not front/back)
        ]
        mock_mutagen.return_value.tags = None
        with patch.object(ca, "_first_audio_file", return_value=tmp_path / "x.flac"):
            result = ca._find_embedded_art(tmp_path)

    assert result["front"] == (b"OTHER", "image/jpeg")
    assert result["back"] == (FRONT_MAGIC, "image/jpeg")


# ── embedded: ID3-style APIC frames ──────────────────────────────────────
def test_id3_apic_classified_by_type(tmp_path: Path):
    class FakeID3Tags:
        def getall(self, key):
            assert key == "APIC"
            return [FakePicture(FRONT_MAGIC, "image/jpeg", 3), FakePicture(BACK_MAGIC, "image/png", 4)]

    with patch.object(ca, "MutagenFile") as mock_mutagen:
        mock_mutagen.return_value.tags = FakeID3Tags()
        mock_mutagen.return_value.pictures = None
        with patch.object(ca, "_first_audio_file", return_value=tmp_path / "x.mp3"):
            result = ca._find_embedded_art(tmp_path)

    assert result["front"] == (FRONT_MAGIC, "image/jpeg")
    assert result["back"] == (BACK_MAGIC, "image/png")


# ── embedded: MP4 `covr` (no type marker — positional) ──────────────────
def test_mp4_covr_positional_front_and_back(tmp_path: Path):
    from mutagen.mp4 import MP4Cover

    front_cover = MP4Cover(FRONT_MAGIC, imageformat=MP4Cover.FORMAT_JPEG)
    back_cover = MP4Cover(BACK_MAGIC, imageformat=MP4Cover.FORMAT_PNG)

    class FakeMP4Tags(dict):
        pass  # real MP4Tags is a plain dict subclass, no .getall

    with patch.object(ca, "MutagenFile") as mock_mutagen:
        mock_mutagen.return_value.tags = FakeMP4Tags(covr=[front_cover, back_cover])
        mock_mutagen.return_value.pictures = None
        with patch.object(ca, "_first_audio_file", return_value=tmp_path / "x.m4a"):
            result = ca._find_embedded_art(tmp_path)

    assert result["front"] == (FRONT_MAGIC, "image/jpeg")
    assert result["back"] == (BACK_MAGIC, "image/png")


def test_mp4_single_covr_is_front_only(tmp_path: Path):
    from mutagen.mp4 import MP4Cover

    front_cover = MP4Cover(FRONT_MAGIC, imageformat=MP4Cover.FORMAT_JPEG)

    class FakeMP4Tags(dict):
        pass

    with patch.object(ca, "MutagenFile") as mock_mutagen:
        mock_mutagen.return_value.tags = FakeMP4Tags(covr=[front_cover])
        mock_mutagen.return_value.pictures = None
        with patch.object(ca, "_first_audio_file", return_value=tmp_path / "x.m4a"):
            result = ca._find_embedded_art(tmp_path)

    assert "front" in result
    assert "back" not in result


# ── embedded: Ogg/Opus base64 METADATA_BLOCK_PICTURE ─────────────────────
def test_ogg_metadata_block_picture(tmp_path: Path):
    from mutagen.flac import Picture as RealPicture

    pic = RealPicture()
    pic.data = FRONT_MAGIC
    pic.mime = "image/jpeg"
    pic.type = 3
    block = base64.b64encode(pic.write()).decode("ascii")

    class FakeOggTags(dict):
        pass

    with patch.object(ca, "MutagenFile") as mock_mutagen:
        mock_mutagen.return_value.tags = FakeOggTags(metadata_block_picture=[block])
        mock_mutagen.return_value.pictures = None
        with patch.object(ca, "_first_audio_file", return_value=tmp_path / "x.ogg"):
            result = ca._find_embedded_art(tmp_path)

    assert result["front"] == (FRONT_MAGIC, "image/jpeg")


# ── resilience ────────────────────────────────────────────────────────────
def test_no_audio_files_returns_empty(tmp_path: Path):
    with patch.object(ca, "_first_audio_file", return_value=None):
        assert ca._find_embedded_art(tmp_path) == {}


def test_mutagen_returns_none_for_unreadable_file(tmp_path: Path):
    with patch.object(ca, "MutagenFile", return_value=None), \
         patch.object(ca, "_first_audio_file", return_value=tmp_path / "x.mp3"):
        assert ca._find_embedded_art(tmp_path) == {}


def test_mutagen_raising_does_not_propagate(tmp_path: Path):
    def raiser(*a, **kw):
        raise ValueError("corrupt file")

    with patch.object(ca, "MutagenFile", side_effect=raiser), \
         patch.object(ca, "_first_audio_file", return_value=tmp_path / "x.mp3"):
        assert ca._find_embedded_art(tmp_path) == {}
