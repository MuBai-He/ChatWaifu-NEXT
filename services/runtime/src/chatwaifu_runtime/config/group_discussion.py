"""Operator limits for volatile, authorized group discussion (no durable history)."""

from typing import Self

from pydantic import BaseModel, ConfigDict, Field, model_validator


class GroupDiscussionConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    enabled: bool = True
    max_groups: int = Field(default=32, ge=1, le=128)
    message_characters: int = Field(default=800, ge=80, le=2000)
    cache_messages: int = Field(default=96, ge=32, le=512)
    cache_characters: int = Field(default=24000, ge=3200, le=128000)
    retention_seconds: int = Field(default=900, ge=30, le=3600)
    member_messages: int = Field(default=24, ge=1, le=128)
    member_characters: int = Field(default=6000, ge=80, le=32000)
    member_messages_per_window: int = Field(default=6, ge=1, le=30)
    frequency_window_seconds: int = Field(default=30, ge=1, le=300)
    duplicate_window_seconds: int = Field(default=60, ge=1, le=900)
    input_tokens: int = Field(default=1536, ge=128, le=8192)
    summary_input_tokens: int = Field(default=3072, ge=256, le=16384)
    summary_output_tokens: int = Field(default=256, ge=64, le=1024)
    summary_timeout_seconds: float = Field(default=8, ge=0.1, le=30)

    @model_validator(mode="after")
    def validate_capacity(self) -> Self:
        if self.member_messages > self.cache_messages:
            raise ValueError("member message quota must fit group capacity")
        if self.member_characters > self.cache_characters:
            raise ValueError("member character quota must fit group capacity")
        return self
