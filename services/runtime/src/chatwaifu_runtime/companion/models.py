"""Validated user-facing companion settings and runtime status."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class CompanionSettings(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["1.0"] = "1.0"
    wake_phrase_enabled: bool = True
    wake_phrases: tuple[str, ...] = ("宁宁", "绫地宁宁")
    quiet_hours_enabled: bool = True
    quiet_start: str = "23:00"
    quiet_end: str = "08:00"
    proactive_enabled: bool = False
    proactive_idle_minutes: int = Field(default=45, ge=1, le=1440)
    proactive_cooldown_minutes: int = Field(default=60, ge=1, le=10080)
    proactive_daily_budget: int = Field(default=3, ge=0, le=24)
    resource_sleep_enabled: bool = True
    resource_idle_minutes: int = Field(default=10, ge=1, le=1440)
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    @field_validator("wake_phrases")
    @classmethod
    def validate_wake_phrases(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        normalized = tuple(dict.fromkeys(item.strip() for item in value if item.strip()))
        if not normalized:
            raise ValueError("at least one wake phrase is required")
        if len(normalized) > 12 or any(len(item) > 32 for item in normalized):
            raise ValueError("wake phrases exceed the supported size")
        return normalized

    @field_validator("quiet_start", "quiet_end")
    @classmethod
    def validate_clock_time(cls, value: str) -> str:
        try:
            parsed = datetime.strptime(value, "%H:%M")
        except ValueError as error:
            raise ValueError("quiet-hour values must use HH:MM") from error
        return parsed.strftime("%H:%M")

    @model_validator(mode="after")
    def validate_proactive_budget(self) -> Self:
        if self.proactive_enabled and self.proactive_daily_budget == 0:
            raise ValueError("proactive behavior requires a positive daily budget")
        return self


class CompanionSettingsUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    wake_phrase_enabled: bool
    wake_phrases: tuple[str, ...]
    quiet_hours_enabled: bool
    quiet_start: str
    quiet_end: str
    proactive_enabled: bool
    proactive_idle_minutes: int = Field(ge=1, le=1440)
    proactive_cooldown_minutes: int = Field(ge=1, le=10080)
    proactive_daily_budget: int = Field(ge=0, le=24)
    resource_sleep_enabled: bool
    resource_idle_minutes: int = Field(ge=1, le=1440)

    def validated(self, *, updated_at: datetime | None = None) -> CompanionSettings:
        return CompanionSettings(
            **self.model_dump(),
            updated_at=updated_at or datetime.now(UTC),
        )


class ResourceStatus(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    state: Literal["active", "sleeping", "stopping"]
    idle_seconds: int = Field(ge=0)
    sleep_count: int = Field(ge=0)
    last_sleep_at: datetime | None = None
    last_wake_at: datetime | None = None


class CompanionStatus(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["1.0"] = "1.0"
    settings: CompanionSettings
    resources: ResourceStatus
    proactive_today: int = Field(ge=0)
    last_proactive_at: datetime | None = None
