"""Lazy/preloaded faster-whisper engine with generation-scoped job cancellation."""

# faster-whisper exposes useful annotations but does not mark its wheel as typed and
# leaves a few nested dictionaries unparameterized. Confine that imprecision here.
# pyright: reportMissingTypeStubs=false, reportUnknownMemberType=false

import asyncio
from collections.abc import Callable
from typing import Protocol
from uuid import UUID

import numpy as np
from chatwaifu_model_worker import (
    SttTranscriptionRequest,
    SttTranscriptionResult,
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
        self._jobs: dict[UUID, asyncio.Task[SttTranscriptionResult]] = {}

    async def start(self) -> None:
        if self._settings.preload:
            await self._ensure_loaded()

    async def transcribe(self, request: SttTranscriptionRequest) -> SttTranscriptionResult:
        current = asyncio.current_task()
        if current is None:
            raise RuntimeError("transcription request is not running in an asyncio task")
        typed_current = current  # narrowed for strict type checking
        self._jobs[request.generation_id] = typed_current
        try:
            engine = await self._ensure_loaded()
            async with self._run_lock:
                audio = np.frombuffer(request.audio_bytes(), dtype=np.int16).astype(np.float32)
                audio /= 32768.0
                if request.channels == 2:
                    audio = audio.reshape(-1, 2).mean(axis=1)
                text, language = await asyncio.to_thread(
                    engine.transcribe,
                    audio,
                    language=request.language,
                )
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

    def health(self) -> WorkerHealth:
        queue_depth = sum(not task.done() for task in self._jobs.values())
        return WorkerHealth(
            status="busy" if queue_depth else "ready",
            worker_id=self._settings.worker_id,
            model_loaded=self._engine is not None,
            model=self._settings.model,
            queue_depth=queue_depth,
            device=self._settings.device,
            capabilities=["stt.final", "stt.cancel", "health"],
        )

    async def _ensure_loaded(self) -> TranscriptionEngine:
        if self._engine is not None:
            return self._engine
        async with self._load_lock:
            if self._engine is None:
                self._settings.model_dir.mkdir(parents=True, exist_ok=True)
                self._engine = await asyncio.to_thread(self._engine_factory, self._settings)
        return self._engine
