"""Versioned owner-scoped photo retention and inspection contracts."""

from typing import Literal
from uuid import UUID

from pydantic import AwareDatetime, Field

from chatwaifu_protocol.base import ProtocolModel


class PhotoMemorySettings(ProtocolModel):
    schema_version: Literal["1.0"] = "1.0"
    retention_enabled: bool = False
    revision: int = Field(default=0, ge=0)


class PhotoMemorySettingsUpdate(ProtocolModel):
    schema_version: Literal["1.0"] = "1.0"
    retention_enabled: bool
    expected_revision: int = Field(ge=0)


class SavedPhoto(ProtocolModel):
    schema_version: Literal["1.0"] = "1.0"
    photo_id: UUID
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    mime_type: Literal["image/png", "image/jpeg"]
    byte_size: int = Field(ge=1, le=5 * 1024 * 1024)
    width: int = Field(ge=1, le=2048)
    height: int = Field(ge=1, le=2048)
    title: str = Field(min_length=1, max_length=80)
    description: str = Field(min_length=1, max_length=600)
    confidence: float = Field(ge=0, le=1, allow_inf_nan=False)
    keywords: list[str] = Field(default_factory=list[str], max_length=12)
    caption: str = Field(default="", max_length=1000)
    received_at: AwareDatetime
    saved_at: AwareDatetime
    source_connection_id: UUID
    source_session_id: UUID
    source_turn_id: UUID
    source_generation_id: UUID


class PhotoMemorySnapshot(ProtocolModel):
    schema_version: Literal["1.0"] = "1.0"
    settings: PhotoMemorySettings
    items: list[SavedPhoto] = Field(default_factory=list[SavedPhoto], max_length=200)
    total_bytes: int = Field(ge=0, le=500 * 1024 * 1024)
    capacity: Literal[200] = 200


class PhotoMemoryDeleteResult(ProtocolModel):
    schema_version: Literal["1.0"] = "1.0"
    deleted: bool
    revision: int = Field(ge=0)
