"""Pydantic models for the ``/api/v1/overrides/*`` endpoints.

See :mod:`app.core.field_overrides` for why this exists: VGMDB's
composer/performer/arranger credits don't always contain the name
MusicBrainz already has as the release's real artist, and which credit
role wins is a *global* beets-plugin setting — these endpoints let a
person pin a specific field -> value choice for one album instead.
"""

from __future__ import annotations

from pydantic import BaseModel, Field


# ── shared ───────────────────────────────────────────────────────────────
class FieldCandidate(BaseModel):
    """One selectable value for a field, sourced from VGMDB."""
    value: str
    label: str = Field(description="Human-readable origin, e.g. 'Composer (English)'.")


class FieldOptions(BaseModel):
    """One overridable field's current state — what's on disk, what's
    already overridden (if anything), and what VGMDB offers to choose
    from."""
    field: str
    current_value: str | None = Field(
        default=None, description="Value read from the first audio file's tags.",
    )
    override_value: str | None = Field(
        default=None, description="Currently saved override for this field, if any.",
    )
    candidates: list[FieldCandidate] = Field(default_factory=list)


# ── GET /overrides/options ─────────────────────────────────────────────────
class AlbumFieldOptions(BaseModel):
    """Response from ``GET /overrides/options`` — everything needed to
    render the field/value picker for one album."""
    folder: str
    artist: str
    album: str
    mb_release_id: str | None = None
    vgmdb_id: str | None = None
    mapped: bool = Field(description="True if the album resolves to a VGMDB id.")
    fields: list[FieldOptions]
    warnings: list[str] = Field(default_factory=list)


# ── PUT /overrides ──────────────────────────────────────────────────────────
class SetFieldOverridesRequest(BaseModel):
    """Body for ``PUT /overrides?folder=...``.

    ``fields`` maps override field name -> chosen value. A field mapped
    to an empty string clears that field's override rather than saving
    an empty one. Fields not present in the dict are left untouched —
    this merges into whatever's already saved.
    """
    fields: dict[str, str] = Field(default_factory=dict)


class FieldOverrideEntry(BaseModel):
    """One row from ``field_overrides.json`` with its key exposed."""
    folder: str
    mb_release_id: str | None = None
    artist: str = ""
    album: str = ""
    fields: dict[str, str] = Field(default_factory=dict)


# ── DELETE /overrides ────────────────────────────────────────────────────
class DeleteFieldOverrideResult(BaseModel):
    deleted: bool = Field(description="True if an override entry existed and was removed.")
