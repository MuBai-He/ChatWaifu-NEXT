"""Runtime-backed Domain Sink for Cloud Realtime.

Bridges normalized provider events from CloudRealtimeCoordinator directly into
ChatWaifu Runtime domain boundaries:
- Ephemeral transcript deltas flow over EventHub/ConversationService
  (zero SQLite write amplification);
- Authoritative final transcripts commit durable User turns, trigger Character Kernel observations,
  and enqueue Memory extractions;
- Completed responses atomically finalize Assistant turn and Generation via ConversationService CAS;
- Interruption, barge-in, or cancellation transitions generation state and purges pipelines;
- Lifecycle events (ready, degraded, closed, committed, error) update session observability.
"""

from __future__ import annotations

import logging
from typing import Literal
from uuid import UUID

from chatwaifu_protocol.errors import StructuredError

from chatwaifu_runtime.conversation.service import ConversationService
from chatwaifu_runtime.eventing.hub import EventHub
from chatwaifu_runtime.realtime.cloud.contracts import RealtimeUsage
from chatwaifu_runtime.realtime.cloud.coordinator import RealtimeDomainSink

_LOGGER = logging.getLogger(__name__)


class RuntimeRealtimeDomainSink(RealtimeDomainSink):
    """Authoritative domain sink delegating coordinator events to ConversationService."""

    def __init__(
        self,
        conversation: ConversationService,
        *,
        event_hub: EventHub | None = None,
        backend_id: str = "cloud_realtime",
    ) -> None:
        self._conversation = conversation
        self._event_hub = event_hub
        self._backend_id = backend_id

    async def response_started(self, session_id: UUID, turn_id: UUID, generation_id: UUID) -> None:
        _LOGGER.debug(
            "Cloud realtime response started: session=%s, turn=%s, gen=%s",
            session_id,
            turn_id,
            generation_id,
        )

    async def transcript_delta(
        self,
        session_id: UUID,
        turn_id: UUID,
        generation_id: UUID,
        text: str,
        role: Literal["user", "assistant"] = "assistant",
        utterance_id: UUID | None = None,
    ) -> None:
        await self._conversation.publish_realtime_transcript_delta(
            session_id=session_id,
            turn_id=turn_id,
            generation_id=generation_id,
            text=text,
            role=role,
            provider=self._backend_id,
            utterance_id=utterance_id,
        )

    async def transcript_final(
        self,
        session_id: UUID,
        turn_id: UUID,
        generation_id: UUID,
        text: str,
        role: Literal["user", "assistant"],
        utterance_id: UUID | None = None,
    ) -> None:
        if role == "user":
            await self._conversation.commit_realtime_user_transcript(
                session_id=session_id,
                turn_id=turn_id,
                generation_id=generation_id,
                text=text,
            )
        else:
            _LOGGER.debug(
                "Assistant final transcript received: session=%s, gen=%s, text_len=%d",
                session_id,
                generation_id,
                len(text),
            )

    async def response_completed(
        self, session_id: UUID, turn_id: UUID, generation_id: UUID, text: str
    ) -> None:
        await self._conversation.complete_realtime_generation(
            session_id=session_id,
            turn_id=turn_id,
            generation_id=generation_id,
            text=text,
        )

    async def response_cancelled(
        self, session_id: UUID, turn_id: UUID, generation_id: UUID, reason: str
    ) -> None:
        await self._conversation.cancel_realtime_generation(
            session_id=session_id,
            turn_id=turn_id,
            generation_id=generation_id,
            reason=reason,
        )

    async def response_failed(
        self, session_id: UUID, turn_id: UUID, generation_id: UUID, reason: str
    ) -> None:
        await self._conversation.fail_realtime_generation(
            session_id=session_id,
            turn_id=turn_id,
            generation_id=generation_id,
            error=StructuredError(
                code="realtime_generation_failed",
                message=reason,
                retryable=False,
                component="realtime.cloud",
            ),
        )

    async def usage_recorded(
        self, session_id: UUID, turn_id: UUID, generation_id: UUID, usage: RealtimeUsage
    ) -> None:
        _LOGGER.info(
            "Cloud realtime usage: session=%s, gen=%s, input_tokens=%s, output_tokens=%s",
            session_id,
            generation_id,
            usage.input_tokens,
            usage.output_tokens,
        )

    async def session_ready(self, session_id: UUID, provider_session_id: str | None) -> None:
        _LOGGER.info(
            "Cloud realtime session ready: session=%s, provider_sess=%s",
            session_id,
            provider_session_id,
        )

    async def session_degraded(self, session_id: UUID, reason: str) -> None:
        _LOGGER.warning(
            "Cloud realtime session degraded: session=%s, reason=%s",
            session_id,
            reason,
        )

    async def session_closed(self, session_id: UUID, reason: str) -> None:
        _LOGGER.info(
            "Cloud realtime session closed: session=%s, reason=%s",
            session_id,
            reason,
        )
        await self._conversation.terminate_active_generation(
            session_id,
            reason=f"session_closed: {reason}",
            terminal="cancelled",
        )

    async def input_audio_committed(self, session_id: UUID, turn_id: UUID | None) -> None:
        _LOGGER.debug(
            "Cloud realtime input audio committed: session=%s, turn=%s",
            session_id,
            turn_id,
        )

    async def provider_error(self, session_id: UUID, error: StructuredError) -> None:
        # The error channel is strictly observational: generation-scoped
        # failures arrive as explicit response_failed calls from the
        # coordinator, and lifecycle paths terminate generations explicitly.
        # Never infer a failure target from the currently active generation
        # here, or a late error for an old generation would kill the new one.
        _LOGGER.error(
            "Cloud realtime provider error: session=%s, code=%s, msg=%s",
            session_id,
            error.code,
            error.message,
        )
