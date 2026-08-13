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


class TestNotificationRequest(BaseModel):
    """Body for ``POST /settings/test-notification``."""

    key: str = Field(description="Which Discord webhook field to test, e.g. 'discord_webhook_artist'.")
    url: str | None = Field(
        default=None,
        description="The URL currently in that field's input box, even if "
        "unsaved — lets someone verify a webhook before hitting Save. "
        "Blank/omitted falls back to whatever's currently configured.",
    )


class TestNotificationResult(BaseModel):
    ok: bool
    message: str = Field(description="Human-readable result, shown next to the Send Test button either way.")


class TestConnectionRequest(BaseModel):
    """Body for ``POST /settings/test-connection``."""

    service: str = Field(
        description="Which integration to test: 'lidarr', 'prowlarr', 'qbit', or 'vgmdb'.",
    )
    values: dict[str, str | None] = Field(
        default_factory=dict,
        description="Current form values for the service's fields. Blank/omitted "
        "entries fall back to whatever is already configured.",
    )


class TestConnectionResult(BaseModel):
    ok: bool
    message: str = Field(description="Human-readable result, shown next to the Test Connection button.")
