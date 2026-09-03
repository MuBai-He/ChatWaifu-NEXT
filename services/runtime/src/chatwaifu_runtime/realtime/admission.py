"""Runtime-owned Realtime Turn Admission.

Coordinates speech turn and generation lifecycle for voice interactions,
allocating authoritative domain identities before audio frames or provider events
are processed, strictly avoiding any random UUID fallback in lower layers.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Protocol
from uuid import UUID, uuid4

from chatwaifu_runtime.realtime.contracts import VoiceTurnIdentity

if TYPE_CHECKING:
    from chatwaifu_runtime.conversation.service import ConversationService

_LOGGER = logging.getLogger(__name__)


class RealtimeTurnAdmissionPort(Protocol):
    """Port for admitting and managing voice turn identities."""

    async def begin_utterance(
        self,
        session_id: UUID,
    ) -> VoiceTurnIdentity: ...

    async def commit_user_transcript(
        self,
        identity: VoiceTurnIdentity,
        text: str,
        confidence: float | None = None,
    ) -> None: ...

    async def cancel_utterance(
        self,
        identity: VoiceTurnIdentity,
        reason: str,
    ) -> None: ...


class RuntimeRealtimeTurnAdmission(RealtimeTurnAdmissionPort):
    """Runtime-backed admission manager delegating to ConversationService."""

    def __init__(self, conversation: ConversationService) -> None:
        self._conversation = conversation
        self._active_identities: dict[UUID, VoiceTurnIdentity] = {}

    async def begin_utterance(self, session_id: UUID) -> VoiceTurnIdentity:
        identity = VoiceTurnIdentity(
            session_id=session_id,
            utterance_id=uuid4(),
            audio_stream_id=uuid4(),
            turn_id=uuid4(),
            generation_id=uuid4(),
        )
        self._active_identities[identity.generation_id] = identity
        await self._conversation.begin_realtime_generation(
            session_id=identity.session_id,
            turn_id=identity.turn_id,
            generation_id=identity.generation_id,
            audio_stream_id=identity.audio_stream_id,
        )
        _LOGGER.debug(
            "Admitted realtime turn for session %s: turn_id=%s, generation_id=%s",
            session_id,
            identity.turn_id,
            identity.generation_id,
        )
        return identity

    async def commit_user_transcript(
        self,
        identity: VoiceTurnIdentity,
        text: str,
        confidence: float | None = None,
    ) -> None:
        await self._conversation.commit_realtime_user_transcript(
            session_id=identity.session_id,
            turn_id=identity.turn_id,
            generation_id=identity.generation_id,
            text=text,
            confidence=confidence,
        )

    async def cancel_utterance(
        self,
        identity: VoiceTurnIdentity,
        reason: str,
    ) -> None:
        self._active_identities.pop(identity.generation_id, None)
        await self._conversation.cancel_realtime_generation(
            session_id=identity.session_id,
            turn_id=identity.turn_id,
            generation_id=identity.generation_id,
            reason=reason,
        )


class InMemoryTurnAdmission(RealtimeTurnAdmissionPort):
    """Deterministic in-memory turn admission for testing without database."""

    def __init__(self) -> None:
        self.begun: list[VoiceTurnIdentity] = []
        self.committed: list[tuple[VoiceTurnIdentity, str, float | None]] = []
        self.cancelled: list[tuple[VoiceTurnIdentity, str]] = []

    async def begin_utterance(self, session_id: UUID) -> VoiceTurnIdentity:
        identity = VoiceTurnIdentity(
            session_id=session_id,
            utterance_id=uuid4(),
            audio_stream_id=uuid4(),
            turn_id=uuid4(),
            generation_id=uuid4(),
        )
        self.begun.append(identity)
        return identity

    async def commit_user_transcript(
        self,
        identity: VoiceTurnIdentity,
        text: str,
        confidence: float | None = None,
    ) -> None:
        self.committed.append((identity, text, confidence))

    async def cancel_utterance(
        self,
        identity: VoiceTurnIdentity,
        reason: str,
    ) -> None:
        self.cancelled.append((identity, reason))
