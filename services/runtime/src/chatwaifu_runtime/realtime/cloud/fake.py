"""Deterministic fake cloud realtime backend and session for reproducible testing.

Provides zero-network, fully controllable implementations of CloudRealtimeBackend
and CloudRealtimeSession. Allows programmatic or scripted injection of provider events,
delays via explicit asyncio Events/barriers (no arbitrary sleeps), interruptions,
late frame emulation, and structured provider errors.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Callable
from datetime import UTC, datetime
from typing import Literal
from uuid import UUID, uuid4

from chatwaifu_runtime.realtime.cloud.contracts import (
    AssistantTranscriptEvent,
    CloudRealtimeBackend,
    CloudRealtimeSession,
    InputAudioCommittedEvent,
    OutputAudioEvent,
    ProviderErrorEvent,
    RealtimeCapabilities,
    RealtimeContextPatch,
    RealtimeInputAudioFrame,
    RealtimeOutputAudioFrame,
    RealtimeProviderError,
    RealtimeProviderEvent,
    RealtimeSessionLineage,
    RealtimeSessionOpenRequest,
    RealtimeTranscriptCandidate,
    RealtimeUsage,
    ResponseCancelledEvent,
    ResponseCompletedEvent,
    ResponseStartedEvent,
    SessionClosedEvent,
    SessionReadyEvent,
    UserTranscriptEvent,
)


class FakeCloudRealtimeSession(CloudRealtimeSession):
    """Deterministic, scriptable fake implementation of CloudRealtimeSession."""

    def __init__(
        self,
        request: RealtimeSessionOpenRequest,
        *,
        backend_id: str = "fake_cloud_realtime",
        auto_ready: bool = True,
    ) -> None:
        self.session_id: UUID = request.session_id
        self.character_id: str = request.character_id
        self.backend_id: str = backend_id
        self.provider_session_id: str = f"fake_sess_{uuid4().hex[:12]}"
        self.lineage: RealtimeSessionLineage = RealtimeSessionLineage(
            session_id=request.session_id,
            turn_id=request.turn_id,
            generation_id=request.generation_id,
            backend_id=backend_id,
            provider_session_id=self.provider_session_id,
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
        )

        self._queue: asyncio.Queue[RealtimeProviderEvent] = asyncio.Queue()
        self.sent_audio_frames: list[RealtimeInputAudioFrame] = []
        self.commit_calls: int = 0
        self.context_updates: list[RealtimeContextPatch] = []
        if request.initial_context is not None:
            self.context_updates.append(request.initial_context)
        self.interrupt_calls: list[tuple[UUID, str]] = []
        self.tool_results: list[tuple[str, str]] = []

        self._is_closed: bool = False
        self._on_audio_frame_hook: Callable[[RealtimeInputAudioFrame], None] | None = None
        self._on_commit_hook: Callable[[], None] | None = None

        if auto_ready:
            self.inject_event(
                SessionReadyEvent(
                    session_id=self.session_id,
                    provider_session_id=self.provider_session_id,
                    backend_id=self.backend_id,
                )
            )

    @property
    def is_closed(self) -> bool:
        return self._is_closed

    async def send_audio(self, frame: RealtimeInputAudioFrame) -> None:
        if self._is_closed:
            raise RuntimeError(f"Session {self.session_id} is closed")
        self.sent_audio_frames.append(frame)
        if self._on_audio_frame_hook is not None:
            self._on_audio_frame_hook(frame)

    async def commit_input(self) -> None:
        if self._is_closed:
            raise RuntimeError(f"Session {self.session_id} is closed")
        self.commit_calls += 1
        self.inject_event(
            InputAudioCommittedEvent(
                session_id=self.session_id,
                turn_id=self.lineage.turn_id,
            )
        )
        if self._on_commit_hook is not None:
            self._on_commit_hook()

    async def update_context(self, patch: RealtimeContextPatch) -> None:
        if self._is_closed:
            raise RuntimeError(f"Session {self.session_id} is closed")
        self.context_updates.append(patch)
        self.lineage = RealtimeSessionLineage(
            session_id=self.lineage.session_id,
            turn_id=self.lineage.turn_id,
            generation_id=self.lineage.generation_id,
            backend_id=self.lineage.backend_id,
            provider_session_id=self.lineage.provider_session_id,
            provider_response_id=self.lineage.provider_response_id,
            created_at=self.lineage.created_at,
            updated_at=datetime.now(UTC),
            revision=self.lineage.revision + 1,
        )

    async def interrupt(self, generation_id: UUID, reason: str = "user_barge_in") -> None:
        self.interrupt_calls.append((generation_id, reason))
        self.inject_event(
            ResponseCancelledEvent(
                session_id=self.session_id,
                generation_id=generation_id,
                provider_response_id=self.lineage.provider_response_id,
                reason=reason,
            )
        )

    async def submit_tool_result(self, call_id: str, output: str) -> None:
        self.tool_results.append((call_id, output))
        raise NotImplementedError(
            "Tool bridge is not supported in Phase 13.0-13.3. submit_tool_result is unsupported."
        )

    async def receive(self) -> RealtimeProviderEvent:
        if self._is_closed and self._queue.empty():
            return SessionClosedEvent(
                session_id=self.session_id,
                backend_id=self.backend_id,
                reason="session_closed",
            )
        return await self._queue.get()

    async def events(self) -> AsyncIterator[RealtimeProviderEvent]:
        while not (self._is_closed and self._queue.empty()):
            event = await self.receive()
            yield event
            if isinstance(event, SessionClosedEvent):
                break

    async def close(self) -> None:
        if self._is_closed:
            return
        self._is_closed = True
        self.inject_event(
            SessionClosedEvent(
                session_id=self.session_id,
                backend_id=self.backend_id,
                reason="client_close",
            )
        )

    # --- Test Inoculation / Injection Helpers ---

    def inject_event(self, event: RealtimeProviderEvent) -> None:
        """Inject a provider event into the session's receive queue."""
        self._queue.put_nowait(event)

    def inject_user_transcript(
        self,
        text: str,
        *,
        phase: Literal["delta", "final"] = "final",
        confidence: float = 0.95,
        generation_id: UUID | None = None,
    ) -> None:
        self.inject_event(
            UserTranscriptEvent(
                candidate=RealtimeTranscriptCandidate(
                    session_id=self.session_id,
                    generation_id=generation_id or self.lineage.generation_id,
                    role="user",
                    phase=phase,
                    text=text,
                    source="provider",
                    confidence=confidence,
                )
            )
        )

    def inject_response_started(
        self,
        generation_id: UUID,
        *,
        provider_response_id: str | None = None,
    ) -> str:
        resp_id = provider_response_id or f"resp_{uuid4().hex[:8]}"
        self.lineage = RealtimeSessionLineage(
            session_id=self.lineage.session_id,
            turn_id=self.lineage.turn_id,
            generation_id=generation_id,
            backend_id=self.lineage.backend_id,
            provider_session_id=self.lineage.provider_session_id,
            provider_response_id=resp_id,
            created_at=self.lineage.created_at,
            updated_at=datetime.now(UTC),
            revision=self.lineage.revision + 1,
        )
        self.inject_event(
            ResponseStartedEvent(
                session_id=self.session_id,
                generation_id=generation_id,
                provider_response_id=resp_id,
            )
        )
        return resp_id

    def inject_assistant_transcript(
        self,
        text: str,
        generation_id: UUID,
        *,
        phase: Literal["delta", "final"] = "delta",
    ) -> None:
        self.inject_event(
            AssistantTranscriptEvent(
                candidate=RealtimeTranscriptCandidate(
                    session_id=self.session_id,
                    generation_id=generation_id,
                    role="assistant",
                    phase=phase,
                    text=text,
                    source="provider",
                )
            )
        )

    def inject_output_audio(
        self,
        generation_id: UUID,
        audio: bytes,
        *,
        sequence: int = 0,
        pts_ms: int = 0,
        sample_rate: int = 24_000,
        channels: int = 1,
        is_final: bool = False,
    ) -> None:
        self.inject_event(
            OutputAudioEvent(
                frame=RealtimeOutputAudioFrame(
                    session_id=self.session_id,
                    generation_id=generation_id,
                    sequence=sequence,
                    pts_ms=pts_ms,
                    sample_rate=sample_rate,
                    channels=channels,
                    audio=audio,
                    is_final=is_final,
                )
            )
        )

    def inject_response_completed(
        self,
        generation_id: UUID,
        *,
        provider_response_id: str | None = None,
        usage: RealtimeUsage | None = None,
    ) -> None:
        resp_id = (
            provider_response_id or self.lineage.provider_response_id or f"resp_{uuid4().hex[:8]}"
        )
        self.inject_event(
            ResponseCompletedEvent(
                session_id=self.session_id,
                generation_id=generation_id,
                provider_response_id=resp_id,
                usage=usage,
            )
        )

    def inject_error(
        self,
        code: str,
        message: str,
        *,
        generation_id: UUID | None = None,
        retryable: bool = False,
        details: dict[str, str] | None = None,
    ) -> None:
        self.inject_event(
            ProviderErrorEvent(
                error=RealtimeProviderError(
                    session_id=self.session_id,
                    generation_id=generation_id,
                    backend_id=self.backend_id,
                    code=code,
                    message=message,
                    retryable=retryable,
                    details=details or {},
                )
            )
        )

    def set_on_audio_frame_hook(
        self,
        hook: Callable[[RealtimeInputAudioFrame], None] | None,
    ) -> None:
        self._on_audio_frame_hook = hook

    def set_on_commit_hook(
        self,
        hook: Callable[[], None] | None,
    ) -> None:
        self._on_commit_hook = hook


class FakeCloudRealtimeBackend(CloudRealtimeBackend):
    """Deterministic in-memory backend for CloudRealtime testing."""

    def __init__(
        self,
        backend_id: str = "fake_cloud_realtime",
        *,
        capabilities: RealtimeCapabilities | None = None,
        auto_ready: bool = True,
    ) -> None:
        self.backend_id: str = backend_id
        self._capabilities: RealtimeCapabilities = capabilities or RealtimeCapabilities(
            backend_id=backend_id,
        )
        self._auto_ready: bool = auto_ready
        self.open_session_calls: list[RealtimeSessionOpenRequest] = []
        self.sessions: list[FakeCloudRealtimeSession] = []
        self.close_calls: int = 0
        self._custom_session_factory: (
            Callable[[RealtimeSessionOpenRequest], FakeCloudRealtimeSession] | None
        ) = None

    def set_session_factory(
        self,
        factory: Callable[[RealtimeSessionOpenRequest], FakeCloudRealtimeSession] | None,
    ) -> None:
        self._custom_session_factory = factory

    async def capabilities(self) -> RealtimeCapabilities:
        return self._capabilities

    async def open_session(
        self,
        request: RealtimeSessionOpenRequest,
    ) -> CloudRealtimeSession:
        self.open_session_calls.append(request)
        if self._custom_session_factory is not None:
            session = self._custom_session_factory(request)
        else:
            session = FakeCloudRealtimeSession(
                request,
                backend_id=self.backend_id,
                auto_ready=self._auto_ready,
            )
        self.sessions.append(session)
        return session

    async def close(self) -> None:
        self.close_calls += 1
        for session in self.sessions:
            await session.close()
        self.sessions.clear()
