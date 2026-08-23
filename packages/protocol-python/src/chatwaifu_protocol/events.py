"""Persistable domain event envelopes and high-value v1 payloads."""

from typing import Literal
from uuid import UUID

from pydantic import AwareDatetime, Field

from chatwaifu_protocol.avatar import AvatarCue
from chatwaifu_protocol.base import JsonObject, PrivacyLevel, ProtocolModel
from chatwaifu_protocol.errors import StructuredError
from chatwaifu_protocol.version import SCHEMA_VERSION


class EventEnvelope[EventTypeT: str, PayloadT: ProtocolModel | JsonObject](ProtocolModel):
    event_id: UUID
    schema_version: str = SCHEMA_VERSION
    event_type: EventTypeT
    session_id: UUID | None = None
    turn_id: UUID | None = None
    generation_id: UUID | None = None
    skill_run_id: UUID | None = None
    sequence: int | None = Field(default=None, ge=0)
    occurred_at: AwareDatetime
    source: str = Field(min_length=1)
    correlation_id: UUID | None = None
    causation_id: UUID | None = None
    privacy: PrivacyLevel = PrivacyLevel.PRIVATE
    payload: PayloadT


class SessionCreatedPayload(ProtocolModel):
    character_id: str = Field(min_length=1)


class UserTurnCommittedPayload(ProtocolModel):
    text: str = Field(min_length=1, max_length=20_000)


class UserSpeechStartedPayload(ProtocolModel):
    utterance_id: UUID
    audio_stream_id: UUID
    sample_rate: int = Field(ge=8_000, le=48_000)
    channels: int = Field(ge=1, le=2)


class UserSpeechStoppedPayload(ProtocolModel):
    utterance_id: UUID
    audio_stream_id: UUID
    duration_ms: int = Field(ge=0)
    audio_bytes: int = Field(ge=0)


class UserTranscriptPayload(ProtocolModel):
    utterance_id: UUID
    text: str = Field(min_length=1, max_length=20_000)
    language: str | None = Field(default=None, min_length=2, max_length=32)
    provider: str = Field(min_length=1, max_length=128)
    is_final: bool


class AssistantGenerationStartedPayload(ProtocolModel):
    backend_kind: str = Field(min_length=1)


class AvatarCueEmittedPayload(ProtocolModel):
    cue: AvatarCue


class ErrorRaisedPayload(ProtocolModel):
    error: StructuredError


class SessionCreatedEvent(EventEnvelope[Literal["session.created"], SessionCreatedPayload]):
    event_type: Literal["session.created"] = "session.created"


class UserTurnCommittedEvent(
    EventEnvelope[Literal["user.turn_committed"], UserTurnCommittedPayload]
):
    event_type: Literal["user.turn_committed"] = "user.turn_committed"


class UserSpeechStartedEvent(
    EventEnvelope[Literal["user.speech_started"], UserSpeechStartedPayload]
):
    event_type: Literal["user.speech_started"] = "user.speech_started"


class UserSpeechStoppedEvent(
    EventEnvelope[Literal["user.speech_stopped"], UserSpeechStoppedPayload]
):
    event_type: Literal["user.speech_stopped"] = "user.speech_stopped"


class UserTranscriptPartialEvent(
    EventEnvelope[Literal["user.transcript_partial"], UserTranscriptPayload]
):
    event_type: Literal["user.transcript_partial"] = "user.transcript_partial"


class UserTranscriptFinalEvent(
    EventEnvelope[Literal["user.transcript_final"], UserTranscriptPayload]
):
    event_type: Literal["user.transcript_final"] = "user.transcript_final"


class AssistantGenerationStartedEvent(
    EventEnvelope[Literal["assistant.generation_started"], AssistantGenerationStartedPayload]
):
    event_type: Literal["assistant.generation_started"] = "assistant.generation_started"


class AvatarCueEmittedEvent(EventEnvelope[Literal["avatar.cue_emitted"], AvatarCueEmittedPayload]):
    event_type: Literal["avatar.cue_emitted"] = "avatar.cue_emitted"


class ErrorRaisedEvent(EventEnvelope[Literal["system.error_raised"], ErrorRaisedPayload]):
    event_type: Literal["system.error_raised"] = "system.error_raised"


type GenericCoreEventType = Literal[
    "system.runtime_started",
    "system.runtime_stopping",
    "system.component_health_changed",
    "session.closed",
    "session.state_changed",
    "user.speech_progress",
    "assistant.text_delta",
    "assistant.text_segment_committed",
    "assistant.generation_cancelled",
    "assistant.generation_completed",
    "assistant.audio_stream_started",
    "assistant.audio_chunk_queued",
    "assistant.playback_started",
    "assistant.playback_progress",
    "assistant.playback_stopped",
    "assistant.spoken_text_committed",
    "conversation.interruption_requested",
    "conversation.interrupted",
    "conversation.recovered",
    "skill.discovered",
    "skill.activated",
    "skill.run_started",
    "skill.progress",
    "skill.confirmation_requested",
    "skill.run_completed",
    "skill.run_failed",
    "skill.run_cancelled",
    "tool.call_started",
    "tool.call_completed",
    "tool.call_failed",
    "memory.proposed",
    "memory.committed",
    "memory.superseded",
    "memory.tombstoned",
    "memory.recalled",
    "character.state_changed",
    "relationship.state_changed",
    "avatar.interaction_received",
    "model.route_selected",
    "model.worker_loaded",
    "model.worker_unloaded",
    "model.fallback_triggered",
]

GENERIC_CORE_EVENT_TYPES: tuple[GenericCoreEventType, ...] = (
    "system.runtime_started",
    "system.runtime_stopping",
    "system.component_health_changed",
    "session.closed",
    "session.state_changed",
    "user.speech_progress",
    "assistant.text_delta",
    "assistant.text_segment_committed",
    "assistant.generation_cancelled",
    "assistant.generation_completed",
    "assistant.audio_stream_started",
    "assistant.audio_chunk_queued",
    "assistant.playback_started",
    "assistant.playback_progress",
    "assistant.playback_stopped",
    "assistant.spoken_text_committed",
    "conversation.interruption_requested",
    "conversation.interrupted",
    "conversation.recovered",
    "skill.discovered",
    "skill.activated",
    "skill.run_started",
    "skill.progress",
    "skill.confirmation_requested",
    "skill.run_completed",
    "skill.run_failed",
    "skill.run_cancelled",
    "tool.call_started",
    "tool.call_completed",
    "tool.call_failed",
    "memory.proposed",
    "memory.committed",
    "memory.superseded",
    "memory.tombstoned",
    "memory.recalled",
    "character.state_changed",
    "relationship.state_changed",
    "avatar.interaction_received",
    "model.route_selected",
    "model.worker_loaded",
    "model.worker_unloaded",
    "model.fallback_triggered",
)


class GenericCoreEvent(EventEnvelope[GenericCoreEventType, JsonObject]):
    """Known lower-value v1 event whose payload will be specialized before its phase begins."""

    event_type: GenericCoreEventType


type EventModel = (
    SessionCreatedEvent
    | UserTurnCommittedEvent
    | UserSpeechStartedEvent
    | UserSpeechStoppedEvent
    | UserTranscriptPartialEvent
    | UserTranscriptFinalEvent
    | AssistantGenerationStartedEvent
    | AvatarCueEmittedEvent
    | ErrorRaisedEvent
    | GenericCoreEvent
)
