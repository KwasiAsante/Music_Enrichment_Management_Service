"""Pydantic models for ``/api/v1/playlist/*``.

* ``PlaylistConvertResult`` — response for ``POST /playlist/convert``,
  mirroring the upload/download shape already used by
  ``POST /mapping/import`` (see that router). ``entries`` carries enough
  structured data (``extinf``, ``resolved_path``) for the browser to
  rebuild the playlist text itself after a person manually fixes one or
  more entries, without another round trip — see ``artist_root``, which
  is what a manually-picked ``resolved_path`` gets joined onto client-side.
* ``AlbumTracksResult`` — response for ``GET /playlist/album-tracks``,
  the manual-match flow: a person picks an album (search via the
  existing ``GET /library/albums?folder=``), this lists that album's
  actual audio files plus a best-effort ``suggested_path`` so they don't
  have to eyeball which one is right when it's obvious.
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class PlaylistEntryResult(BaseModel):
    """One playlist line's resolution outcome."""

    line_no: int
    original_path: str
    extinf: str | None = Field(
        default=None,
        description="The raw '#EXTINF:...' line preceding this entry, if any.",
    )
    resolved_path: str | None = Field(
        default=None,
        description="Path relative to artist_root, or null if unmatched.",
    )
    method: str = Field(
        description="'unchanged', 'renumbered', 'relocated', 'fuzzy', "
        "'manual', or 'unmatched'.",
    )


class PlaylistConvertResult(BaseModel):
    """Response from ``POST /playlist/convert``."""

    filename: str
    artist_root: str = Field(
        description="Absolute path every entry's resolved_path is relative to — "
        "join them client-side to rebuild the playlist after a manual fix.",
    )
    total: int
    matched: int
    unmatched: int
    entries: list[PlaylistEntryResult] = Field(default_factory=list)
    playlist: str = Field(description="The converted playlist file's full text.")
    job_id: str | None = None


class AlbumTrack(BaseModel):
    """One audio file in a manually-picked album folder — possibly
    several directory levels down (multi-disc albums in this library
    keep tracks under a "Disc 1"/"Disc 2" subfolder, not directly in the
    album folder)."""

    path: str = Field(description="Path relative to artist_root.")
    filename: str
    relative_path: str = Field(
        description="Path relative to the album folder itself, e.g. "
        "'Disc 2/04 - Track.mp3' — what to actually display, since bare "
        "filename alone can't distinguish same-numbered tracks across discs.",
    )


class AlbumTracksResult(BaseModel):
    """Response from ``GET /playlist/album-tracks``."""

    folder: str
    tracks: list[AlbumTrack] = Field(default_factory=list)
    suggested_path: str | None = Field(
        default=None,
        description="Best-effort automatic match within this album for the "
        "'original_path' query param, if one was given and something plausible "
        "was found — relative to artist_root, matches one of 'tracks'.",
    )
