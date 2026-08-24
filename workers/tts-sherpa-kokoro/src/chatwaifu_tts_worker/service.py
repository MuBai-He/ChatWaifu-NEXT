"""Bounded, generation-aware offline Kokoro synthesis service."""

# sherpa-onnx does not publish complete Python typing metadata. Keep the untyped
# model API confined to this worker adapter.
# pyright: reportMissingTypeStubs=false, reportUnknownMemberType=false, reportUnknownVariableType=false

import asyncio
import base64
import io
import wave
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from functools import partial
from typing import Protocol, cast
from uuid import UUID

import numpy as np
from chatwaifu_model_worker import TtsSynthesisRequest, TtsSynthesisResult, WorkerHealth
from numpy.typing import NDArray

from chatwaifu_tts_worker.config import WorkerSettings


class SynthesisEngine(Protocol):
    def synthesize(self, text: str, *, speaker_id: int, speed: float) -> tuple[bytes, int, int]: ...


class _GeneratedAudio(Protocol):
    samples: NDArray[np.float32]
    sample_rate: int


class SherpaKokoroEngine:
    def __init__(self, settings: WorkerSettings) -> None:
        import sherpa_onnx

        root = settings.model_dir
        config = sherpa_onnx.OfflineTtsConfig(
            model=sherpa_onnx.OfflineTtsModelConfig(
                kokoro=sherpa_onnx.OfflineTtsKokoroModelConfig(
                    model=str(root / "model.onnx"),
                    voices=str(root / "voices.bin"),
                    tokens=str(root / "tokens.txt"),
                    data_dir=str(root / "espeak-ng-data"),
                    lexicon=",".join(
                        [str(root / "lexicon-us-en.txt"), str(root / "lexicon-zh.txt")]
                    ),
                ),
                num_threads=settings.num_threads,
                debug=False,
            )
        )
        if not config.validate():
            raise ValueError(f"invalid Kokoro model configuration under {root}")
        self._tts = sherpa_onnx.OfflineTts(config)

    def synthesize(self, text: str, *, speaker_id: int, speed: float) -> tuple[bytes, int, int]:
        generated = cast(
            _GeneratedAudio,
            self._tts.generate(text=text, sid=speaker_id, speed=speed),
        )
        samples: NDArray[np.float32] = np.asarray(generated.samples, dtype=np.float32)
        sample_rate = int(generated.sample_rate)
        pcm: NDArray[np.int16] = (np.clip(samples, -1.0, 1.0) * 32767).astype("<i2", copy=False)
        buffer = io.BytesIO()
        with wave.open(buffer, "wb") as output:
            output.setnchannels(1)
            output.setsampwidth(2)
            output.setframerate(sample_rate)
            output.writeframes(pcm.tobytes())
        duration_ms = round(len(pcm) * 1000 / sample_rate)
        return buffer.getvalue(), sample_rate, duration_ms


class SynthesisService:
    def __init__(
        self,
        settings: WorkerSettings,
        engine_factory: Callable[[WorkerSettings], SynthesisEngine] = SherpaKokoroEngine,
    ) -> None:
        self._settings = settings
        self._engine_factory = engine_factory
        self._engine: SynthesisEngine | None = None
        self._load_lock = asyncio.Lock()
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="kokoro-tts")
        self._jobs: dict[UUID, asyncio.Task[TtsSynthesisResult]] = {}

    async def start(self) -> None:
        if self._settings.preload:
            await self._ensure_loaded()

    async def synthesize(self, request: TtsSynthesisRequest) -> TtsSynthesisResult:
        current = asyncio.current_task()
        if current is None:
            raise RuntimeError("synthesis request is not running in an asyncio task")
        self._jobs[request.generation_id] = current
        try:
            engine = await self._ensure_loaded()
            loop = asyncio.get_running_loop()
            audio, sample_rate, duration_ms = await loop.run_in_executor(
                self._executor,
                partial(
                    engine.synthesize,
                    request.text,
                    speaker_id=request.speaker_id,
                    speed=request.speed,
                ),
            )
            return TtsSynthesisResult(
                request_id=request.request_id,
                session_id=request.session_id,
                turn_id=request.turn_id,
                generation_id=request.generation_id,
                job_id=request.job_id,
                audio_base64=base64.b64encode(audio).decode("ascii"),
                sample_rate=sample_rate,
                duration_ms=duration_ms,
                provider="sherpa-onnx-kokoro",
                model=self._settings.model,
                speaker_id=request.speaker_id,
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
            device="cpu",
            capabilities=["tts.synthesize", "tts.cancel", "voice.kokoro", "health"],
        )

    async def close(self) -> None:
        tasks = tuple(self._jobs.values())
        for task in tasks:
            task.cancel("worker_stopping")
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self._jobs.clear()
        self._executor.shutdown(wait=False, cancel_futures=True)

    async def _ensure_loaded(self) -> SynthesisEngine:
        if self._engine is not None:
            return self._engine
        async with self._load_lock:
            if self._engine is None:
                self._engine = await asyncio.to_thread(self._engine_factory, self._settings)
        return self._engine
