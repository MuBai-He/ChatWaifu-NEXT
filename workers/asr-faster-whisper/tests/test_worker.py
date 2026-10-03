# Starlette's TestClient methods inherit partially untyped httpx compatibility overloads.
# pyright: reportPrivateUsage=false, reportUnknownMemberType=false, reportUnknownVariableType=false

import asyncio
import base64
import threading
from collections.abc import Iterator
from pathlib import Path
from uuid import UUID, uuid4

import numpy as np
import pytest
from chatwaifu_model_worker import SttTranscriptionRequest, SttTranscriptionResult
from fastapi.testclient import TestClient

from chatwaifu_asr_worker.config import WorkerSettings
from chatwaifu_asr_worker.main import create_app
from chatwaifu_asr_worker.service import (
    TranscriptionCapacityError,
    TranscriptionEngine,
    TranscriptionService,
    _prepare_whisper_audio,
    _resolve_model_source,
)


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


class FakeEngine(TranscriptionEngine):
    def transcribe(
        self,
        audio: np.ndarray,
        *,
        language: str | None,
    ) -> tuple[str, str | None]:
        assert audio.dtype == np.float32
        return "你好, 语音回合。", language


@pytest.fixture
def client(tmp_path: Path) -> Iterator[TestClient]:
    settings = WorkerSettings(
        token="test-token",  # pyright: ignore[reportArgumentType]
        model_dir=tmp_path,
        preload=True,
    )
    service = TranscriptionService(settings, engine_factory=lambda _: FakeEngine())
    with TestClient(create_app(settings, service)) as test_client:
        yield test_client


def test_worker_requires_ephemeral_token(client: TestClient) -> None:
    assert client.get("/v1/health").status_code == 401
    health = client.get("/v1/health", headers={"Authorization": "Bearer test-token"})
    assert health.status_code == 200
    assert health.json()["model_loaded"] is True
    capabilities = client.get("/v1/capabilities", headers={"Authorization": "Bearer test-token"})
    assert capabilities.status_code == 200
    assert capabilities.json() == {
        "schema_version": "1.0",
        "provider_id": "faster-whisper",
        "display_name": "faster-whisper · 本地",
        "model": "base",
        "languages": ["zh", "ja", "en"],
        "supports_partial": False,
        "supports_word_timestamps": False,
        "local_only": True,
    }


def test_capacity_rejection_is_authenticated_http_429(tmp_path: Path) -> None:
    class BusyService(TranscriptionService):
        async def transcribe(self, request: SttTranscriptionRequest) -> SttTranscriptionResult:
            del request
            raise TranscriptionCapacityError("private implementation detail")

    settings = WorkerSettings(
        token="test-token",  # pyright: ignore[reportArgumentType]
        model_dir=tmp_path,
        preload=True,
    )
    service = BusyService(settings, engine_factory=lambda _: FakeEngine())
    with TestClient(create_app(settings, service)) as client:
        body = _request().model_dump(mode="json")
        assert client.post("/v1/transcribe", json=body).status_code == 401
        response = client.post(
            "/v1/transcribe", json=body, headers={"Authorization": "Bearer test-token"}
        )
        assert response.status_code == 429
        assert response.json() == {"detail": "STT worker request capacity exceeded"}


def test_offline_pack_resolves_the_materialized_model_directory(tmp_path: Path) -> None:
    model_dir = tmp_path / "model"
    model_dir.mkdir()
    (model_dir / "config.json").write_text("{}", encoding="utf-8")
    (model_dir / "model.bin").write_bytes(b"weights")
    (model_dir / "tokenizer.json").write_text("{}", encoding="utf-8")
    settings = WorkerSettings(
        token="test-token",  # pyright: ignore[reportArgumentType]
        model="base",
        model_dir=model_dir,
        local_files_only=True,
        preload=False,
    )

    assert _resolve_model_source(settings) == str(model_dir.resolve())


def test_offline_pack_rejects_an_incomplete_model_directory(tmp_path: Path) -> None:
    settings = WorkerSettings(
        token="test-token",  # pyright: ignore[reportArgumentType]
        model="base",
        model_dir=tmp_path / "missing-model",
        local_files_only=True,
        preload=False,
    )

    with pytest.raises(RuntimeError, match=r"config.json, model.bin, tokenizer.json"):
        _resolve_model_source(settings)


def test_whisper_audio_resampler_preserves_24khz_duration_and_pitch() -> None:
    source_rate = 24_000
    frequency = 440
    time = np.arange(source_rate, dtype=np.float64) / source_rate
    pcm16 = (np.sin(2 * np.pi * frequency * time) * 16_000).astype(np.int16).tobytes()

    audio = _prepare_whisper_audio(pcm16, sample_rate=source_rate, channels=1)

    assert audio.dtype == np.float32
    assert audio.shape == (16_000,)
    assert np.max(np.abs(audio)) == pytest.approx(16_000 / 32_768, abs=0.01)
    positive_crossings = np.count_nonzero((audio[:-1] <= 0) & (audio[1:] > 0))
    assert positive_crossings == pytest.approx(frequency, abs=2)


def test_whisper_audio_resampler_downmixes_16khz_stereo() -> None:
    left = np.full(160, 16_384, dtype=np.int16)
    right = np.zeros(160, dtype=np.int16)
    interleaved = np.column_stack((left, right)).reshape(-1).tobytes()

    audio = _prepare_whisper_audio(interleaved, sample_rate=16_000, channels=2)

    assert audio.shape == (160,)
    assert audio == pytest.approx(np.full(160, 0.25, dtype=np.float32), abs=1e-6)


class CapturingEngine(TranscriptionEngine):
    def __init__(self) -> None:
        self.audio: np.ndarray | None = None

    def transcribe(
        self,
        audio: np.ndarray,
        *,
        language: str | None,
    ) -> tuple[str, str | None]:
        self.audio = audio
        return "采样率正确。", language


@pytest.mark.anyio
async def test_worker_resamples_24khz_but_reports_original_duration(tmp_path: Path) -> None:
    settings = WorkerSettings(
        token="test-token",  # pyright: ignore[reportArgumentType]
        model_dir=tmp_path,
        preload=False,
    )
    engine = CapturingEngine()
    service = TranscriptionService(settings, engine_factory=lambda _: engine)
    pcm16 = np.zeros(24_000, dtype=np.int16).tobytes()
    request = SttTranscriptionRequest(
        request_id=uuid4(),
        session_id=uuid4(),
        turn_id=uuid4(),
        generation_id=uuid4(),
        job_id=uuid4(),
        audio_base64=base64.b64encode(pcm16).decode("ascii"),
        sample_rate=24_000,
        channels=1,
        language="zh",
    )

    try:
        result = await service.transcribe(request)
    finally:
        await service.close()

    assert engine.audio is not None
    assert engine.audio.shape == (16_000,)
    assert result.duration_ms == 1_000


def test_worker_transcribes_pcm_with_generation_identity(client: TestClient) -> None:
    identifiers = {
        "request_id": str(uuid4()),
        "session_id": str(uuid4()),
        "turn_id": str(uuid4()),
        "generation_id": str(uuid4()),
        "job_id": str(uuid4()),
    }
    response = client.post(
        "/v1/transcribe",
        headers={"Authorization": "Bearer test-token"},
        json={
            **identifiers,
            "audio_base64": base64.b64encode(b"\x00\x01" * 16_000).decode("ascii"),
            "sample_rate": 16_000,
            "channels": 1,
            "language": "zh",
        },
    )

    assert response.status_code == 200
    result = response.json()
    assert result["generation_id"] == identifiers["generation_id"]
    assert result["text"] == "你好, 语音回合。"
    assert result["provider"] == "faster-whisper"


def test_worker_unloads_idle_model_and_loads_again_on_demand(client: TestClient) -> None:
    headers = {"Authorization": "Bearer test-token"}
    assert client.post("/v1/model/unload", headers=headers).json() == {"unloaded": True}
    assert client.get("/v1/health", headers=headers).json()["model_loaded"] is False

    identifiers = {
        "request_id": str(uuid4()),
        "session_id": str(uuid4()),
        "turn_id": str(uuid4()),
        "generation_id": str(uuid4()),
        "job_id": str(uuid4()),
    }
    response = client.post(
        "/v1/transcribe",
        headers=headers,
        json={
            **identifiers,
            "audio_base64": base64.b64encode(b"\x00\x01" * 160).decode("ascii"),
            "sample_rate": 16_000,
            "channels": 1,
            "language": "zh",
        },
    )

    assert response.status_code == 200
    assert client.get("/v1/health", headers=headers).json()["model_loaded"] is True


class BlockingEngine(TranscriptionEngine):
    def __init__(self) -> None:
        self.first_started = threading.Event()
        self.release_first = threading.Event()
        self.second_started = threading.Event()
        self._lock = threading.Lock()
        self._calls = 0
        self.concurrent = 0
        self.max_concurrent = 0

    def transcribe(
        self,
        audio: np.ndarray,
        *,
        language: str | None,
    ) -> tuple[str, str | None]:
        with self._lock:
            self._calls += 1
            call = self._calls
            self.concurrent += 1
            self.max_concurrent = max(self.max_concurrent, self.concurrent)
        try:
            if call == 1:
                self.first_started.set()
                self.release_first.wait(timeout=2)
                return "迟到旧结果", language
            self.second_started.set()
            return "第二轮", language
        finally:
            with self._lock:
                self.concurrent -= 1


def _request(*, generation_id: UUID | None = None) -> SttTranscriptionRequest:
    return SttTranscriptionRequest(
        request_id=uuid4(),
        session_id=uuid4(),
        turn_id=uuid4(),
        generation_id=generation_id or uuid4(),
        job_id=uuid4(),
        audio_base64=base64.b64encode(b"\x00\x01" * 160).decode("ascii"),
        sample_rate=16_000,
        channels=1,
        language="zh",
    )


class BlockingFactory:
    """Hold native initialization without loading an actual model."""

    def __init__(self, *, warm_first: bool = False, fail_load: bool = False) -> None:
        self.started = asyncio.Event()
        self.release = threading.Event()
        self.finished = asyncio.Event()
        self.calls = 0
        self.concurrent = 0
        self.max_concurrent = 0
        self._loop = asyncio.get_running_loop()
        self._lock = threading.Lock()
        self._warm_first = warm_first
        self._fail_load = fail_load

    def __call__(self, settings: WorkerSettings) -> TranscriptionEngine:
        del settings
        with self._lock:
            self.calls += 1
            call = self.calls
            self.concurrent += 1
            self.max_concurrent = max(self.max_concurrent, self.concurrent)
        try:
            if self._warm_first and call == 1:
                return FakeEngine()
            self._loop.call_soon_threadsafe(self.started.set)
            if not self.release.wait(timeout=3):
                raise RuntimeError("bounded fixture initialization was not released")
            if self._fail_load:
                self._fail_load = False
                raise RuntimeError("fixture model initialization failed")
            return FakeEngine()
        finally:
            with self._lock:
                self.concurrent -= 1
            self._loop.call_soon_threadsafe(self.finished.set)


@pytest.mark.anyio
async def test_cancelled_native_transcription_never_overlaps_next_generation(
    tmp_path: Path,
) -> None:
    settings = WorkerSettings(
        token="test-token",  # pyright: ignore[reportArgumentType]
        model_dir=tmp_path,
        preload=False,
    )
    engine = BlockingEngine()
    service = TranscriptionService(settings, engine_factory=lambda _: engine)
    first = _request()
    second = _request()
    first_task = asyncio.create_task(service.transcribe(first))
    second_task: asyncio.Task[SttTranscriptionResult] | None = None
    try:
        assert await asyncio.to_thread(engine.first_started.wait, 1)
        assert service.cancel(first.generation_id) is True
        with pytest.raises(asyncio.CancelledError):
            await first_task
        assert await service.unload() is False

        second_task = asyncio.create_task(service.transcribe(second))
        await asyncio.sleep(0)
        assert engine.second_started.is_set() is False

        engine.release_first.set()
        result = await asyncio.wait_for(second_task, timeout=1)
        assert result.text == "第二轮"
        assert engine.max_concurrent == 1
    finally:
        engine.release_first.set()
        for task in (first_task, second_task):
            if task is not None and not task.done():
                task.cancel()
        await asyncio.gather(
            *(task for task in (first_task, second_task) if task is not None),
            return_exceptions=True,
        )
        await service.close()


@pytest.mark.anyio
async def test_cancelled_native_job_keeps_capacity_until_cpu_finishes(tmp_path: Path) -> None:
    settings = WorkerSettings(
        token="test-token",  # pyright: ignore[reportArgumentType]
        model_dir=tmp_path,
        preload=True,
        max_active_jobs=1,
    )
    engine = BlockingEngine()
    service = TranscriptionService(settings, engine_factory=lambda _: engine)
    await service.start()
    first = _request()
    task = asyncio.create_task(service.transcribe(first))
    try:
        assert await asyncio.to_thread(engine.first_started.wait, 1)
        native = service._native_jobs[first.generation_id]
        assert service.cancel(first.generation_id)
        with pytest.raises(asyncio.CancelledError):
            await task
        for _ in range(5):
            with pytest.raises(TranscriptionCapacityError):
                await service.transcribe(_request())
        assert service.health().queue_depth == 1
        assert engine.second_started.is_set() is False
        engine.release_first.set()
        await asyncio.wait_for(asyncio.shield(native), timeout=1)
        result = await service.transcribe(_request())
        assert result.text == "第二轮"
        assert service.health().queue_depth == 0
        assert engine.max_concurrent == 1
    finally:
        engine.release_first.set()
        if not task.done():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        await service.close()


@pytest.mark.anyio
@pytest.mark.parametrize("after_unload", [False, True])
async def test_cancelled_initialization_retains_capacity_and_loaded_engine(
    tmp_path: Path,
    after_unload: bool,
) -> None:
    settings = WorkerSettings(
        token="test-token",  # pyright: ignore[reportArgumentType]
        model_dir=tmp_path,
        preload=after_unload,
        max_active_jobs=1,
    )
    factory = BlockingFactory(warm_first=after_unload)
    service = TranscriptionService(settings, engine_factory=factory)
    if after_unload:
        await service.start()
        assert await service.unload()
    task = asyncio.create_task(service.transcribe(_request()))
    try:
        await asyncio.wait_for(factory.started.wait(), timeout=1)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert service.health().queue_depth == 1
        assert service.health().status == "busy"
        assert not await service.unload()
        load = service._load_future
        assert load is not None and not load.done()
        for _ in range(5):
            with pytest.raises(TranscriptionCapacityError):
                await service.transcribe(_request())
        assert factory.calls == (2 if after_unload else 1)
        assert factory.max_concurrent == 1
        factory.release.set()
        await asyncio.wait_for(asyncio.shield(load), timeout=1)
        assert service.health().queue_depth == 0
        assert service.health().model_loaded
        result = await service.transcribe(_request())
        assert result.text == "你好, 语音回合。"
        assert factory.calls == (2 if after_unload else 1)
    finally:
        factory.release.set()
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        await service.close()


@pytest.mark.anyio
async def test_initialization_is_shared_by_all_logical_waiters(tmp_path: Path) -> None:
    settings = WorkerSettings(
        token="test-token",  # pyright: ignore[reportArgumentType]
        model_dir=tmp_path,
        preload=False,
        max_active_jobs=4,
    )
    factory = BlockingFactory()
    service = TranscriptionService(settings, engine_factory=factory)
    tasks = [asyncio.create_task(service.transcribe(_request())) for _ in range(4)]
    try:
        await asyncio.wait_for(factory.started.wait(), timeout=1)
        assert service.health().queue_depth == 4
        for task in tasks[:3]:
            task.cancel()
        cancelled = await asyncio.gather(*tasks[:3], return_exceptions=True)
        assert all(isinstance(value, asyncio.CancelledError) for value in cancelled)
        assert service.health().queue_depth == 1
        factory.release.set()
        result = await asyncio.wait_for(tasks[3], timeout=1)
        assert result.text == "你好, 语音回合。"
        assert factory.calls == 1 and factory.max_concurrent == 1
        assert service.health().queue_depth == 0
    finally:
        factory.release.set()
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        await service.close()


@pytest.mark.anyio
async def test_cancelled_preload_still_reserves_one_slot(tmp_path: Path) -> None:
    settings = WorkerSettings(
        token="test-token",  # pyright: ignore[reportArgumentType]
        model_dir=tmp_path,
        preload=True,
        max_active_jobs=1,
    )
    factory = BlockingFactory()
    service = TranscriptionService(settings, engine_factory=factory)
    task = asyncio.create_task(service.start())
    try:
        await asyncio.wait_for(factory.started.wait(), timeout=1)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert service.health().queue_depth == 1
        with pytest.raises(TranscriptionCapacityError):
            await service.transcribe(_request())
        load = service._load_future
        assert load is not None
        factory.release.set()
        await asyncio.wait_for(asyncio.shield(load), timeout=1)
        result = await service.transcribe(_request())
        assert result.text == "你好, 语音回合。"
        assert factory.calls == 1
    finally:
        factory.release.set()
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        await service.close()


@pytest.mark.anyio
@pytest.mark.parametrize("finish_in_grace", [False, True])
async def test_close_waits_for_initialization_with_bounded_grace(
    tmp_path: Path,
    finish_in_grace: bool,
) -> None:
    settings = WorkerSettings(
        token="test-token",  # pyright: ignore[reportArgumentType]
        model_dir=tmp_path,
        preload=False,
        max_active_jobs=1,
        shutdown_timeout_seconds=1 if finish_in_grace else 0.02,
    )
    factory = BlockingFactory()
    service = TranscriptionService(settings, engine_factory=factory)
    task = asyncio.create_task(service.transcribe(_request()))
    closing: asyncio.Task[None] | None = None
    try:
        await asyncio.wait_for(factory.started.wait(), timeout=1)
        load = service._load_future
        assert load is not None
        closing = asyncio.create_task(service.close())
        with pytest.raises(asyncio.CancelledError):
            await task
        if finish_in_grace:
            assert not closing.done()
            factory.release.set()
        await asyncio.wait_for(closing, timeout=0.5)
        if not finish_in_grace:
            assert not load.done()
            assert service.health().queue_depth == 1
        with pytest.raises(RuntimeError, match="closed"):
            await service.transcribe(_request())
        factory.release.set()
        await asyncio.wait_for(asyncio.shield(load), timeout=1)
        assert service.health().queue_depth == 0
        assert not service.health().model_loaded
        assert factory.calls == 1
    finally:
        factory.release.set()
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        if closing is not None:
            await asyncio.gather(closing, return_exceptions=True)
        await service.close()


@pytest.mark.anyio
async def test_close_keeps_native_inference_in_the_same_bounded_grace(tmp_path: Path) -> None:
    settings = WorkerSettings(
        token="test-token",  # pyright: ignore[reportArgumentType]
        model_dir=tmp_path,
        preload=True,
        shutdown_timeout_seconds=0.02,
    )
    engine = BlockingEngine()
    service = TranscriptionService(settings, engine_factory=lambda _: engine)
    await service.start()
    request = _request()
    task = asyncio.create_task(service.transcribe(request))
    try:
        assert await asyncio.to_thread(engine.first_started.wait, 1)
        native = service._native_jobs[request.generation_id]
        await asyncio.wait_for(service.close(), timeout=0.5)
        with pytest.raises(asyncio.CancelledError):
            await task
        assert not native.done()
        assert service.health().queue_depth == 1
        engine.release_first.set()
        await asyncio.wait_for(asyncio.shield(native), timeout=1)
        assert service.health().queue_depth == 0
        assert not service.health().model_loaded
    finally:
        engine.release_first.set()
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        await service.close()


@pytest.mark.anyio
@pytest.mark.parametrize("abandoned", [False, True])
async def test_failed_initialization_is_observed_and_can_retry(
    tmp_path: Path,
    abandoned: bool,
) -> None:
    settings = WorkerSettings(
        token="test-token",  # pyright: ignore[reportArgumentType]
        model_dir=tmp_path,
        preload=False,
        max_active_jobs=1,
    )
    factory = BlockingFactory(fail_load=True)
    service = TranscriptionService(settings, engine_factory=factory)
    loop = asyncio.get_running_loop()
    previous_handler = loop.get_exception_handler()
    unhandled: list[dict[str, object]] = []
    loop.set_exception_handler(lambda _, context: unhandled.append(context))
    task = asyncio.create_task(service.transcribe(_request()))
    try:
        await asyncio.wait_for(factory.started.wait(), timeout=1)
        if abandoned:
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
        load = service._load_future
        assert load is not None
        observed = asyncio.Event()
        load.add_done_callback(lambda _: observed.set())
        factory.release.set()
        await asyncio.wait_for(observed.wait(), timeout=1)
        if not abandoned:
            with pytest.raises(RuntimeError, match="fixture model initialization failed"):
                await task
        assert service.health().queue_depth == 0
        assert not service.health().model_loaded
        assert service._load_future is None
        del load
        result = await service.transcribe(_request())
        assert result.text == "你好, 语音回合。"
        assert factory.calls == 2 and not unhandled
    finally:
        loop.set_exception_handler(previous_handler)
        factory.release.set()
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        await service.close()


@pytest.mark.anyio
async def test_late_initialization_error_after_close_is_observed(tmp_path: Path) -> None:
    settings = WorkerSettings(
        token="test-token",  # pyright: ignore[reportArgumentType]
        model_dir=tmp_path,
        preload=False,
        shutdown_timeout_seconds=0.02,
    )
    factory = BlockingFactory(fail_load=True)
    service = TranscriptionService(settings, engine_factory=factory)
    loop = asyncio.get_running_loop()
    previous_handler = loop.get_exception_handler()
    unhandled: list[dict[str, object]] = []
    loop.set_exception_handler(lambda _, context: unhandled.append(context))
    task = asyncio.create_task(service.transcribe(_request()))
    try:
        await asyncio.wait_for(factory.started.wait(), timeout=1)
        load = service._load_future
        assert load is not None
        observed = asyncio.Event()
        load.add_done_callback(lambda _: observed.set())
        await asyncio.wait_for(service.close(), timeout=0.5)
        with pytest.raises(asyncio.CancelledError):
            await task
        assert not load.done()
        factory.release.set()
        await asyncio.wait_for(observed.wait(), timeout=1)
        assert service._load_future is None
        assert service.health().queue_depth == 0
        assert not service.health().model_loaded
        del load
        assert factory.calls == 1 and not unhandled
    finally:
        loop.set_exception_handler(previous_handler)
        factory.release.set()
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        await service.close()


@pytest.mark.anyio
async def test_cancelled_close_still_shuts_down_executor_and_observes_late_load(
    tmp_path: Path,
) -> None:
    settings = WorkerSettings(
        token="test-token",  # pyright: ignore[reportArgumentType]
        model_dir=tmp_path,
        preload=False,
        shutdown_timeout_seconds=1,
    )
    factory = BlockingFactory()
    service = TranscriptionService(settings, engine_factory=factory)
    task = asyncio.create_task(service.transcribe(_request()))
    closing: asyncio.Task[None] | None = None
    try:
        await asyncio.wait_for(factory.started.wait(), timeout=1)
        load = service._load_future
        assert load is not None
        closing = asyncio.create_task(service.close())
        with pytest.raises(asyncio.CancelledError):
            await task
        closing.cancel()
        with pytest.raises(asyncio.CancelledError):
            await closing
        with pytest.raises(RuntimeError, match="shutdown"):
            service._executor.submit(lambda: None)
        factory.release.set()
        await asyncio.wait_for(asyncio.shield(load), timeout=1)
        assert service.health().queue_depth == 0
        assert not service.health().model_loaded
    finally:
        factory.release.set()
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        if closing is not None:
            closing.cancel()
            await asyncio.gather(closing, return_exceptions=True)
        await service.close()
