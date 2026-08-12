"""app.core.album_details — the tag+VGMDB+MusicBrainz aggregator.

The recurring theme in this file: every external call (VGMDB, MB, a
possibly-corrupt tag) must degrade to a warning, never raise — a detail
page with a few blank sections is fine; a 500 because one upstream was
unreachable or a payload didn't match the expected shape is not.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import app.core.album_details as ad
from app.core.mb_client import MBClient
from app.core.vgmdb_client import VGMDBClient


def test_tags_plus_vgmdb_supplement(isolated_env):
    vgmdb_payload = {
        "catalog": "ABC-001", "release_date": "2020-05-01", "category": "Video Game Music",
        "media_format": "2 CDs", "notes": "A great soundtrack.",
        "publisher": {"names": {"en": "Square Enix"}},
        "composers": [{"names": {"en": "Nobuo Uematsu"}}],
        "performers": [{"names": {"en": "Tokyo Philharmonic"}}],
        "arrangers": [{"names": {"en": "Someone Else"}}],
        "lyricists": [],
        "discs": [{"tracks": [
            {"names": {"en": "Opening"}, "track_length": "2:05"},
            {"names": {"en": "Battle"}, "track_length": "3:00"},
        ]}],
    }
    tag_tracks = [
        {"disc": 1, "track": 1, "title": "Opening Theme", "composer": "Nobuo Uematsu",
         "genre": "Soundtrack", "date": "2020-05-01", "duration_seconds": 125.5},
        {"disc": 1, "track": 2, "title": "Battle Theme", "composer": "Nobuo Uematsu",
         "genre": "Soundtrack", "date": "2020-05-01", "duration_seconds": 180.0},
    ]

    with patch.object(VGMDBClient, "get_album", return_value=vgmdb_payload), \
         patch.object(ad, "_read_tag_tracks", return_value=tag_tracks), \
         patch.object(ad, "find_album_art", return_value={"front": (b"x", "image/jpeg")}):
        result = ad.get_album_detail(
            folder="Test Artist/Test OST", album_dir=Path("/whatever"),
            artist="Test Artist", album="Test OST",
            mb_release_id=None, vgmdb_id="12345", mapped=True, enriched=True,
        )

    assert result.sources == ["tags", "vgmdb"]
    assert result.composers == ["Nobuo Uematsu"]  # from tags, not overwritten
    assert result.performers == ["Tokyo Philharmonic"]  # supplemented from VGMDB
    assert result.arrangers == ["Someone Else"]
    assert result.genres == ["Soundtrack"]  # tags already had it, not overwritten
    assert result.label == "Square Enix"
    assert result.description == "A great soundtrack."
    assert result.art.front is True and result.art.back is False
    assert len(result.tracks) == 2  # from tags, not the VGMDB tracklist fallback
    assert result.warnings == []


def test_no_tags_falls_back_to_vgmdb_tracklist(isolated_env):
    vgmdb_payload = {
        "catalog": "ABC-001", "release_date": "2020-05-01", "category": "Video Game Music",
        "publisher": {"names": {"en": "Square Enix"}},
        "composers": [{"names": {"en": "Nobuo Uematsu"}}],
        "performers": [], "arrangers": [], "lyricists": [],
        "discs": [{"tracks": [
            {"names": {"en": "Opening"}, "track_length": "2:05"},
            {"names": {"en": "Unknown Length Track"}, "track_length": "Unknown"},
        ]}],
    }

    with patch.object(VGMDBClient, "get_album", return_value=vgmdb_payload), \
         patch.object(ad, "_read_tag_tracks", return_value=[]), \
         patch.object(ad, "find_album_art", return_value={}):
        result = ad.get_album_detail(
            folder="X/Y", album_dir=Path("/nope"), artist="X", album="Y",
            mb_release_id=None, vgmdb_id="999", mapped=True, enriched=False,
        )

    assert "tags" not in result.sources
    assert len(result.tracks) == 2
    assert result.tracks[0].duration_seconds == 125.0
    assert result.tracks[1].duration_seconds is None  # "Unknown" length
    assert "Could not read track tags" in result.warnings[0]
    assert any("Track list filled in from VGMDB" in w for w in result.warnings)


def test_vgmdb_network_failure_degrades_gracefully(isolated_env):
    with patch.object(VGMDBClient, "get_album", side_effect=Exception("connection refused")), \
         patch.object(ad, "_read_tag_tracks", return_value=[]), \
         patch.object(ad, "find_album_art", return_value={}):
        result = ad.get_album_detail(
            folder="X/Y", album_dir=Path("/nope"), artist="X", album="Y",
            mb_release_id=None, vgmdb_id="999", mapped=True, enriched=False,
        )

    assert result.sources == []
    assert any("VGMDB lookup failed" in w for w in result.warnings)


def test_malformed_vgmdb_payload_does_not_crash(isolated_env):
    with patch.object(VGMDBClient, "get_album", return_value={"totally": "unexpected shape"}), \
         patch.object(ad, "_read_tag_tracks", return_value=[]), \
         patch.object(ad, "find_album_art", return_value={}):
        result = ad.get_album_detail(
            folder="X/Y", album_dir=Path("/nope"), artist="X", album="Y",
            mb_release_id=None, vgmdb_id="999", mapped=True, enriched=False,
        )

    assert "vgmdb" in result.sources
    assert result.composers == []
    assert result.tracks == []


def test_musicbrainz_supplement_and_genre_dedup(isolated_env):
    mb_payload = {
        "genres": [{"name": "soundtrack"}, {"name": "orchestral"}],
        "tags": [{"name": "video game music"}, {"name": "soundtrack"}],  # dup with genre
        "label-info": [{"label": {"name": "Square Enix"}, "catalog-number": "SQEX-001"}],
        "media": [{"tracks": [{"title": "Opening", "number": "1", "length": 125000}]}],
    }
    tag_tracks = [{"disc": 1, "track": 1, "title": "Opening Theme", "composer": None,
                   "genre": "Rock", "date": None, "duration_seconds": 125.5}]

    with patch.object(MBClient, "get_release", return_value=mb_payload), \
         patch.object(ad, "_read_tag_tracks", return_value=tag_tracks), \
         patch.object(ad, "find_album_art", return_value={}):
        result = ad.get_album_detail(
            folder="X/Y", album_dir=Path("/nope"), artist="X", album="Y",
            mb_release_id="abc-123-def", vgmdb_id=None, mapped=True, enriched=False,
        )

    assert "musicbrainz" in result.sources and "tags" in result.sources
    assert set(result.genres) == {"Rock", "soundtrack", "orchestral", "video game music"}
    assert result.label == "Square Enix" and result.catalog == "SQEX-001"
    assert len(result.tracks) == 1 and result.tracks[0].title == "Opening Theme"


def test_synthetic_vgmdb_release_id_skips_musicbrainz_lookup(isolated_env):
    with patch.object(MBClient, "get_release") as mock_get_release, \
         patch.object(ad, "_read_tag_tracks", return_value=[]), \
         patch.object(ad, "find_album_art", return_value={}):
        ad.get_album_detail(
            folder="X/Y", album_dir=Path("/nope"), artist="X", album="Y",
            mb_release_id="vgmdb-12345", vgmdb_id=None, mapped=True, enriched=False,
        )
    mock_get_release.assert_not_called()


def test_no_mapping_at_all_still_returns_a_result(isolated_env):
    with patch.object(ad, "_read_tag_tracks", return_value=[]), \
         patch.object(ad, "find_album_art", return_value={}):
        result = ad.get_album_detail(
            folder="X/Y", album_dir=Path("/nope"), artist="X", album="Y",
            mb_release_id=None, vgmdb_id=None, mapped=False, enriched=False,
        )
    assert result.sources == []
    assert result.mapped is False
