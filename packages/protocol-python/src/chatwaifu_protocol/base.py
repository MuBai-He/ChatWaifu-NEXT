"""Shared scalar types and model behavior."""

from enum import StrEnum

from pydantic import BaseModel, ConfigDict

type JsonValue = str | int | float | bool | list[JsonValue] | dict[str, JsonValue] | None
type JsonObject = dict[str, JsonValue]


class ProtocolModel(BaseModel):
    """Forward-compatible base: unknown optional fields are ignored in v1."""

    model_config = ConfigDict(extra="ignore", frozen=True)


class PrivacyLevel(StrEnum):
    PUBLIC = "public"
    LOCAL = "local"
    PRIVATE = "private"
    SENSITIVE = "sensitive"


class SideEffect(StrEnum):
    READ = "read"
    WRITE = "write"
    DESTRUCTIVE = "destructive"
    EXTERNAL_COMMUNICATION = "external_communication"
    DEVICE_CONTROL = "device_control"
