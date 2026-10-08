"""Operator-only channel policy, independent of credentials and audience grants."""

from typing import Literal, Self

from pydantic import AwareDatetime, ConfigDict, Field, model_validator

from chatwaifu_protocol.base import ProtocolModel


class GroupDiscussionPolicy(ProtocolModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

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


class ChannelRuntimePolicy(ProtocolModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    qq_owner_public_web_enabled: bool = False
    qq_owner_agent_enabled: bool = False
    qq_account_enabled: bool = False
    qq_owner_voice_reply_enabled: bool = True
    qq_owner_voice_input_enabled: bool = True
    qq_native_favorites_enabled: bool = True
    group_discussion: GroupDiscussionPolicy = Field(default_factory=GroupDiscussionPolicy)


class ChannelRuntimeSettingsSnapshot(ProtocolModel):
    schema_version: Literal["1.0"] = "1.0"
    revision: int = Field(ge=0)
    policy: ChannelRuntimePolicy
    updated_at: AwareDatetime | None = None


class ChannelRuntimeSettingsUpdate(ProtocolModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["1.0"] = "1.0"
    expected_revision: int = Field(strict=True, ge=0)
    policy: ChannelRuntimePolicy


class ChannelRuntimeSettingsResponse(ChannelRuntimeSettingsSnapshot):
    """Selected service names only; no endpoints, credentials or private messages."""

    search_provider: str
    reader_provider: str
    stt_provider: str
