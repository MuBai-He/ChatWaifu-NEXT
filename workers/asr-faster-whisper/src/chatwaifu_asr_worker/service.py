"""Lazy/preloaded faster-whisper engine with generation-scoped job cancellation."""

# faster-whisper exposes useful annotations but does not mark its wheel as typed and
# leaves a few nested dictionaries unparameterized. Confine that imprecision here.
# pyright: reportMissingTypeStubs=false, reportUnknownMemberType=false

import asyncio
import gc
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from functools import partial
from typing import Protocol
from uuid import UUID

import av
import numpy as np
from chatwaifu_model_worker import (
    SttTranscriptionRequest,
    SttTranscriptionResult,
    SttWorkerCapabilities,
    WorkerHealth,
)

from chatwaifu_asr_worker.config import WorkerSettings

WHISPER_SAMPLE_RATE = 16_000


class TranscriptionCapacityError(RuntimeError):
    """A cancelled native inference continues to own its bounded worker slot."""


class TranscriptionEngine(Protocol):
    def transcribe(
        self,
        audio: np.ndarray,
        *,
        language: str | None,
    ) -> tuple[str, str | None]: ...


class FasterWhisperEngine:
    def __init__(self, settings: WorkerSettings) -> None:
        from faster_whisper import WhisperModel

        self._beam_size = settings.beam_size
        self._chinese_initial_prompt = settings.chinese_initial_prompt
        model_source = _resolve_model_source(settings)
        self._model = WhisperModel(
            model_source,
            device=settings.device,
            compute_type=settings.compute_type,
            download_root=str(settings.model_dir),
            local_files_only=settings.local_files_only,
        )

    def transcribe(
        self,
        audio: np.ndarray,
        *,
        language: str | None,
    ) -> tuple[str, str | None]:
        segments, info = self._model.transcribe(
            audio,
            language=language,
            beam_size=self._beam_size,
            initial_prompt=self._chinese_initial_prompt if language == "zh" else None,
            condition_on_previous_text=False,
            vad_filter=False,
        )
        text = "".join(segment.text for segment in segments).strip()
        return text, info.language or language


def _resolve_model_source(settings: WorkerSettings) -> str:
    if not settings.local_files_only:
        return settings.model
    model_dir = settings.model_dir.resolve()
    required = (
        model_dir / "config.json",
        model_dir / "model.bin",
        model_dir / "tokenizer.json",
    )
    missing = [path.name for path in required if not path.is_file()]
    if missing:
        raise RuntimeError(
            "offline faster-whisper model directory is incomplete; missing " + ", ".join(missing)
        )
    return str(model_dir)


def _prepare_whisper_audio(
    pcm16: bytes,
    *,
    sample_rate: int,
    channels: int,
) -> np.ndarray:
    """Convert interleaved PCM16 at the protocol rate to 16 kHz mono float32."""

    audio = np.frombuffer(pcm16, dtype=np.int16).astype(np.float32)
    audio /= 32768.0
    if channels == 2:
        audio = audio.reshape(-1, 2).mean(axis=1)
    frame = av.AudioFrame.from_ndarray(audio.reshape(1, -1), format="flt", layout="mono")
    frame.sample_rate = sample_rate
    resampler = av.AudioResampler(
        format="flt",
        layout="mono",
        rate=WHISPER_SAMPLE_RATE,
    )
    output_frames = resampler.resample(frame)
    output_frames.extend(resampler.resample(None))
    output = [output.to_ndarray().reshape(-1) for output in output_frames]
    if not output:
        return np.empty(0, dtype=np.float32)
    return np.concatenate(output).astype(np.float32, copy=False)


class TranscriptionService:
    def __init__(
        self,
        settings: WorkerSettings,
        engine_factory: Callable[[WorkerSettings], TranscriptionEngine] = FasterWhisperEngine,
    ) -> None:
        self._settings = settings
        self._engine_factory = engine_factory
        self._engine: TranscriptionEngine | None = None
        self._load_future: asyncio.Future[TranscriptionEngine] | None = None
        self._closed = False
        self._load_lock = asyncio.Lock()
        self._run_lock = asyncio.Lock()
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix=settings.worker_id)
        self._jobs: dict[UUID, asyncio.Task[SttTranscriptionResult]] = {}
        self._native_jobs: dict[UUID, asyncio.Future[tuple[str, str | None]]] = {}

    async def start(self) -> None:
        if self._settings.preload:
            await self._ensure_loaded()

    async def transcribe(self, request: SttTranscriptionRequest) -> SttTranscriptionResult:
        if self._closed:
            raise RuntimeError("transcription service is closed")
        current = asyncio.current_task()
        if current is None:
            raise RuntimeError("transcription request is not running in an asyncio task")
        typed_current = current  # narrowed for strict type checking
        if request.generation_id in self._jobs or request.generation_id in self._native_jobs:
            raise RuntimeError("generation already has an active STT job")
        if self._active_job_count() >= self._settings.max_active_jobs:
            raise TranscriptionCapacityError("STT worker request capacity exceeded")
        self._jobs[request.generation_id] = typed_current
        try:
            engine = await self._ensure_loaded()
            async with self._run_lock:
                pcm16 = request.audio_bytes()
                audio = _prepare_whisper_audio(
                    pcm16,
                    sample_rate=request.sample_rate,
                    channels=request.channels,
                )
                loop = asyncio.get_running_loop()
                native_job = loop.run_in_executor(
                    self._executor,
                    partial(engine.transcribe, audio, language=request.language),
                )
                self._native_jobs[request.generation_id] = native_job
                native_job.add_done_callback(
                    partial(self._native_job_finished, request.generation_id)
                )
                text, language = await asyncio.shield(native_job)
            duration_ms = len(pcm16) * 1000 // (request.sample_rate * request.channels * 2)
            return SttTranscriptionResult(
                request_id=request.request_id,
                session_id=request.session_id,
                turn_id=request.turn_id,
                generation_id=request.generation_id,
                job_id=request.job_id,
                text=text,
                language=language,
                confidence=None,
                duration_ms=duration_ms,
                provider="faster-whisper",
            )
        finally:
            if self._jobs.get(request.generation_id) is current:
                self._jobs.pop(request.generation_id, None)

    def cancel(self, generation_id: UUID) -> bool:
        task = self._jobs.get(generation_id)
        if task is None or task.done():
            return False
        task.cancel("generation_cancelled")
        return True

    async def unload(self) -> bool:
        if self._active_job_count():
            return False
        async with self._load_lock:
            if self._active_job_count() or self._engine is None:
                return False
            self._engine = None
            await asyncio.to_thread(gc.collect)
        return True

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        native_finished = False
        try:
            for generation_id in tuple(self._jobs):
                self.cancel(generation_id)
            tasks = tuple(self._jobs.values())
            if tasks:
                await asyncio.gather(*tasks, return_exceptions=True)
            pending: list[
                asyncio.Future[TranscriptionEngine] | asyncio.Future[tuple[str, str | None]]
            ] = [future for future in self._native_jobs.values() if not future.done()]
            if self._load_future is not None and not self._load_future.done():
                pending.append(self._load_future)
            still_running: set[
                asyncio.Future[TranscriptionEngine] | asyncio.Future[tuple[str, str | None]]
            ]
            if pending:
                _, still_running = await asyncio.wait(
                    pending,
                    timeout=self._settings.shutdown_timeout_seconds,
                )
            else:
                still_running = set()
            native_finished = not still_running
            if native_finished:
                await self.unload()
        finally:
            # Outstanding native calls retain their own bound engine/factory. A
            # late initialization cannot repopulate the cache after shutdown.
            # Even cancellation of close must stop accepting executor work.
            self._engine = None
            self._executor.shutdown(wait=native_finished, cancel_futures=True)

    def health(self) -> WorkerHealth:
        queue_depth = self._active_job_count()
        return WorkerHealth(
            status="busy" if queue_depth else "ready",
            worker_id=self._settings.worker_id,
            model_loaded=self._engine is not None,
            model=self._settings.model,
            queue_depth=queue_depth,
            device=self._settings.device,
            capabilities=["stt.final", "stt.cancel", "health"],
        )

    def capabilities(self) -> SttWorkerCapabilities:
        return SttWorkerCapabilities(
            provider_id=self._settings.provider_id,
            display_name=self._settings.display_name,
            model=self._settings.model,
            languages=["zh", "ja", "en"],
            supports_partial=False,
            supports_word_timestamps=False,
            local_only=True,
        )

    async def _ensure_loaded(self) -> TranscriptionEngine:
        if self._closed:
            raise RuntimeError("transcription service is closed")
        if self._engine is not None:
            return self._engine
        async with self._load_lock:
            if self._closed:
                raise RuntimeError("transcription service is closed")
            self._discard_finished_native_jobs()
            if self._engine is not None:
                return self._engine
            if self._load_future is None:
                self._settings.model_dir.mkdir(parents=True, exist_ok=True)
                self._load_future = asyncio.get_running_loop().run_in_executor(
                    self._executor, self._engine_factory, self._settings
                )
                self._load_future.add_done_callback(self._model_load_finished)
            future = self._load_future
        # All callers await the same native operation. Cancellation only removes
        # that logical waiter; the unfinished initialization retains one slot.
        return await asyncio.shield(future)

    def _model_load_finished(self, future: asyncio.Future[TranscriptionEngine]) -> None:
        error = None if future.cancelled() else future.exception()
        if self._load_future is future:
            self._load_future = None
            if not future.cancelled() and error is None and not self._closed:
                self._engine = future.result()

    def _active_job_count(self) -> int:
        self._discard_finished_native_jobs()
        active = {
            generation_id for generation_id, task in self._jobs.items() if not task.done()
        } | {
            generation_id
            for generation_id, future in self._native_jobs.items()
            if not future.done()
        }
        # Logical waiters already cover the shared initialization. Once all
        # withdraw, the still-running load continues to reserve one slot.
        loading = self._load_future is not None and not self._load_future.done()
        return max(len(active), int(loading))

    def _native_job_finished(
        self,
        generation_id: UUID,
        future: asyncio.Future[tuple[str, str | None]],
    ) -> None:
        if not future.cancelled():
            _ = future.exception()
        if self._native_jobs.get(generation_id) is future:
            self._native_jobs.pop(generation_id, None)

    def _discard_finished_native_jobs(self) -> None:
        if self._load_future is not None and self._load_future.done():
            self._model_load_finished(self._load_future)
        for generation_id, future in tuple(self._native_jobs.items()):
            if future.done() and self._native_jobs.get(generation_id) is future:
                self._native_jobs.pop(generation_id, None)
