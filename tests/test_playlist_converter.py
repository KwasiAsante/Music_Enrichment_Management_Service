"""app.core.playlist_converter — matching strategy and passthrough rules.

Builds small fake libraries directly under a tmp_path "Artist" root (the
same layout LibraryScanner/ArtistFixer use) rather than going through the
`isolated_env`/`client` fixtures — this module has no dependency on
`settings`/`store` when `artist_root` is passed explicitly, so plain
tmp_path keeps these tests fast and focused on the matching logic alone.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import app.core.playlist_converter as pc
from app.core.playlist_converter import convert, list_album_tracks, parse_playlist, resolve_within_album


def _touch(root: Path, *parts: str) -> Path:
    p = root.joinpath(*parts)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.touch()
    return p


def test_unchanged_entry_resolves_as_is(tmp_path: Path):
    root = tmp_path / "Artist"
    _touch(root, "Artist A", "Album 1", "01 - Song.flac")

    playlist = str(root / "Artist A" / "Album 1" / "01 - Song.flac")
    result = convert(playlist, artist_root=root)

    assert result.total == 1 and result.matched == 1 and result.unmatched == 0
    assert result.entries[0].method == "unchanged"
    assert result.entries[0].resolved_path == "Artist A/Album 1/01 - Song.flac"


def test_relocated_entry_matched_by_unique_basename(tmp_path: Path):
    root = tmp_path / "Artist"
    # Old entry pointed at a kanji-named artist folder that ArtistFixer
    # has since renamed; the track filename itself is untouched.
    _touch(root, "Artist A (Renamed)", "Album 1", "01 - Song.flac")

    playlist = "/music/synced_music/Artist/アーティスト/Album 1/01 - Song.flac"
    result = convert(playlist, artist_root=root)

    assert result.entries[0].method == "relocated"
    assert result.entries[0].resolved_path == "Artist A (Renamed)/Album 1/01 - Song.flac"
    assert result.matched == 1


def test_ambiguous_basename_disambiguated_by_folder_similarity(tmp_path: Path):
    root = tmp_path / "Artist"
    _touch(root, "Artist X", "Album Alpha", "01.flac")
    _touch(root, "Artist Y", "Album Beta", "01.flac")

    # Neither the artist nor the full old tail resolves directly (so the
    # "unchanged"/"renumbered" album-dir shortcuts both miss), leaving the
    # album-folder-name similarity as the only disambiguating signal
    # between the two same-basename candidates.
    playlist = "/old/Some Old Artist Name/Album Alpha/01.flac"
    result = convert(playlist, artist_root=root)

    assert result.entries[0].method == "fuzzy"
    assert result.entries[0].resolved_path == "Artist X/Album Alpha/01.flac"


def test_renumbered_entry_matched_within_unchanged_album_folder(tmp_path: Path):
    root = tmp_path / "Artist"
    # beets/Picard rewrote "01.flac" -> "01 - Song Title.flac" in place —
    # the album folder itself was never touched.
    _touch(root, "Artist A", "Album 1", "01 - Song Title.flac")

    playlist = "/old/Artist A/Album 1/01.flac"
    result = convert(playlist, artist_root=root)

    assert result.entries[0].method == "renumbered"
    assert result.entries[0].resolved_path == "Artist A/Album 1/01 - Song Title.flac"


def test_fuzzy_matches_title_across_unrelated_folder_rename(tmp_path: Path):
    # Real-world regression: an old playlist exported from a descriptive,
    # non-"Artist/Album" folder structure (a soundtrack compilation's own
    # organisation, not the library's) with a bare title filename. The
    # current library file lives under a completely differently-named
    # folder and has gained a track-number prefix — folder-name
    # similarity is useless here (near zero), but the *title* survives
    # unchanged, which is what should carry the match.
    root = tmp_path / "Artist"
    _touch(root, "Sora Amamiya", "Skyreach", "01 - Skyreach.mp3")
    _touch(root, "Sora Amamiya", "Skyreach", "03 - Skyreach (Instrumental).mp3")

    playlist = (
        "Akame ga Kill/Akame ga Kill! OP&ED/01 OP/"
        "Akame ga Kill Opening 01 - Skyreach by Sora Amamiya/Skyreach.mp3"
    )
    result = convert(playlist, artist_root=root)

    assert result.entries[0].method == "fuzzy"
    assert result.entries[0].resolved_path == "Sora Amamiya/Skyreach/01 - Skyreach.mp3"


def test_mid_range_title_similarity_needs_folder_corroboration(tmp_path: Path):
    # Real-world regression: "Innen" (old) vs "LINE" (an unrelated track
    # from a different soundtrack entirely) scores the *same* difflib
    # title ratio (0.667) as a genuine alternate-romanization rename
    # ("Shikoutazer" vs "Shcowtaser", see the test below) — title
    # similarity alone can't tell them apart at that ratio. The old
    # entry's folder text ("Disc 2") shares nothing with the wrong
    # candidate's actual folder ("Naruto The Best"), which is what must
    # sink it.
    root = tmp_path / "Artist"
    _touch(root, "Various Artists", "Naruto The Best", "10 - LINE.mp3")

    playlist = "Akame ga Kill/Akame ga Kill! Original Soundtrack 01/Disc 2/03 - Innen .mp3"
    result = convert(playlist, artist_root=root)

    assert result.entries[0].method == "unmatched"
    assert result.entries[0].resolved_path is None


def test_mid_range_title_similarity_accepted_with_folder_corroboration(tmp_path: Path):
    # The other half of the same regression: a genuine alternate-
    # romanization rename ("Shikoutazer" -> "Shcowtaser") lands at the
    # exact same 0.667 title ratio as the wrong match above, but the old
    # entry's album-folder text ("Original Soundtrack 02") still closely
    # resembles the candidate's actual folder ("Original Soundtrack 2"),
    # which is real corroborating evidence a bad match wouldn't have.
    root = tmp_path / "Artist"
    _touch(root, "Taku Iwasaki", "Akame ga Kill! Original Soundtrack 2", "05 - Shcowtaser.mp3")

    playlist = "Akame ga Kill/Akame ga Kill! Original Soundtrack 02/05. Shikoutazer.mp3"
    result = convert(playlist, artist_root=root)

    assert result.entries[0].method == "fuzzy"
    assert result.entries[0].resolved_path == "Taku Iwasaki/Akame ga Kill! Original Soundtrack 2/05 - Shcowtaser.mp3"


def test_generic_disc_label_alone_is_not_folder_corroboration(tmp_path: Path):
    # Real-world regression: two *different* multi-disc soundtracks both
    # happen to use "Disc 1"/"Disc 2" folders. Comparing only the
    # immediate parent folder ("Disc 2" vs "Disc 1") scored high enough
    # to wrongly corroborate a mid-confidence title match ("Innen" vs
    # "Neon", ratio 0.667) into a completely different soundtrack.
    # Folder context has to look one level higher (the album folder,
    # not just the disc subfolder) to tell these apart.
    root = tmp_path / "Artist"
    _touch(root, "Jeff Williams", "RWBY Volume 3 Soundtrack", "Disc 1", "05 - Neon.mp3")

    playlist = "Akame ga Kill/Akame ga Kill! Original Soundtrack 01/Disc 2/03 - Innen .mp3"
    result = convert(playlist, artist_root=root)

    assert result.entries[0].method == "unmatched"
    assert result.entries[0].resolved_path is None


def test_bare_numbered_filenames_never_fuzzy_cross_match(tmp_path: Path):
    # With no title text on either side, two different bare track numbers
    # must never be treated as a match, even though their raw filename
    # similarity (0.857 for "01.flac" vs "07.flac") would clear a naive
    # single-cutoff fuzzy check.
    root = tmp_path / "Artist"
    _touch(root, "Artist A", "Some Other Album", "07.flac")

    playlist = "/old/Unrelated Artist/Unrelated Album/01.flac"
    result = convert(playlist, artist_root=root)

    assert result.entries[0].method == "unmatched"
    assert result.entries[0].resolved_path is None


def test_unmatched_entry_is_logged_and_skipped_not_fatal(tmp_path: Path):
    root = tmp_path / "Artist"
    _touch(root, "Artist A", "Album 1", "01.flac")

    playlist = "\n".join([
        "/old/Artist A/Album 1/01.flac",
        "/old/Completely Different Artist/Nonexistent Album/99 - Nope.flac",
    ])
    result = convert(playlist, artist_root=root)

    assert result.total == 2
    assert result.matched == 1
    assert result.unmatched == 1
    unmatched_entry = result.entries[1]
    assert unmatched_entry.method == "unmatched"
    assert unmatched_entry.resolved_path is None
    assert "# UNMATCHED: /old/Completely Different Artist" in result.playlist_text


def test_similarly_shaped_but_unrelated_filename_is_not_a_fuzzy_match(tmp_path: Path):
    # Regression: "99 - Gone.flac" vs "01 - Song.flac" clears the
    # filename-only difflib cutoff (0.71) purely from sharing the same
    # "NN - Title.ext" shape, even though the two tracks/albums are
    # completely unrelated and the parent folder name doesn't match
    # either. Folder-name similarity must also clear its own bar before
    # a fuzzy match is trusted — caught via a real end-to-end run against
    # a live server, not by the narrower unit tests above.
    root = tmp_path / "Artist"
    _touch(root, "Artist A", "Album 1", "01 - Song.flac")

    playlist = "/old/Nowhere/Nothing/99 - Gone.flac"
    result = convert(playlist, artist_root=root)

    assert result.entries[0].method == "unmatched"
    assert result.entries[0].resolved_path is None


def test_extinf_and_comments_are_preserved(tmp_path: Path):
    root = tmp_path / "Artist"
    _touch(root, "Artist A", "Album 1", "01 - Song.flac")

    playlist = "\n".join([
        "#EXTM3U",
        "# a user comment",
        "",
        "#EXTINF:180,Artist A - Song",
        str(root / "Artist A" / "Album 1" / "01 - Song.flac"),
    ])
    result = convert(playlist, artist_root=root)

    assert "#EXTM3U" in result.playlist_text
    assert "# a user comment" in result.playlist_text
    assert "#EXTINF:180,Artist A - Song" in result.playlist_text
    assert result.entries[0].extinf == "#EXTINF:180,Artist A - Song"


def test_windows_backslash_paths_are_normalised(tmp_path: Path):
    root = tmp_path / "Artist"
    _touch(root, "Artist A", "Album 1", "01 - Song.flac")

    playlist = r"C:\Music\Artist A\Album 1\01 - Song.flac"
    result = convert(playlist, artist_root=root)

    assert result.entries[0].method in ("unchanged", "relocated")
    assert result.entries[0].resolved_path == "Artist A/Album 1/01 - Song.flac"


def test_missing_artist_root_yields_all_unmatched(tmp_path: Path):
    root = tmp_path / "does-not-exist"
    playlist = "/old/Artist A/Album 1/01.flac"
    result = convert(playlist, artist_root=root)

    assert result.matched == 0
    assert result.unmatched == 1


def test_parse_playlist_associates_extinf_with_following_path_only():
    text = "#EXTINF:100,A - B\n/some/path.flac\n/no/extinf.flac\n"
    items = parse_playlist(text)

    assert items[0] == ("#EXTINF:100,A - B", "/some/path.flac")
    assert items[1] == (None, "/no/extinf.flac")


# ── manual matching (resolve_within_album / list_album_tracks) ─────────────

def test_list_album_tracks_sorted_and_audio_only(tmp_path: Path):
    album_dir = tmp_path / "Album"
    _touch(tmp_path, "Album", "02 - B.flac")
    _touch(tmp_path, "Album", "01 - A.flac")
    (album_dir / "cover.jpg").touch()

    tracks = list_album_tracks(album_dir)

    assert [t.name for t in tracks] == ["01 - A.flac", "02 - B.flac"]


def test_list_album_tracks_missing_folder_is_empty(tmp_path: Path):
    assert list_album_tracks(tmp_path / "nope") == []


def test_list_album_tracks_recurses_into_disc_subfolders(tmp_path: Path):
    # Real-world regression: multi-disc albums in this library keep
    # tracks under "Disc 1"/"Disc 2" subfolders, not directly in the
    # album folder — a non-recursive listing came back empty, leaving
    # the manual-match picker with nothing to show for these albums.
    album_dir = tmp_path / "Album"
    _touch(tmp_path, "Album", "Disc 1", "02 - B.flac")
    _touch(tmp_path, "Album", "Disc 1", "01 - A.flac")
    _touch(tmp_path, "Album", "Disc 2", "01 - C.flac")
    (album_dir / "album.nfo").touch()

    tracks = list_album_tracks(album_dir)

    assert [t.relative_to(album_dir).as_posix() for t in tracks] == [
        "Disc 1/01 - A.flac", "Disc 1/02 - B.flac", "Disc 2/01 - C.flac",
    ]


def test_resolve_within_album_matches_a_track_inside_a_disc_subfolder(tmp_path: Path):
    # Same real-world case as above, one level further: even a heavily
    # retranslated title ("Gekisen" -> "Intense Battle") still resolves
    # by track number once the disc subfolder is actually searched.
    album_dir = tmp_path / "Album"
    _touch(tmp_path, "Album", "Disc 1", "09 - Intense Battle.mp3")

    resolved = resolve_within_album("09 - Gekisen .mp3", album_dir)

    assert resolved == album_dir / "Disc 1" / "09 - Intense Battle.mp3"


def test_resolve_within_album_track_number_ambiguous_across_discs_falls_through(tmp_path: Path):
    # Track numbers restart per disc, so a bare-number match must not
    # pick one arbitrarily when two discs both have a "09" — falls
    # through to the title check, which also fails here (unrelated
    # titles), so the person has to pick by hand.
    album_dir = tmp_path / "Album"
    _touch(tmp_path, "Album", "Disc 1", "09 - Intense Battle.mp3")
    _touch(tmp_path, "Album", "Disc 2", "09 - Training.mp3")

    assert resolve_within_album("09 - Gekisen .mp3", album_dir) is None


def test_resolve_within_album_exact_name(tmp_path: Path):
    album_dir = tmp_path / "Album"
    _touch(tmp_path, "Album", "Some Track.mp3")

    assert resolve_within_album("Some Track.mp3", album_dir) == album_dir / "Some Track.mp3"


def test_resolve_within_album_by_track_number(tmp_path: Path):
    album_dir = tmp_path / "Album"
    _touch(tmp_path, "Album", "05 - Retitled.mp3")

    assert resolve_within_album("05.mp3", album_dir) == album_dir / "05 - Retitled.mp3"


def test_resolve_within_album_by_title_needs_no_folder_corroboration(tmp_path: Path):
    # Unlike the whole-library fuzzy pass, a mid-confidence title match
    # is trusted here on its own — the album itself is no longer in
    # question once a person has picked it.
    album_dir = tmp_path / "Akame ga Kill! Original Soundtrack 01"
    _touch(tmp_path, "Akame ga Kill! Original Soundtrack 01", "03 - Intense Battle.mp3")

    resolved = resolve_within_album("09 - Gekisen .mp3", album_dir)
    # "gekisen" vs "intense battle" doesn't clear even the title cutoff —
    # confirms this stays a real bar, not "anything in the folder goes".
    assert resolved is None


def test_resolve_within_album_no_plausible_candidate_is_none(tmp_path: Path):
    album_dir = tmp_path / "Album"
    _touch(tmp_path, "Album", "Completely Unrelated Title.mp3")

    assert resolve_within_album("Some Track.mp3", album_dir) is None


def test_resolve_within_album_missing_folder_is_none(tmp_path: Path):
    assert resolve_within_album("Some Track.mp3", tmp_path / "nope") is None


# ── tag-based title matching ────────────────────────────────────────────────

def _mock_mutagen_titles(titles: dict[str, str | None]):
    """A MutagenFile(path, easy=True) stand-in keyed by basename — mirrors
    easy-mode's real shape (a dict of str -> list[str]) closely enough for
    _candidate_tag_title's `.tags.get("title")` / list-unwrap logic. A
    missing/None entry mimics a file with no readable tags at all."""
    def _side_effect(path, *args, **kwargs):
        title = titles.get(Path(path).name)
        m = MagicMock()
        m.tags = {"title": [title]} if title is not None else None
        return m
    return _side_effect


def test_candidate_tag_title_reads_and_normalises(tmp_path: Path):
    f = tmp_path / "07.mp3"
    f.touch()
    with patch.object(pc, "MutagenFile", side_effect=_mock_mutagen_titles({"07.mp3": "Skyreach"})):
        assert pc._candidate_tag_title(f) == "skyreach"


def test_candidate_tag_title_none_when_unreadable(tmp_path: Path):
    f = tmp_path / "07.mp3"
    f.touch()
    with patch.object(pc, "MutagenFile", side_effect=OSError("boom")):
        assert pc._candidate_tag_title(f) is None


def test_best_title_ratio_prefers_tag_over_uninformative_filename(tmp_path: Path):
    # A bare track number strips down to an empty filename-derived title
    # (ratio 0.0 against anything) — the tag is the only usable signal.
    f = tmp_path / "07.mp3"
    f.touch()
    with patch.object(pc, "MutagenFile", side_effect=_mock_mutagen_titles({"07.mp3": "Skyreach"})):
        assert pc._best_title_ratio("skyreach", f) == 1.0


def test_resolve_within_album_matches_via_tag_when_filename_uninformative(tmp_path: Path):
    # Real-world case the filename-only version of this function missed:
    # an accurately-tagged track filed under a generic name.
    album_dir = tmp_path / "Album"
    _touch(tmp_path, "Album", "07.mp3")

    with patch.object(pc, "MutagenFile", side_effect=_mock_mutagen_titles({"07.mp3": "Skyreach"})):
        resolved = resolve_within_album("Skyreach.mp3", album_dir)

    assert resolved == album_dir / "07.mp3"


def test_convert_whole_library_fuzzy_trusts_exact_tag_over_imperfect_filename(tmp_path: Path):
    # Filename-derived title similarity alone ("skyreach" vs "skyrelch",
    # ratio 0.875) lands in the "needs folder corroboration" band, and
    # the old/candidate folders here share nothing — without the tag
    # this would be rejected (see the companion test below). The tag's
    # exact title clears _TITLE_EXACT_CUTOFF and is trusted on its own.
    root = tmp_path / "Artist"
    _touch(root, "Artist A", "Some Unrelated Folder", "07 - Skyrelch.mp3")

    playlist = "/old/Completely Different Path/Skyreach.mp3"
    with patch.object(pc, "MutagenFile", side_effect=_mock_mutagen_titles({"07 - Skyrelch.mp3": "Skyreach"})):
        result = convert(playlist, artist_root=root)

    assert result.entries[0].method == "fuzzy"
    assert result.entries[0].resolved_path == "Artist A/Some Unrelated Folder/07 - Skyrelch.mp3"


def test_convert_whole_library_fuzzy_rejects_without_tag_corroboration(tmp_path: Path):
    # Same filenames/folders as above, minus the tag — confirms the
    # match above genuinely depends on the tag, not a fluke of the
    # filename/folder text alone.
    root = tmp_path / "Artist"
    _touch(root, "Artist A", "Some Unrelated Folder", "07 - Skyrelch.mp3")

    playlist = "/old/Completely Different Path/Skyreach.mp3"
    with patch.object(pc, "MutagenFile", return_value=None):
        result = convert(playlist, artist_root=root)

    assert result.entries[0].method == "unmatched"
