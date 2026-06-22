"""Pydantic models for the ``/api/v1/picard/*`` endpoints."""

from __future__ import annotations

from pydantic import BaseModel, Field


# ── POST /picard/export ────────────────────────────────────────────────────
class ExportOneRequest(BaseModel):
    """Body for ``POST /picard/export``.

    Picard's Post-Tagging Action expands the ``%directory%`` token to the
    folder it just wrote into. The wrapper forwards that as-is — could be
    an absolute container path, a host path, or just the artist folder
    name. PicardExporter resolves all three.
    """
    artist_folder: str = Field(
        description="Path to (or basename of) the artist folder Picard "
        "just touched.",
    )


class ExportOneResult(BaseModel):
    """Response from ``POST /picard/export``."""
    ok: bool
    artist: str
    mb_id: str | None = None
    is_new: bool = Field(
        default=False,
        description="True if this artist wasn't already in artists_mbids.json.",
    )
    gist_updated: bool = Field(
        default=False,
        description="True if the GitHub Gist was successfully updated. "
        "False here without an error means: no GITHUB_TOKEN configured.",
    )
    message: str


# ── POST /picard/export/full ───────────────────────────────────────────────
class ExportAllResult(BaseModel):
    """Response from ``POST /picard/export/full``."""
    ok: bool
    processed: int = Field(description="Artist folders walked.")
    new: int = Field(description="Artists newly added to the export.")
    skipped: int = Field(
        description="Folders skipped — empty, no audio, or no MB id in tags.",
    )
    gist_updated: bool = False
    errors: list[str] = Field(default_factory=list)
