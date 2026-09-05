"""Versioned owner-scoped learned sticker library contracts."""

from typing import Literal
from uuid import UUID

from pydantic import AwareDatetime, Field

from chatwaifu_protocol.base import ProtocolModel


class StickerLibrarySettings(ProtocolModel):
    schema_version: Literal["1.0"] = "1.0"
    learning_enabled: bool = False
    revision: int = Field(default=0, ge=0)


class StickerLibrarySettingsUpdate(ProtocolModel):
    schema_version: Literal["1.0"] = "1.0"
    learning_enabled: bool
    expected_revision: int = Field(ge=0)


class LearnedSticker(ProtocolModel):
    schema_version: Literal["1.0"] = "1.0"
    sticker_id: str = Field(pattern=r"^learned_[0-9a-f]{32}$")
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    mime_type: Literal["image/png"] = "image/png"
    label: str = Field(min_length=1, max_length=80)
    description: str = Field(min_length=1, max_length=300)
    expression: Literal["neutral", "happy", "sad", "angry", "surprised", "shy", "curious"]
    byte_size: int = Field(ge=1, le=5 * 1024 * 1024)
    learned_at: AwareDatetime
    source_connection_id: UUID


class StickerLibrarySnapshot(ProtocolModel):
    schema_version: Literal["1.0"] = "1.0"
    settings: StickerLibrarySettings
    items: list[LearnedSticker] = Field(default_factory=list[LearnedSticker], max_length=100)
    total_bytes: int = Field(ge=0, le=100 * 1024 * 1024)
    capacity: Literal[100] = 100


class StickerLibraryDeleteResult(ProtocolModel):
    schema_version: Literal["1.0"] = "1.0"
    deleted: bool
    revision: int = Field(ge=0)
