"""Pipecat media bridge for cloud realtime speech-to-speech sessions.

Bridges high-frequency WebRTC input audio frames from Pipecat to a
CloudRealtimeSession via bounded queues with backpressure, and routes
normalized output audio frames from CloudRealtimeCoordinator back into
Pipecat downstream playback, enforcing generation invalidation and barge-in order.
"""

# pyright: reportUnknownMemberType=false

from __future__ import annotations

import asyncio
import logging
from uuid import UUID

from pipecat.frames.frames import (
    CancelFrame,
    EndFrame,
    Frame,
    InputAudioRawFrame,
    InterruptionFrame,
    OutputAudioRawFrame,
    StartFrame,
    TTSStoppedFrame,
    VADUserStartedSpeakingFrame,
    VADUserStoppedSpeakingFrame,
)
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor

from chatwaifu_runtime.realtime.admission import RealtimeTurnAdmissionPort
from chatwaifu_runtime.realtime.cloud.contracts import (
    CloudRealtimeSession,
    RealtimeInputAudioFrame,
    RealtimeOutputAudioFrame,
)
from chatwaifu_runtime.realtime.cloud.coordinator import (
    CloudRealtimeCoordinator,
    InMemoryDomainSink,
    RealtimeDomainSink,
    RealtimeMediaSink,
)
from chatwaifu_runtime.realtime.cloud.mirror import RealtimeSessionMirror
from chatwaifu_runtime.realtime.contracts import VoiceTurnIdentity

_LOGGER = logging.getLogger(__name__)


class CloudRealtimeMediaBridge(FrameProcessor, RealtimeMediaSink):
    """Bridges Pipecat audio plane and CloudRealtimeSession / Coordinator.

    Invariants enforced:
    1. Input audio is bounded in a queue with oldest-frame drop backpressure.
    2. Raw PCM is never forwarded to domain sinks or EventStore.
    3. User barge-in sequence: Runtime generation invalidated -> Provider interrupt
       -> InterruptionFrame downstream -> Late audio dropped.
    4. Teardown of WebRTC pipeline closes provider session.
    """

    def __init__(
        self,
        *,
        session_id: UUID,
        coordinator: CloudRealtimeCoordinator,
        admission: RealtimeTurnAdmissionPort | None = None,
        sample_rate: int = 16_000,
        channels: int = 1,
        input_queue_capacity: int = 100,
    ) -> None:
        super().__init__(name=f"cloud-realtime-bridge-{str(session_id)[:8]}")
        self.session_id: UUID = session_id
        self._coordinator: CloudRealtimeCoordinator = coordinator
        self._admission: RealtimeTurnAdmissionPort | None = admission
        self._current_identity: VoiceTurnIdentity | None = None
        self._sample_rate: int = sample_rate
        self._channels: int = channels
        self._input_queue_capacity: int = input_queue_capacity

        self._input_queue: asyncio.Queue[RealtimeInputAudioFrame] = asyncio.Queue(
            maxsize=input_queue_capacity
        )
        self._input_sequence: int = 0
        self._input_pts_ms: int = 0
        self._dropped_input_frames: int = 0
        self._send_task: asyncio.Task[None] | None = None
        self._started: bool = False
        self._is_torn_down: bool = False

        # Register self as media sink in coordinator
        self._coordinator.set_media_sink(self)

    @classmethod
    def create(
        cls,
        *,
        session_id: UUID,
        backend_id: str,
        session: CloudRealtimeSession,
        admission: RealtimeTurnAdmissionPort | None = None,
        domain_sink: RealtimeDomainSink | None = None,
        sample_rate: int = 16_000,
        channels: int = 1,
        input_queue_capacity: int = 100,
    ) -> CloudRealtimeMediaBridge:
        sink = domain_sink or InMemoryDomainSink()
        mirror = RealtimeSessionMirror(
            session_id,
            backend_id=backend_id,
            provider_session_id=session.lineage.provider_session_id,
        )
        coordinator = CloudRealtimeCoordinator(
            session_id=session_id,
            session=session,
            mirror=mirror,
            domain_sink=sink,
        )
        return cls(
            session_id=session_id,
            coordinator=coordinator,
            admission=admission,
            sample_rate=sample_rate,
            channels=channels,
            input_queue_capacity=input_queue_capacity,
        )

    @property
    def coordinator(self) -> CloudRealtimeCoordinator:
        return self._coordinator

    @property
    def current_identity(self) -> VoiceTurnIdentity | None:
        return self._current_identity

    @property
    def dropped_input_frames(self) -> int:
        return self._dropped_input_frames

    @property
    def input_sequence(self) -> int:
        return self._input_sequence

    def _ensure_task_manager(self) -> None:
        if getattr(self, "_task_manager", None) is None:
            try:
                from pipecat.utils.asyncio.task_manager import TaskManager

                self._task_manager = TaskManager()
            except Exception:
                pass

    async def process_frame(self, frame: Frame, direction: FrameDirection) -> None:
        self._ensure_task_manager()
        await super().process_frame(frame, direction)

        if isinstance(frame, StartFrame):
            self._ensure_started()
            await self.push_frame(frame, direction)
        elif isinstance(frame, InputAudioRawFrame):
            self._handle_input_audio(frame)
            await self.push_frame(frame, direction)
        elif isinstance(frame, VADUserStartedSpeakingFrame):
            await self._handle_user_speaking_started()
            await self.push_frame(frame, direction)
        elif isinstance(frame, VADUserStoppedSpeakingFrame):
            await self._handle_user_speaking_stopped()
            await self.push_frame(frame, direction)
        elif isinstance(frame, (CancelFrame, EndFrame)):
            await self._teardown()
            await self.push_frame(frame, direction)
        else:
            await self.push_frame(frame, direction)

    def _ensure_started(self) -> None:
        if self._started:
            return
        self._ensure_task_manager()
        self._started = True
        self._coordinator.start()
        if self._send_task is None:
            self._send_task = self.create_task(
                self._send_audio_loop(),
                name=f"cloud-audio-sender-{str(self.session_id)[:8]}",
            )

    def _handle_input_audio(self, frame: InputAudioRawFrame) -> None:
        self._ensure_started()
        self._input_sequence += 1

        bytes_per_sample = 2
        denom = frame.num_channels * bytes_per_sample
        total_samples = len(frame.audio) // denom if denom > 0 else len(frame.audio) // 2
        frame_duration_ms = (
            int((total_samples / frame.sample_rate) * 1000) if frame.sample_rate > 0 else 20
        )
        pts_ms = self._input_pts_ms
        self._input_pts_ms += frame_duration_ms

        input_frame = RealtimeInputAudioFrame(
            session_id=self.session_id,
            generation_id=self._coordinator.mirror.active_generation_id,
            sequence=self._input_sequence,
            pts_ms=pts_ms,
            sample_rate=frame.sample_rate,
            channels=frame.num_channels,
            audio=frame.audio,
            is_final=False,
        )

        if self._input_queue.full():
            try:
                self._input_queue.get_nowait()
                self._input_queue.task_done()
                self._dropped_input_frames += 1
                _LOGGER.warning(
                    "Cloud realtime input audio queue full; dropped oldest frame "
                    "(total dropped: %d)",
                    self._dropped_input_frames,
                )
            except asyncio.QueueEmpty:
                pass

        try:
            self._input_queue.put_nowait(input_frame)
        except asyncio.QueueFull:
            self._dropped_input_frames += 1

    async def _handle_user_speaking_started(self) -> None:
        """Enforce barge-in order:

        1. Runtime Generation invalidation (in mirror & domain sink)
        2. Provider interrupt signal
        3. Transport output queue cleared (InterruptionFrame)
        4. Late audio frames discarded by tombstone fence.
        """
        active_gen_id = self._coordinator.mirror.active_generation_id
        if active_gen_id is not None:
            _LOGGER.info(
                "Barge-in detected: cancelling active generation %s for session %s",
                active_gen_id,
                self.session_id,
            )
            await self._coordinator.cancel_generation(active_gen_id, reason="user_barge_in")
            await self.push_frame(InterruptionFrame(), FrameDirection.DOWNSTREAM)

        if self._admission is not None:
            identity = await self._admission.begin_utterance(self.session_id)
            self._current_identity = identity
            self._coordinator.admit_turn(
                turn_id=identity.turn_id,
                generation_id=identity.generation_id,
                utterance_id=identity.utterance_id,
            )
            _LOGGER.debug(
                "Realtime turn admitted via admission port for session %s: gen=%s, turn=%s",
                self.session_id,
                identity.generation_id,
                identity.turn_id,
            )

    async def _handle_user_speaking_stopped(self) -> None:
        try:
            await self._coordinator.session.commit_input()
        except Exception:
            _LOGGER.debug(
                "Error committing input on session %s (may be unsupported by backend)",
                self.session_id,
                exc_info=True,
            )

    async def _send_audio_loop(self) -> None:
        try:
            while True:
                frame = await self._input_queue.get()
                try:
                    await self._coordinator.session.send_audio(frame)
                except asyncio.CancelledError:
                    raise
                except Exception:
                    _LOGGER.warning(
                        "Error sending audio frame to cloud session %s",
                        self.session_id,
                        exc_info=True,
                    )
                finally:
                    self._input_queue.task_done()
        except asyncio.CancelledError:
            pass

    async def handle_audio_frame(self, frame: RealtimeOutputAudioFrame) -> None:
        """Implement RealtimeMediaSink protocol to route output frames downstream."""
        if self._coordinator.mirror.is_tombstoned(
            frame.generation_id
        ) or not self._coordinator.mirror.is_active(frame.generation_id):
            _LOGGER.debug(
                "Dropping late output audio frame for inactive or tombstoned generation %s",
                frame.generation_id,
            )
            return

        raw_frame = OutputAudioRawFrame(
            audio=frame.audio,
            sample_rate=frame.sample_rate,
            num_channels=frame.channels,
        )
        await self.push_frame(raw_frame, FrameDirection.DOWNSTREAM)

        if frame.is_final:
            await self.push_frame(TTSStoppedFrame(), FrameDirection.DOWNSTREAM)

    async def _teardown(self) -> None:
        if self._is_torn_down:
            return
        self._is_torn_down = True
        if self._send_task is not None:
            await self.cancel_task(self._send_task)
            self._send_task = None
        try:
            await asyncio.shield(self._coordinator.stop())
        except Exception:
            _LOGGER.warning("Error stopping coordinator during teardown", exc_info=True)

    async def cleanup(self) -> None:
        await self._teardown()
        await super().cleanup()
