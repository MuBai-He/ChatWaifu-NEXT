"""Bounded, generation-aware synthesis shared by all neural engines."""

import asyncio
import base64
import threading
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from functools import partial
from uuid import UUID

from chatwaifu_model_worker import (
    TtsSynthesisRequest,
    TtsSynthesisResult,
    TtsWorkerCapabilities,
    WorkerHealth,
)

from chatwaifu_tts_neural_worker.config import WorkerSettings
from chatwaifu_tts_neural_worker.engines import SynthesisEngine, build_engine


class SynthesisService:
    def __init__(
        self,
        settings: WorkerSettings,
        engine_factory: Callable[[WorkerSettings], SynthesisEngine] = build_engine,
    ) -> None:
        self._settings = settings
        self._engine_factory = engine_factory
        self._engine: SynthesisEngine | None = None
        self._load_lock = asyncio.Lock()
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix=settings.provider_id)
        self._jobs: dict[UUID, asyncio.Task[TtsSynthesisResult]] = {}
        self._cancel_events: dict[UUID, threading.Event] = {}

    async def start(self) -> None:
        if self._settings.preload:
            await self._ensure_loaded()

    async def synthesize(self, request: TtsSynthesisRequest) -> TtsSynthesisResult:
        current = asyncio.current_task()
        if current is None:
            raise RuntimeError("synthesis request is not running in an asyncio task")
        if request.generation_id in self._jobs:
            raise RuntimeError("generation already has an active TTS job")
        cancel_event = threading.Event()
        self._jobs[request.generation_id] = current
        self._cancel_events[request.generation_id] = cancel_event
        try:
            engine = await self._ensure_loaded()
            loop = asyncio.get_running_loop()
            audio, sample_rate, duration_ms = await loop.run_in_executor(
                self._executor,
                partial(engine.synthesize, request, cancel_event),
            )
            if cancel_event.is_set():
                raise asyncio.CancelledError("generation_cancelled")
            return TtsSynthesisResult(
                request_id=request.request_id,
                session_id=request.session_id,
                turn_id=request.turn_id,
                generation_id=request.generation_id,
                job_id=request.job_id,
                audio_base64=base64.b64encode(audio).decode("ascii"),
                sample_rate=sample_rate,
                duration_ms=duration_ms,
                provider=self._settings.provider_id,
                model=self._settings.model,
                speaker_id=request.speaker_id,
            )
        finally:
            if self._jobs.get(request.generation_id) is current:
                self._jobs.pop(request.generation_id, None)
                self._cancel_events.pop(request.generation_id, None)

    def cancel(self, generation_id: UUID) -> bool:
        task = self._jobs.get(generation_id)
        event = self._cancel_events.get(generation_id)
        if task is None or event is None or task.done():
            return False
        event.set()
        if self._engine is not None:
            self._engine.cancel()
        task.cancel("generation_cancelled")
        return True

    async def unload(self) -> bool:
        was_loaded = self._engine is not None
        for generation_id in tuple(self._jobs):
            self.cancel(generation_id)
        tasks = tuple(self._jobs.values())
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        async with self._load_lock:
            engine = self._engine
            if engine is not None:
                loop = asyncio.get_running_loop()
                await loop.run_in_executor(self._executor, engine.unload)
                self._engine = None
        return was_loaded

    def health(self) -> WorkerHealth:
        queue_depth = sum(not task.done() for task in self._jobs.values())
        return WorkerHealth(
            status="busy" if queue_depth else "ready",
            worker_id=self._settings.worker_id,
            model_loaded=self._engine is not None,
            model=self._settings.model,
            queue_depth=queue_depth,
            device=self._engine.device if self._engine is not None else self._settings.device,
            capabilities=["tts.synthesize", "tts.cancel", "tts.unload", "health"],
        )

    def capabilities(self) -> TtsWorkerCapabilities:
        qwen = self._settings.backend == "qwen3_tts_mlx"
        return TtsWorkerCapabilities(
            provider_id=self._settings.provider_id,
            display_name=self._settings.display_name,
            model=self._settings.model,
            languages=["zh", "ja", "en"],
            supports_voice_cloning=True,
            supports_style=False,
            supports_speed=False,
            supports_pitch=False,
            native_streaming=qwen,
            local_only=True,
        )

    async def close(self) -> None:
        await self.unload()
        self._executor.shutdown(wait=True, cancel_futures=True)

    async def _ensure_loaded(self) -> SynthesisEngine:
        if self._engine is not None:
            return self._engine
        async with self._load_lock:
            if self._engine is None:
                loop = asyncio.get_running_loop()
                self._engine = await loop.run_in_executor(
                    self._executor, self._engine_factory, self._settings
                )
        engine = self._engine
        if engine is None:
            raise RuntimeError("TTS engine failed to load")
        return engine
