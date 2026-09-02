"""app.core.library_scanner — rebuilding album_list.json from tags on disk.

Focuses on the media_type/franchise caching added alongside mb_release_id;
the rest of the scan behavior (cleanup, new-album detection) isn't touched
by that change.
"""

from __future__ import annotations

from unittest.mock import patch

from app.core.library_scanner import LibraryScanner
from app.storage.json_store import store


def test_scan_caches_media_type_and_franchise(isolated_env):
    album_dir = isolated_env.music_dir / "Artist" / "Real Band Name" / "Test OST"
    album_dir.mkdir(parents=True)
    (album_dir / "01.flac").touch()

    with patch.object(
        LibraryScanner, "_get_tags",
        return_value={
            "album": ["Test OST"], "albumartist": ["Real Band Name"],
            "media_type": ["video-game"], "franchise": ["Devil May Cry"],
        },
    ):
        LibraryScanner().scan()

    entry = store.album_list.read()["Test OST"]
    assert entry["media_type"] == "video-game"
    assert entry["franchise"] == "Devil May Cry"


def test_scan_leaves_media_type_and_franchise_none_when_untagged(isolated_env):
    album_dir = isolated_env.music_dir / "Artist" / "Real Band Name" / "Test OST"
    album_dir.mkdir(parents=True)
    (album_dir / "01.flac").touch()

    with patch.object(
        LibraryScanner, "_get_tags",
        return_value={"album": ["Test OST"], "albumartist": ["Real Band Name"]},
    ):
        LibraryScanner().scan()

    entry = store.album_list.read()["Test OST"]
    assert entry["media_type"] is None
    assert entry["franchise"] is None
