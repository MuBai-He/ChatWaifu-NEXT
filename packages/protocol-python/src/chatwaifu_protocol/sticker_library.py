"""Versioned owner-scoped learned sticker library contracts."""

from typing import Literal
from uuid import UUID

from pydantic import AwareDatetime, Field, model_validator

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


class StickerUsageRecord(ProtocolModel):
    """One retained image part, not a counter of attempts or user preference."""

    schema_version: Literal["1.0"] = "1.0"
    part_id: UUID
    sticker_id: str = Field(pattern=r"^[a-zA-Z0-9_-]+$", min_length=1, max_length=128)
    label: str = Field(min_length=1, max_length=80)
    origin: Literal["preset", "learned"]
    status: Literal["pending", "sending", "delivered", "failed", "cancelled", "skipped"]
    attempt: int = Field(ge=0)
    created_at: AwareDatetime
    updated_at: AwareDatetime
    delivered_at: AwareDatetime | None = None

    @model_validator(mode="after")
    def validate_delivery_evidence(self) -> "StickerUsageRecord":
        if (self.status == "delivered") != (self.delivered_at is not None):
            raise ValueError("only a delivered part has a delivery timestamp")
        if self.status == "delivered" and self.attempt < 1:
            raise ValueError("delivery requires at least one attempt")
        return self


class StickerUsageHistory(ProtocolModel):
    """Up to 50 visible records from the newest 200 retained scoped image parts.

    has_more means the returned view was bounded, not that a pagination cursor
    exists or that an all-time count can be derived from this recent window.
    """

    schema_version: Literal["1.0"] = "1.0"
    items: list[StickerUsageRecord] = Field(default_factory=list[StickerUsageRecord], max_length=50)
    scan_limit: Literal[200] = 200
    has_more: bool = False
