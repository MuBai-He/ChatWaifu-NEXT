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
from dataclasses import dataclass
from uuid import UUID, uuid4

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

from chatwaifu_runtime.playback.service import PlaybackService
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
from chatwaifu_runtime.realtime.pipecat.processor import build_playback_marker

_LOGGER = logging.getLogger(__name__)
MEDIA_OPERATION_TIMEOUT_SECONDS = 5.0


@dataclass(slots=True)
class _OutputSegmentState:
    segment_id: UUID
    stream_id: UUID
    segment_index: int
    total_audio_bytes: int = 0
    sample_rate: int = 24_000
    channels: int = 1


@dataclass(frozen=True, slots=True)
class _InputCommit:
    generation_id: UUID


class CloudRealtimeMediaBridge(FrameProcessor, RealtimeMediaSink):
    """Bridges Pipecat audio plane and CloudRealtimeSession / Coordinator.

    Invariants enforced:
    1. Input audio is bounded in a queue with oldest-frame drop backpressure.
    2. Raw PCM is never forwarded to domain sinks or EventStore.
    3. User barge-in invalidates Runtime generation and flushes local playback before
       bounded provider interruption; late audio is dropped.
    4. Teardown of WebRTC pipeline closes provider session.
    """

    def __init__(
        self,
        *,
        session_id: UUID,
        coordinator: CloudRealtimeCoordinator,
        admission: RealtimeTurnAdmissionPort | None = None,
        playback: PlaybackService | None = None,
        sample_rate: int = 16_000,
        channels: int = 1,
        input_queue_capacity: int = 100,
    ) -> None:
        super().__init__(name=f"cloud-realtime-bridge-{str(session_id)[:8]}")
        self.session_id: UUID = session_id
        self._coordinator: CloudRealtimeCoordinator = coordinator
        self._admission: RealtimeTurnAdmissionPort | None = admission
        self._playback: PlaybackService | None = playback
        self._current_identity: VoiceTurnIdentity | None = None
        self._sample_rate: int = sample_rate
        self._channels: int = channels
        self._input_queue_capacity: int = input_queue_capacity

        if input_queue_capacity < 1:
            raise ValueError("input queue capacity must be positive")
        # Reserve one slot for the commit barrier; audio pressure cannot evict it.
        self._input_queue: asyncio.Queue[RealtimeInputAudioFrame | _InputCommit] = asyncio.Queue(
            maxsize=input_queue_capacity + 1
        )
        self._commit_generation_id: UUID | None = None
        self._input_sequence: int = 0
        self._input_pts_ms: int = 0
        self._dropped_input_frames: int = 0
        self._send_task: asyncio.Task[None] | None = None
        self._output_task: asyncio.Task[object] | None = None
        self._output_lock = asyncio.Lock()
        self._turn_lock = asyncio.Lock()
        self._started: bool = False
        self._is_torn_down: bool = False
        self._media_failure_reported: bool = False
        self._fatal_media_failure: bool = False
        self._closed_event = asyncio.Event()
        self._completion_listener_token: UUID | None = None
        self._active_segments: dict[UUID, _OutputSegmentState] = {}
        self._generation_segment_counts: dict[UUID, int] = {}

        # Register self as media sink in coordinator
        self._coordinator.set_media_sink(self)

    @property
    def closed_event(self) -> asyncio.Event:
        """Signaled when the cloud session terminates (EOF, error, or close)."""
        return self._closed_event

    def set_completion_listener_token(self, token: UUID) -> None:
        self._completion_listener_token = token

    @classmethod
    def create(
        cls,
        *,
        session_id: UUID,
        backend_id: str,
        session: CloudRealtimeSession,
        admission: RealtimeTurnAdmissionPort | None = None,
        domain_sink: RealtimeDomainSink | None = None,
        playback: PlaybackService | None = None,
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
            playback=playback,
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
        if self._is_torn_down or self._fatal_media_failure or self._coordinator.is_closing:
            return
        self._ensure_task_manager()
        if not self._started:
            self._started = True
            self._coordinator.start()
        if self._send_task is None or self._send_task.done():
            self._send_task = self.create_task(
                self._send_audio_loop(),
                name=f"cloud-audio-sender-{str(self.session_id)[:8]}",
            )

    def _handle_input_audio(self, frame: InputAudioRawFrame) -> None:
        self._ensure_started()
        if self._fatal_media_failure or self._is_torn_down or self._coordinator.is_closing:
            _LOGGER.debug(
                "Dropping input audio for session %s: bridge is fatally failed",
                self.session_id,
            )
            return
        if self._commit_generation_id is not None:
            return
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

        if self._input_queue.qsize() >= self._input_queue_capacity:
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
        async with self._turn_lock:
            await self._begin_utterance()

    async def _begin_utterance(self) -> None:
        """Enforce barge-in order:

        1. Runtime Generation invalidation (in mirror & domain sink)
        2. Transport output queue cleared (InterruptionFrame)
        3. Old input drained and provider interrupt call completed within a deadline
        4. Late audio frames discarded by tombstone fence.
        """
        if self._fatal_media_failure:
            _LOGGER.warning(
                "Refusing VAD admission for session %s: bridge is fatally failed "
                "and must be rebuilt",
                self.session_id,
            )
            return
        if self._is_torn_down or self._coordinator.is_closing:
            return
        active_gen_id = self._coordinator.mirror.active_generation_id
        if active_gen_id is not None:
            await self._coordinator.cancel_generation(
                active_gen_id, reason="user_barge_in", interrupt_provider=False
            )
        if active_gen_id is not None or self._current_identity is not None:
            # Local playback must stop even when the provider socket is blocked.
            # Also flush audio buffered after model completion (mirror no longer active).
            await self._flush_output()
        await self._cancel_sender()
        self._drain_input_queue()
        if active_gen_id is not None:
            try:
                async with asyncio.timeout(MEDIA_OPERATION_TIMEOUT_SECONDS):
                    await self._coordinator.session.interrupt(active_gen_id, "user_barge_in")
            except Exception as error:
                await self._fail_active_media_operation("media_interrupt_failed", error)
                return
        if self._fatal_media_failure or self._is_torn_down or self._coordinator.is_closing:
            return
        self._commit_generation_id = None
        if self._admission is not None:
            identity = await self._coordinator.admit_utterance(self._admission)
            if identity is None:
                return
            self._current_identity = identity
            _LOGGER.debug(
                "Realtime turn admitted via admission port for session %s: gen=%s, turn=%s",
                self.session_id,
                identity.generation_id,
                identity.turn_id,
            )

        self._ensure_started()

    async def _cancel_sender(self) -> None:
        sender = self._send_task
        if sender is not None and sender is not asyncio.current_task():
            sender.cancel()
            await asyncio.gather(sender, return_exceptions=True)
            if self._send_task is sender:
                self._send_task = None

    async def session_terminated(self) -> None:
        """Seal this bridge on EOF/close/error; reconnect requires a fresh bridge."""
        self._fatal_media_failure = True
        self._drain_input_queue()
        await self._flush_output()
        await self._cancel_sender()
        self._closed_event.set()

    async def _flush_output(self) -> None:
        self._active_segments.clear()
        self._generation_segment_counts.clear()
        output = self._output_task
        if output is not None:
            output.cancel()
            await asyncio.gather(output, return_exceptions=True)
        await self.push_frame(InterruptionFrame(), FrameDirection.DOWNSTREAM)

    def _drain_input_queue(self) -> None:
        """Drop all queued but unsent input frames after a fatal failure."""
        drained = 0
        while True:
            try:
                self._input_queue.get_nowait()
            except asyncio.QueueEmpty:
                break
            self._input_queue.task_done()
            drained += 1
        if drained:
            _LOGGER.debug(
                "Drained %d queued input frames for failed session %s",
                drained,
                self.session_id,
            )

    async def _fail_active_media_operation(self, code: str, error: Exception) -> bool:
        """Route a media-plane failure into a terminal generation transition.

        The first failure seals the bridge: the active generation fails, the
        unsent queue is drained, the cloud session closes, and the sender loop
        must exit (returns True). Later failures only log (returns False), so
        a dead session cannot spam terminal events per queued frame. After a
        fatal failure the bridge refuses new admissions and input audio until
        the outer pipeline destroys and rebuilds it.
        """
        if self._media_failure_reported:
            _LOGGER.debug(
                "Ignoring repeated media failure %s for session %s",
                code,
                self.session_id,
            )
            return True
        self._media_failure_reported = True
        self._fatal_media_failure = True
        try:
            await self._coordinator.report_media_failure(code, error)
        except Exception:
            _LOGGER.warning(
                "Error reporting media failure for session %s",
                self.session_id,
                exc_info=True,
            )
        self._drain_input_queue()
        try:
            await self._coordinator.stop()
        except Exception:
            _LOGGER.debug(
                "Error closing cloud session %s after media failure",
                self.session_id,
                exc_info=True,
            )
        return True

    async def _handle_user_speaking_stopped(self) -> None:
        generation_id = self._coordinator.mirror.active_generation_id
        if (
            self._fatal_media_failure
            or self._is_torn_down
            or self._coordinator.is_closing
            or generation_id is None
            or self._commit_generation_id == generation_id
        ):
            return
        self._commit_generation_id = generation_id
        # Same FIFO as audio: stop notification never overtakes accepted PCM.
        self._input_queue.put_nowait(_InputCommit(generation_id))

    def _is_frame_sendable(self, frame: RealtimeInputAudioFrame | _InputCommit) -> bool:
        """Generation fence for outbound input audio.

        Only the currently active, non-tombstoned generation may reach the
        provider, so stale queued audio can neither pollute the next turn nor
        misattribute its own send failure to that turn.
        """
        mirror = self._coordinator.mirror
        if frame.generation_id is None:
            _LOGGER.debug(
                "Dropping input audio without generation for session %s",
                self.session_id,
            )
            return False
        if mirror.is_tombstoned(frame.generation_id):
            _LOGGER.debug(
                "Dropping input audio for tombstoned generation %s",
                frame.generation_id,
            )
            return False
        if not mirror.is_active(frame.generation_id):
            _LOGGER.debug(
                "Dropping input audio for superseded generation %s",
                frame.generation_id,
            )
            return False
        return True

    async def _send_audio_loop(self) -> None:
        try:
            while True:
                if self._fatal_media_failure:
                    return
                frame = await self._input_queue.get()
                try:
                    if self._fatal_media_failure or not self._is_frame_sendable(frame):
                        continue
                    async with asyncio.timeout(MEDIA_OPERATION_TIMEOUT_SECONDS):
                        if isinstance(frame, _InputCommit):
                            await self._coordinator.session.commit_input()
                        else:
                            await self._coordinator.session.send_audio(frame)
                except asyncio.CancelledError:
                    raise
                except Exception as error:
                    _LOGGER.warning(
                        "Error sending audio frame to cloud session %s",
                        self.session_id,
                        exc_info=True,
                    )
                    code = (
                        "media_commit_failed"
                        if isinstance(frame, _InputCommit)
                        else "media_send_failed"
                    )
                    if await self._fail_active_media_operation(code, error):
                        return
                finally:
                    self._input_queue.task_done()
        except asyncio.CancelledError:
            raise

    async def handle_audio_frame(self, frame: RealtimeOutputAudioFrame) -> None:
        """Implement RealtimeMediaSink protocol to route output frames downstream."""
        async with self._output_lock:
            if not self._is_output_active(frame):
                return
            output = asyncio.create_task(self._push_output(frame), name="cloud-output-handoff")
            self._output_task = output
            try:
                # Cancelling this child at barge-in must not cancel the provider event pump.
                # Cancellation of the pump itself still propagates through gather.
                results = await asyncio.gather(output, return_exceptions=True)
                result = results[0]
                if isinstance(result, Exception):
                    raise result
            finally:
                if self._output_task is output:
                    self._output_task = None

    def _is_generation_active(self, generation_id: UUID) -> bool:
        if (
            self._fatal_media_failure
            or self._is_torn_down
            or self._coordinator.is_closing
            or self._coordinator.mirror.is_tombstoned(generation_id)
            or not self._coordinator.mirror.is_active(generation_id)
        ):
            return False
        return True

    def _is_output_active(self, frame: RealtimeOutputAudioFrame) -> bool:
        if not self._is_generation_active(frame.generation_id):
            _LOGGER.debug(
                "Dropping late output audio frame for inactive or tombstoned generation %s",
                frame.generation_id,
            )
            return False
        return True

    async def _push_output(self, frame: RealtimeOutputAudioFrame) -> None:
        seg_state = self._active_segments.get(frame.generation_id)
        if seg_state is None:
            stream_id = self._coordinator.mirror.get_audio_stream_id(frame.generation_id) or (
                self._current_identity.audio_stream_id
                if self._current_identity
                and self._current_identity.generation_id == frame.generation_id
                else None
            )
            if stream_id is None:
                _LOGGER.error(
                    "Refusing output audio for unadmitted generation %s without stream_id",
                    frame.generation_id,
                )
                return
            segment_id = uuid4()
            segment_index = self._generation_segment_counts.get(frame.generation_id, 0)
            self._generation_segment_counts[frame.generation_id] = segment_index + 1
            seg_state = _OutputSegmentState(
                segment_id=segment_id,
                stream_id=stream_id,
                segment_index=segment_index,
                total_audio_bytes=0,
                sample_rate=frame.sample_rate,
                channels=frame.channels,
            )
            self._active_segments[frame.generation_id] = seg_state
            if self._playback is not None:
                await self._playback.register_segment(
                    session_id=self.session_id,
                    generation_id=frame.generation_id,
                    stream_id=stream_id,
                    segment_id=segment_id,
                    segment_index=segment_index,
                    text="",
                    duration_ms=0,
                    duration_finalized=False,
                    transcript_finalized=False,
                )
                started_marker = build_playback_marker(
                    payload={
                        "stream_id": stream_id,
                        "segment_id": segment_id,
                        "duration_ms": 0,
                    },
                    generation_id=frame.generation_id,
                    phase="started",
                )
                if started_marker is not None:
                    await self.push_frame(
                        OutputTransportMessageFrame(message=started_marker),
                        FrameDirection.DOWNSTREAM,
                    )
        else:
            if frame.sample_rate != seg_state.sample_rate or frame.channels != seg_state.channels:
                error = ValueError(
                    f"Audio format unstable for generation {frame.generation_id}: "
                    f"expected {seg_state.sample_rate}Hz/{seg_state.channels}ch, "
                    f"got {frame.sample_rate}Hz/{frame.channels}ch"
                )
                await self._fail_active_media_operation("unstable_audio_format", error)
                raise error

        raw_frame = OutputAudioRawFrame(
            audio=frame.audio,
            sample_rate=frame.sample_rate,
            num_channels=frame.channels,
        )
        await self.push_frame(raw_frame, FrameDirection.DOWNSTREAM)
        seg_state.total_audio_bytes += len(frame.audio)

    async def finalize_response_audio(self, generation_id: UUID) -> int | None:
        """Finalize whole-response audio after all queued PCM of all provider audio items."""
        async with self._output_lock:
            if not self._is_generation_active(generation_id):
                return None
            seg_state = self._active_segments.get(generation_id)
            if seg_state is None:
                return None

            output = asyncio.create_task(
                self._push_finalization(generation_id),
                name="cloud-finalize-handoff",
            )
            self._output_task = output
            try:
                results = await asyncio.gather(output, return_exceptions=True)
                result = results[0]
                if isinstance(result, BaseException):
                    if isinstance(result, asyncio.CancelledError):
                        return None
                    raise result
                return result
            finally:
                if self._output_task is output:
                    self._output_task = None

    async def _push_finalization(self, generation_id: UUID) -> int | None:
        seg_state = self._active_segments.get(generation_id)
        if seg_state is None or not self._is_generation_active(generation_id):
            return None

        bytes_per_sample = 2
        denom = seg_state.channels * bytes_per_sample * seg_state.sample_rate
        if denom <= 0:
            error = ValueError(f"Invalid audio format denominator: {denom}")
            await self._fail_active_media_operation("invalid_audio_format", error)
            raise error

        duration_ms = int((seg_state.total_audio_bytes * 1000) / denom)
        if duration_ms < 0:
            error = ValueError(f"Calculated negative audio duration: {duration_ms}")
            await self._fail_active_media_operation("invalid_duration", error)
            raise error

        if not self._is_generation_active(generation_id):
            return None
        await self.push_frame(TTSStoppedFrame(), FrameDirection.DOWNSTREAM)

        if not self._is_generation_active(generation_id):
            return None
        if self._playback is not None:
            # Durably commit final duration in PlaybackService BEFORE sending buffered marker
            await self._playback.finalize_segment(seg_state.segment_id, duration_ms)

        if not self._is_generation_active(generation_id):
            return None
        if self._playback is not None:
            buffered_marker = build_playback_marker(
                payload={
                    "stream_id": seg_state.stream_id,
                    "segment_id": seg_state.segment_id,
                    "duration_ms": duration_ms,
                },
                generation_id=generation_id,
                phase="buffered",
            )
            if buffered_marker is not None:
                await self.push_frame(
                    OutputTransportMessageFrame(message=buffered_marker),
                    FrameDirection.DOWNSTREAM,
                )

        self._active_segments.pop(generation_id, None)
        return duration_ms

    def clear_generation(self, generation_id: UUID) -> None:
        """Clear generation segment state upon terminal completion."""
        self._active_segments.pop(generation_id, None)
        self._generation_segment_counts.pop(generation_id, None)

    async def _teardown(self) -> None:
        self._is_torn_down = True
        self._closed_event.set()
        if self._playback is not None and self._completion_listener_token is not None:
            self._playback.unregister_completion_listener(
                self.session_id, self._completion_listener_token
            )
            self._completion_listener_token = None
        try:
            # stop owns the shielded cleanup, including media queues and sender.
            # Repeated teardown must join it even if an earlier waiter was cancelled.
            await self._coordinator.stop()
        except Exception:
            _LOGGER.warning("Error stopping coordinator during teardown", exc_info=True)

    async def cleanup(self) -> None:
        await self._teardown()
        await super().cleanup()
