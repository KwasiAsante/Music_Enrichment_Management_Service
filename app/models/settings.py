"""Pydantic models for ``/api/v1/settings/*``."""

from __future__ import annotations

from pydantic import BaseModel, Field


class SettingField(BaseModel):
    """One editable (or read-only) configuration value."""

    key: str
    label: str
    group: str
    field_type: str = Field(description="'text' | 'password' | 'bool' | 'select'.")
    value: str | bool | None = Field(
        default=None,
        description="Current value. Always None for secret fields — see "
        "is_set instead; a secret's real value is never sent to the browser.",
    )
    options: list[str] | None = Field(
        default=None, description="Choices, for field_type='select'.",
    )
    is_secret: bool = False
    is_set: bool = Field(
        default=False,
        description="For secret fields: whether a real (non-placeholder, "
        "non-empty) value is currently configured.",
    )
    editable: bool = True
    overridden: bool = Field(
        default=False,
        description="True if this value currently comes from a saved "
        "Settings-page change rather than .env/the environment.",
    )
    restart_pending: bool = Field(
        default=False,
        description="True if this field has a saved override that the "
        "running process hasn't picked up yet (i.e. a restart would "
        "change its live value).",
    )
    help_text: str | None = None


class SettingsGroup(BaseModel):
    name: str
    fields: list[SettingField]


class SettingsResponse(BaseModel):
    groups: list[SettingsGroup]
    restart_required: bool = Field(
        description="True if ANY field has a saved-but-unapplied change.",
    )


class SettingsUpdateRequest(BaseModel):
    """Body for ``PUT /settings``. Keyed by field key.

    For a secret field, an empty string or omitted key means "leave the
    current value alone" — matches how a blank password field is
    conventionally treated, and means a GET-then-PUT round trip can never
    accidentally wipe a secret it didn't actually echo back.
    """

    values: dict[str, str | bool | None] = Field(default_factory=dict)


class SettingsUpdateResult(BaseModel):
    saved: list[str] = Field(description="Field keys that were actually changed.")
    restart_required: bool


class RestartResult(BaseModel):
    status: str
