"""Persistence port for conversation turns and generation state."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol
from uuid import UUID

from chatwaifu_protocol.events import (
    AssistantGenerationStartedEvent,
    AvatarCueEmittedEvent,
    ErrorRaisedEvent,
    GenericCoreEvent,
    UserTurnCommittedEvent,
)
from chatwaifu_protocol.session import GenerationState

from chatwaifu_runtime.conversation.models import (
    ConversationHistoryEntry,
    ConversationSourceContext,
)


@dataclass(frozen=True, slots=True)
class ConversationRecoveryRecord:
    messages: tuple[dict[str, object], ...]
    after_sequence: int
    last_sequence: int
    active_generation_id: UUID | None


@dataclass(frozen=True, slots=True)
class ConversationGenerationRecord:
    generation_id: UUID
    session_id: UUID
    turn_id: UUID
    state: GenerationState
    output_text: str | None
    error_code: str | None


class ConversationRepository(Protocol):
    async def recovery_state(self, session_id: UUID) -> ConversationRecoveryRecord: ...

    async def list_messages(self, session_id: UUID, *, limit: int) -> list[dict[str, object]]: ...

    async def recent_history(
        self, session_id: UUID, current_turn_id: UUID, *, limit: int
    ) -> tuple[ConversationHistoryEntry, ...]: ...

    async def generation_result(
        self, generation_id: UUID
    ) -> ConversationGenerationRecord | None: ...

    async def commit_user_generation(
        self,
        *,
        session_id: UUID,
        turn_id: UUID,
        generation_id: UUID,
        audio_stream_id: UUID,
        text: str,
        backend_kind: str,
        source_context: ConversationSourceContext | None,
        occurred_at: datetime,
        user_event: UserTurnCommittedEvent,
        generation_event: AssistantGenerationStartedEvent,
    ) -> tuple[UserTurnCommittedEvent, AssistantGenerationStartedEvent]: ...

    async def commit_proactive_generation(
        self,
        *,
        session_id: UUID,
        turn_id: UUID,
        generation_id: UUID,
        audio_stream_id: UUID,
        prompt: str,
        backend_kind: str,
        occurred_at: datetime,
        proactive_event: GenericCoreEvent,
        generation_event: AssistantGenerationStartedEvent,
    ) -> tuple[GenericCoreEvent, AssistantGenerationStartedEvent]: ...

    async def complete_generation(
        self,
        *,
        session_id: UUID,
        turn_id: UUID,
        generation_id: UUID,
        assistant_turn_id: UUID,
        output: str,
        source_context: ConversationSourceContext | None,
        occurred_at: datetime,
        set_session_idle: bool,
        complete_event: GenericCoreEvent,
        pre_events: tuple[AvatarCueEmittedEvent | GenericCoreEvent, ...] = (),
    ) -> tuple[tuple[AvatarCueEmittedEvent | GenericCoreEvent, ...], GenericCoreEvent] | None: ...

    async def cancel_generation(
        self,
        *,
        session_id: UUID,
        turn_id: UUID,
        generation_id: UUID,
        occurred_at: datetime,
        set_session_idle: bool,
        cancel_events: tuple[GenericCoreEvent, ...] = (),
    ) -> tuple[GenericCoreEvent, ...] | None: ...

    async def fail_generation(
        self,
        *,
        session_id: UUID,
        turn_id: UUID,
        generation_id: UUID,
        error_code: str,
        occurred_at: datetime,
        set_session_idle: bool,
        fail_event: ErrorRaisedEvent,
    ) -> ErrorRaisedEvent | None: ...

    async def begin_realtime_generation(
        self,
        *,
        session_id: UUID,
        turn_id: UUID,
        generation_id: UUID,
        audio_stream_id: UUID,
        backend_kind: str,
        occurred_at: datetime,
        generation_event: AssistantGenerationStartedEvent,
    ) -> AssistantGenerationStartedEvent: ...

    async def commit_realtime_user_transcript(
        self,
        *,
        session_id: UUID,
        turn_id: UUID,
        text: str,
        occurred_at: datetime,
        user_event: UserTurnCommittedEvent,
    ) -> UserTurnCommittedEvent | None: ...
