"""Pipecat frame processor bridging media frames to ChatWaifu domain services."""

# Pipecat's public task helpers intentionally use unparameterized Coroutine/Task annotations.
# Keep that imprecision inside this adapter instead of leaking it into domain code.
# pyright: reportUnknownMemberType=false

import asyncio
import logging
import wave
from collections import deque
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import cast
from uuid import UUID, uuid4

from chatwaifu_protocol.base import PrivacyLevel
from chatwaifu_protocol.errors import StructuredError
from chatwaifu_protocol.events import (
    ErrorRaisedEvent,
    ErrorRaisedPayload,
    GenericCoreEvent,
    UserSpeechStartedEvent,
    UserSpeechStartedPayload,
    UserSpeechStoppedEvent,
    UserSpeechStoppedPayload,
    UserTranscriptFinalEvent,
    UserTranscriptPayload,
)
from pipecat.frames.frames import (
    CancelFrame,
    EndFrame,
    Frame,
    InputAudioRawFrame,
    InterruptionFrame,
    OutputAudioRawFrame,
    OutputTransportMessageFrame,
    StartFrame,
    TTSStoppedFrame,
    VADUserStartedSpeakingFrame,
    VADUserStoppedSpeakingFrame,
)
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor

from chatwaifu_runtime.audio.store import AudioAssetStore
from chatwaifu_runtime.companion.activity import ActivityTracker
from chatwaifu_runtime.companion.attention import VoiceActivationMode, evaluate_attention
from chatwaifu_runtime.companion.settings import CompanionSettingsService
from chatwaifu_runtime.conversation.service import ConversationService
from chatwaifu_runtime.eventing.hub import EventHub, EventSubscription
from chatwaifu_runtime.eventing.publisher import EventPublisher
from chatwaifu_runtime.realtime.contracts import (
    SttBackend,
    SttRequest,
    VoiceTurnIdentity,
)

_LOGGER = logging.getLogger(__name__)


@dataclass(slots=True)
class UtteranceBuffer:
    sample_rate: int
    channels: int
    pre_roll_ms: int
    max_seconds: int
    _pre_roll: deque[bytes] = field(init=False)
    _pre_roll_bytes: int = field(init=False, default=0)
    _speech: bytearray = field(init=False)
    _capturing: bool = field(init=False, default=False)

    def __post_init__(self) -> None:
        self._pre_roll: deque[bytes] = deque()
        self._pre_roll_bytes = 0
        self._speech = bytearray()
        self._capturing = False

    @property
    def max_audio_bytes(self) -> int:
        return self.sample_rate * self.channels * 2 * self.max_seconds

    @property
    def pre_roll_limit(self) -> int:
        return self.sample_rate * self.channels * 2 * self.pre_roll_ms // 1000

    def push(self, audio: bytes) -> None:
        if self._capturing:
            remaining = self.max_audio_bytes - len(self._speech)
            if remaining > 0:
                self._speech.extend(audio[:remaining])
            return
        self._pre_roll.append(audio)
        self._pre_roll_bytes += len(audio)
        while self._pre_roll and self._pre_roll_bytes > self.pre_roll_limit:
            removed = self._pre_roll.popleft()
            self._pre_roll_bytes -= len(removed)

    def start(self) -> None:
        self._speech = bytearray().join(self._pre_roll)
        if len(self._speech) > self.max_audio_bytes:
            self._speech = self._speech[-self.max_audio_bytes :]
        self._pre_roll.clear()
        self._pre_roll_bytes = 0
        self._capturing = True

    def finish(self) -> bytes:
        audio = bytes(self._speech)
        self._speech.clear()
        self._capturing = False
        return audio

    def reset(self) -> None:
        self._pre_roll.clear()
        self._pre_roll_bytes = 0
        self._speech.clear()
        self._capturing = False


class VoiceDomainBridgeProcessor(FrameProcessor):
    """Own VAD turn identity, STT dispatch, barge-in and WebRTC TTS delivery."""

    def __init__(
        self,
        *,
        session_id: UUID,
        sample_rate: int,
        channels: int,
        pre_roll_ms: int,
        max_utterance_seconds: int,
        echo_enabled: bool,
        publisher: EventPublisher,
        event_hub: EventHub,
        conversation: ConversationService,
        audio_assets: AudioAssetStore,
        stt: SttBackend,
        companion_settings: CompanionSettingsService,
        activity: ActivityTracker,
        activation_mode: str,
    ) -> None:
        super().__init__(name=f"voice-domain-bridge-{str(session_id)[:8]}")
        self._session_id = session_id
        self._sample_rate = sample_rate
        self._channels = channels
        self._echo_enabled = echo_enabled
        self._publisher = publisher
        self._event_hub = event_hub
        self._conversation = conversation
        self._audio_assets = audio_assets
        self._stt = stt
        self._companion_settings = companion_settings
        self._activity = activity
        self._activation_mode: VoiceActivationMode = cast(VoiceActivationMode, activation_mode)
        self._buffer = UtteranceBuffer(
            sample_rate,
            channels,
            pre_roll_ms,
            max_utterance_seconds,
        )
        self._identity: VoiceTurnIdentity | None = None
        self._active_output_generation: UUID | None = None
        self._subscription: EventSubscription | None = None
        self._event_task: asyncio.Task[None] | None = None
        self._stt_task: asyncio.Task[None] | None = None

    async def process_frame(self, frame: Frame, direction: FrameDirection) -> None:
        await super().process_frame(frame, direction)

        if isinstance(frame, StartFrame):
            await self.push_frame(frame, direction)
            self._start_event_forwarder()
        elif isinstance(frame, InputAudioRawFrame):
            self._buffer.push(frame.audio)
            await self.push_frame(frame, direction)
            if self._echo_enabled:
                await self.push_frame(
                    OutputAudioRawFrame(
                        audio=frame.audio,
                        sample_rate=frame.sample_rate,
                        num_channels=frame.num_channels,
                    ),
                    FrameDirection.DOWNSTREAM,
                )
        elif isinstance(frame, VADUserStartedSpeakingFrame):
            await self.push_frame(frame, direction)
            await self._speech_started()
        elif isinstance(frame, VADUserStoppedSpeakingFrame):
            await self.push_frame(frame, direction)
            await self._speech_stopped()
        elif isinstance(frame, (CancelFrame, EndFrame)):
            await self._stop_background_tasks()
            await self.push_frame(frame, direction)
        else:
            await self.push_frame(frame, direction)

    async def cleanup(self) -> None:
        await self._stop_background_tasks()
        await super().cleanup()

    def _start_event_forwarder(self) -> None:
        if self._event_task is not None:
            return
        self._subscription = self._event_hub.subscribe(
            lambda event: str(event.get("session_id")) == str(self._session_id),
            queue_size=64,
        )
        self._event_task = self.create_task(
            self._forward_runtime_audio(),
            name="forward-runtime-audio",
        )

    async def _stop_background_tasks(self) -> None:
        if self._stt_task is not None:
            identity = self._identity
            if identity is not None:
                try:
                    await self._stt.cancel(identity.generation_id)
                except Exception:
                    _LOGGER.warning(
                        "failed to cancel STT generation %s during teardown",
                        identity.generation_id,
                        exc_info=True,
                    )
            await self.cancel_task(self._stt_task)
            self._stt_task = None
        if self._event_task is not None:
            await self.cancel_task(self._event_task)
            self._event_task = None
        if self._subscription is not None:
            self._event_hub.unsubscribe(self._subscription)
            self._subscription = None
        self._buffer.reset()

    async def _speech_started(self) -> None:
        self._activity.touch(self._session_id)
        if self._stt_task is not None:
            previous = self._identity
            if previous is not None:
                try:
                    await self._stt.cancel(previous.generation_id)
                except Exception:
                    _LOGGER.warning(
                        "failed to cancel superseded STT generation %s",
                        previous.generation_id,
                        exc_info=True,
                    )
            await self.cancel_task(self._stt_task)
            self._stt_task = None

        identity = VoiceTurnIdentity(
            session_id=self._session_id,
            utterance_id=uuid4(),
            audio_stream_id=uuid4(),
            turn_id=uuid4(),
            generation_id=uuid4(),
        )
        self._identity = identity
        self._buffer.start()

        settings = self._companion_settings.get()
        requires_address = self._activation_mode == "open_mic" and settings.wake_phrase_enabled
        # Open-mic background speech must not interrupt playback before address detection.
        if not requires_address:
            await self._interrupt_current(identity)
        await self._publisher.emit(
            UserSpeechStartedEvent(
                event_id=uuid4(),
                session_id=identity.session_id,
                turn_id=identity.turn_id,
                generation_id=identity.generation_id,
                occurred_at=datetime.now(UTC),
                source="runtime.realtime",
                privacy=PrivacyLevel.LOCAL,
                payload=UserSpeechStartedPayload(
                    utterance_id=identity.utterance_id,
                    audio_stream_id=identity.audio_stream_id,
                    sample_rate=self._sample_rate,
                    channels=self._channels,
                ),
            )
        )

    async def _speech_stopped(self) -> None:
        identity = self._identity
        if identity is None:
            return
        audio = self._buffer.finish()
        duration_ms = len(audio) * 1000 // (self._sample_rate * self._channels * 2)
        await self._publisher.emit(
            UserSpeechStoppedEvent(
                event_id=uuid4(),
                session_id=identity.session_id,
                turn_id=identity.turn_id,
                generation_id=identity.generation_id,
                occurred_at=datetime.now(UTC),
                source="runtime.realtime",
                privacy=PrivacyLevel.LOCAL,
                payload=UserSpeechStoppedPayload(
                    utterance_id=identity.utterance_id,
                    audio_stream_id=identity.audio_stream_id,
                    duration_ms=duration_ms,
                    audio_bytes=len(audio),
                ),
            )
        )
        self._stt_task = self.create_task(
            self._transcribe(identity, audio),
            name=f"transcribe-{identity.utterance_id}",
        )

    async def _transcribe(self, identity: VoiceTurnIdentity, audio: bytes) -> None:
        try:
            result = await self._stt.transcribe(
                SttRequest(
                    identity=identity,
                    audio=audio,
                    sample_rate=self._sample_rate,
                    channels=self._channels,
                    language="zh",
                )
            )
            if result is None or self._identity != identity:
                return
            text = result.text.strip()
            if not text:
                return
            attention = evaluate_attention(
                text,
                self._activation_mode,
                self._companion_settings.get(),
            )
            if not attention.accepted:
                await self._emit_companion_event(
                    identity,
                    "voice.utterance_ignored",
                    {
                        "reason": attention.reason,
                        "wake_phrase": attention.wake_phrase,
                    },
                )
                return
            if attention.reason == "wake_phrase":
                await self._interrupt_current(identity)
                await self._emit_companion_event(
                    identity,
                    "voice.wake_detected",
                    {"wake_phrase": attention.wake_phrase},
                )
            text = attention.text
            self._activity.touch(self._session_id)
            await self._publisher.emit(
                UserTranscriptFinalEvent(
                    event_id=uuid4(),
                    session_id=identity.session_id,
                    turn_id=identity.turn_id,
                    generation_id=identity.generation_id,
                    occurred_at=datetime.now(UTC),
                    source="runtime.realtime",
                    privacy=PrivacyLevel.LOCAL,
                    payload=UserTranscriptPayload(
                        utterance_id=identity.utterance_id,
                        text=text,
                        language=result.language,
                        provider=result.provider,
                        is_final=True,
                    ),
                )
            )
            await self._conversation.submit_voice_transcript(
                identity.session_id,
                text,
                turn_id=identity.turn_id,
                generation_id=identity.generation_id,
            )
        except asyncio.CancelledError:
            raise
        except Exception:
            if self._identity == identity:
                await self._publisher.emit(
                    ErrorRaisedEvent(
                        event_id=uuid4(),
                        session_id=identity.session_id,
                        turn_id=identity.turn_id,
                        generation_id=identity.generation_id,
                        occurred_at=datetime.now(UTC),
                        source="runtime.realtime",
                        privacy=PrivacyLevel.LOCAL,
                        payload=ErrorRaisedPayload(
                            error=StructuredError(
                                code="stt_worker_error",
                                message="本地语音转写失败，文字输入仍然可用。",
                                retryable=True,
                                component="realtime.stt",
                                details={"provider": self._stt.kind},
                            )
                        ),
                    )
                )
        finally:
            current = asyncio.current_task()
            if self._stt_task is current:
                self._stt_task = None

    async def _interrupt_current(self, identity: VoiceTurnIdentity) -> None:
        # Clear transport output before waiting for provider cancellation.
        await self.push_frame(InterruptionFrame(), FrameDirection.DOWNSTREAM)
        self.create_task(
            self._conversation.cancel(self._session_id, "barge_in"),
            name=f"barge-in-{identity.utterance_id}",
        )

    async def _emit_companion_event(
        self,
        identity: VoiceTurnIdentity,
        event_type: str,
        payload: dict[str, object],
    ) -> None:
        await self._publisher.emit(
            GenericCoreEvent.model_validate(
                {
                    "event_id": uuid4(),
                    "event_type": event_type,
                    "session_id": identity.session_id,
                    "turn_id": identity.turn_id,
                    "generation_id": identity.generation_id,
                    "occurred_at": datetime.now(UTC),
                    "source": "runtime.companion.attention",
                    "privacy": PrivacyLevel.LOCAL,
                    "payload": payload,
                }
            )
        )

    async def _forward_runtime_audio(self) -> None:
        subscription = self._subscription
        if subscription is None:
            return
        while True:
            event = await subscription.receive()
            event_type = str(event.get("event_type"))
            generation_id = _optional_uuid(event.get("generation_id"))
            if event_type == "assistant.generation_started":
                if (
                    self._active_output_generation is not None
                    and generation_id != self._active_output_generation
                ):
                    await self.push_frame(InterruptionFrame(), FrameDirection.DOWNSTREAM)
                self._active_output_generation = generation_id
            elif event_type == "assistant.audio_chunk_queued":
                if generation_id is None or generation_id != self._active_output_generation:
                    continue
                raw_payload = event.get("payload")
                if not isinstance(raw_payload, dict):
                    continue
                payload = cast(dict[str, object], raw_payload)
                asset_id = _optional_uuid(payload.get("asset_id"))
                if asset_id is None:
                    continue
                path = self._audio_assets.resolve(asset_id)
                if path is None:
                    continue
                audio, sample_rate, channels = await asyncio.to_thread(_read_pcm_wave, path)
                if generation_id != self._active_output_generation:
                    continue
                marker = build_playback_marker(payload, generation_id, "started")
                if marker is None:
                    continue
                await self.push_frame(
                    OutputTransportMessageFrame(message=marker),
                    FrameDirection.DOWNSTREAM,
                )
                await self.push_frame(
                    OutputAudioRawFrame(
                        audio=audio,
                        sample_rate=sample_rate,
                        num_channels=channels,
                    ),
                    FrameDirection.DOWNSTREAM,
                )
                # Flush Pipecat's trailing partial audio chunk before the ordered
                # buffered marker; otherwise the client could acknowledge a segment
                # while its tail is still held inside the transport.
                await self.push_frame(TTSStoppedFrame(), FrameDirection.DOWNSTREAM)
                await self.push_frame(
                    OutputTransportMessageFrame(
                        message=build_playback_marker(payload, generation_id, "buffered")
                    ),
                    FrameDirection.DOWNSTREAM,
                )
            elif event_type in {
                "assistant.generation_cancelled",
                "conversation.interrupted",
            }:
                if generation_id is None or generation_id == self._active_output_generation:
                    self._active_output_generation = None
                    await self.push_frame(InterruptionFrame(), FrameDirection.DOWNSTREAM)


def _read_pcm_wave(path: Path) -> tuple[bytes, int, int]:
    with wave.open(str(path), "rb") as source:
        if source.getsampwidth() != 2:
            raise ValueError(f"WebRTC output requires PCM16 WAV: {path}")
        return source.readframes(source.getnframes()), source.getframerate(), source.getnchannels()


def _optional_uuid(value: object) -> UUID | None:
    if value is None:
        return None
    try:
        return UUID(str(value))
    except ValueError:
        return None


def build_playback_marker(
    payload: dict[str, object], generation_id: UUID, phase: str
) -> dict[str, object] | None:
    stream_id = _optional_uuid(payload.get("stream_id"))
    segment_id = _optional_uuid(payload.get("segment_id"))
    duration = payload.get("duration_ms")
    if stream_id is None or segment_id is None or not isinstance(duration, int):
        return None
    return {
        "type": "chatwaifu.playback_segment",
        "schema_version": "1.0",
        "phase": phase,
        "generation_id": str(generation_id),
        "stream_id": str(stream_id),
        "segment_id": str(segment_id),
        "duration_ms": max(0, duration),
    }
