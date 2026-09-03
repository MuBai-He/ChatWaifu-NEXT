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


class AssistantPlaybackPayload(ProtocolModel):
    stream_id: UUID
    segment_id: UUID
    played_pts_ms: int = Field(ge=0)
    buffered_ms: int = Field(ge=0)
    client_clock_ms: int = Field(ge=0)
    transport: Literal["audio_element", "webrtc"]


class AssistantPlaybackStoppedPayload(AssistantPlaybackPayload):
    reason: Literal["ended", "interrupted", "error", "queue_cleared"]
    completed: bool


class AssistantSpokenTextCommittedPayload(ProtocolModel):
    stream_id: UUID
    segment_id: UUID
    text: str = Field(min_length=1, max_length=20_000)
    spoken_text: str = Field(min_length=1, max_length=100_000)


class AvatarCueEmittedPayload(ProtocolModel):
    cue: AvatarCue


class EgressReceiptPayload(ProtocolModel):
    provider_backend_id: str = Field(min_length=1)
    patch_id: UUID
    component_kinds: list[str] = Field(default_factory=list)
    memory_record_ids: list[UUID] = Field(default_factory=list[UUID])
    byte_count: int = Field(ge=0)
    estimated_tokens: int = Field(ge=0)
    policy_decision: str = Field(min_length=1)
    approved_by: str | None = None
    scope: str | None = None
    occurred_at: AwareDatetime


class EgressBlockedPayload(ProtocolModel):
    provider_backend_id: str = Field(min_length=1)
    policy_decision: str = Field(min_length=1)
    reason: str = Field(min_length=1)
    occurred_at: AwareDatetime


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


class AssistantPlaybackStartedEvent(
    EventEnvelope[Literal["assistant.playback_started"], AssistantPlaybackPayload]
):
    event_type: Literal["assistant.playback_started"] = "assistant.playback_started"


class AssistantPlaybackProgressEvent(
    EventEnvelope[Literal["assistant.playback_progress"], AssistantPlaybackPayload]
):
    event_type: Literal["assistant.playback_progress"] = "assistant.playback_progress"


class AssistantPlaybackStoppedEvent(
    EventEnvelope[Literal["assistant.playback_stopped"], AssistantPlaybackStoppedPayload]
):
    event_type: Literal["assistant.playback_stopped"] = "assistant.playback_stopped"


class AssistantSpokenTextCommittedEvent(
    EventEnvelope[Literal["assistant.spoken_text_committed"], AssistantSpokenTextCommittedPayload]
):
    event_type: Literal["assistant.spoken_text_committed"] = "assistant.spoken_text_committed"


class AvatarCueEmittedEvent(EventEnvelope[Literal["avatar.cue_emitted"], AvatarCueEmittedPayload]):
    event_type: Literal["avatar.cue_emitted"] = "avatar.cue_emitted"


class ErrorRaisedEvent(EventEnvelope[Literal["system.error_raised"], ErrorRaisedPayload]):
    event_type: Literal["system.error_raised"] = "system.error_raised"


class EgressReceiptEvent(EventEnvelope[Literal["cloud.egress_receipt"], EgressReceiptPayload]):
    event_type: Literal["cloud.egress_receipt"] = "cloud.egress_receipt"


class EgressBlockedEvent(EventEnvelope[Literal["cloud.egress_blocked"], EgressBlockedPayload]):
    event_type: Literal["cloud.egress_blocked"] = "cloud.egress_blocked"


type GenericCoreEventType = Literal[
    "system.runtime_started",
    "system.runtime_stopping",
    "system.component_health_changed",
    "session.closed",
    "session.data_reset",
    "session.state_changed",
    "user.speech_progress",
    "assistant.text_delta",
    "assistant.text_segment_committed",
    "assistant.generation_cancelled",
    "assistant.generation_completed",
    "assistant.audio_stream_started",
    "assistant.audio_chunk_queued",
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
    "skill.run_expired",
    "tool.call_started",
    "tool.call_completed",
    "tool.call_failed",
    "memory.proposed",
    "memory.committed",
    "memory.superseded",
    "memory.tombstoned",
    "memory.recalled",
    "memory.extraction_completed",
    "character.state_changed",
    "character.response_planned",
    "character.prompt_compiled",
    "relationship.state_changed",
    "avatar.interaction_received",
    "model.route_selected",
    "model.worker_loaded",
    "model.worker_unloaded",
    "model.fallback_triggered",
    "voice.wake_detected",
    "voice.utterance_ignored",
    "companion.proactive_triggered",
    "companion.proactive_deferred",
    "channel.delivery_acknowledged",
    "channel.delivery_plan_created",
    "channel.delivery_part_claimed",
    "channel.delivery_part_acknowledged",
    "channel.delivery_part_delivered",
    "channel.delivery_part_failed",
    "channel.delivery_plan_completed",
    "channel.delivery_plan_cancel_requested",
    "channel.delivery_plan_cancelled",
    "resource.models_slept",
    "resource.models_woke",
]

GENERIC_CORE_EVENT_TYPES: tuple[GenericCoreEventType, ...] = (
    "system.runtime_started",
    "system.runtime_stopping",
    "system.component_health_changed",
    "session.closed",
    "session.data_reset",
    "session.state_changed",
    "user.speech_progress",
    "assistant.text_delta",
    "assistant.text_segment_committed",
    "assistant.generation_cancelled",
    "assistant.generation_completed",
    "assistant.audio_stream_started",
    "assistant.audio_chunk_queued",
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
    "skill.run_expired",
    "tool.call_started",
    "tool.call_completed",
    "tool.call_failed",
    "memory.proposed",
    "memory.committed",
    "memory.superseded",
    "memory.tombstoned",
    "memory.recalled",
    "memory.extraction_completed",
    "character.state_changed",
    "character.response_planned",
    "character.prompt_compiled",
    "relationship.state_changed",
    "avatar.interaction_received",
    "model.route_selected",
    "model.worker_loaded",
    "model.worker_unloaded",
    "model.fallback_triggered",
    "voice.wake_detected",
    "voice.utterance_ignored",
    "companion.proactive_triggered",
    "companion.proactive_deferred",
    "channel.delivery_acknowledged",
    "channel.delivery_plan_created",
    "channel.delivery_part_claimed",
    "channel.delivery_part_acknowledged",
    "channel.delivery_part_delivered",
    "channel.delivery_part_failed",
    "channel.delivery_plan_completed",
    "channel.delivery_plan_cancel_requested",
    "channel.delivery_plan_cancelled",
    "resource.models_slept",
    "resource.models_woke",
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
    | AssistantPlaybackStartedEvent
    | AssistantPlaybackProgressEvent
    | AssistantPlaybackStoppedEvent
    | AssistantSpokenTextCommittedEvent
    | AvatarCueEmittedEvent
    | ErrorRaisedEvent
    | EgressReceiptEvent
    | EgressBlockedEvent
    | GenericCoreEvent
)
