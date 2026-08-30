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

import numpy as np
from chatwaifu_model_worker import (
    SttTranscriptionRequest,
    SttTranscriptionResult,
    SttWorkerCapabilities,
    WorkerHealth,
)

from chatwaifu_asr_worker.config import WorkerSettings


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

        self._model = WhisperModel(
            settings.model,
            device=settings.device,
            compute_type=settings.compute_type,
            download_root=str(settings.model_dir),
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
            beam_size=1,
            condition_on_previous_text=False,
            vad_filter=False,
        )
        text = "".join(segment.text for segment in segments).strip()
        return text, info.language or language


class TranscriptionService:
    def __init__(
        self,
        settings: WorkerSettings,
        engine_factory: Callable[[WorkerSettings], TranscriptionEngine] = FasterWhisperEngine,
    ) -> None:
        self._settings = settings
        self._engine_factory = engine_factory
        self._engine: TranscriptionEngine | None = None
        self._load_lock = asyncio.Lock()
        self._run_lock = asyncio.Lock()
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix=settings.worker_id)
        self._jobs: dict[UUID, asyncio.Task[SttTranscriptionResult]] = {}
        self._native_jobs: dict[UUID, asyncio.Future[tuple[str, str | None]]] = {}

    async def start(self) -> None:
        if self._settings.preload:
            await self._ensure_loaded()

    async def transcribe(self, request: SttTranscriptionRequest) -> SttTranscriptionResult:
        current = asyncio.current_task()
        if current is None:
            raise RuntimeError("transcription request is not running in an asyncio task")
        typed_current = current  # narrowed for strict type checking
        if request.generation_id in self._jobs or request.generation_id in self._native_jobs:
            raise RuntimeError("generation already has an active STT job")
        self._jobs[request.generation_id] = typed_current
        try:
            engine = await self._ensure_loaded()
            async with self._run_lock:
                audio = np.frombuffer(request.audio_bytes(), dtype=np.int16).astype(np.float32)
                audio /= 32768.0
                if request.channels == 2:
                    audio = audio.reshape(-1, 2).mean(axis=1)
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
            duration_ms = (
                len(request.audio_bytes()) * 1000 // (request.sample_rate * request.channels * 2)
            )
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
        self._discard_finished_native_jobs()
        if any(not task.done() for task in self._jobs.values()) or any(
            not future.done() for future in self._native_jobs.values()
        ):
            return False
        async with self._load_lock:
            if self._engine is None:
                return False
            self._engine = None
            await asyncio.to_thread(gc.collect)
        return True

    async def close(self) -> None:
        for generation_id in tuple(self._jobs):
            self.cancel(generation_id)
        tasks = tuple(self._jobs.values())
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        pending = tuple(future for future in self._native_jobs.values() if not future.done())
        still_running: set[asyncio.Future[tuple[str, str | None]]]
        if pending:
            _, still_running = await asyncio.wait(
                pending,
                timeout=self._settings.shutdown_timeout_seconds,
            )
        else:
            still_running = set()
        if not still_running:
            await self.unload()
        self._executor.shutdown(wait=not still_running, cancel_futures=True)

    def health(self) -> WorkerHealth:
        self._discard_finished_native_jobs()
        active = {
            generation_id for generation_id, task in self._jobs.items() if not task.done()
        } | {
            generation_id
            for generation_id, future in self._native_jobs.items()
            if not future.done()
        }
        queue_depth = len(active)
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
        if self._engine is not None:
            return self._engine
        async with self._load_lock:
            if self._engine is None:
                self._settings.model_dir.mkdir(parents=True, exist_ok=True)
                self._engine = await asyncio.to_thread(self._engine_factory, self._settings)
        return self._engine

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
        for generation_id, future in tuple(self._native_jobs.items()):
            if future.done() and self._native_jobs.get(generation_id) is future:
                self._native_jobs.pop(generation_id, None)
