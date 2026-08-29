"""Dependency-light speech adapters with subprocess cancellation."""

import asyncio
import json
import logging
import math
import shutil
import struct
import sys
import wave
from collections.abc import AsyncIterator, Callable
from contextlib import AbstractAsyncContextManager
from dataclasses import replace
from pathlib import Path
from typing import Any, cast
from urllib.parse import urlsplit, urlunsplit
from uuid import UUID, uuid4

import httpx2
from chatwaifu_model_worker import (
    TtsStreamCompleted as WorkerTtsStreamCompleted,
)
from chatwaifu_model_worker import (
    TtsStreamFailed as WorkerTtsStreamFailed,
)
from chatwaifu_model_worker import (
    TtsStreamReady as WorkerTtsStreamReady,
)
from chatwaifu_model_worker import (
    TtsStreamStart,
    TtsSynthesisRequest,
    TtsSynthesisResult,
    TtsWorkerCapabilities,
    WorkerHealth,
    unpack_tts_pcm_frame,
)
from websockets.asyncio.client import connect as websocket_connect

from chatwaifu_runtime.providers.contracts import (
    SynthesisRequest,
    SynthesisResult,
    TtsPcmChunk,
    TtsProviderDescriptor,
    TtsProviderHealth,
    TtsStreamCompleted,
    TtsStreamEvent,
)

logger = logging.getLogger(__name__)

type WebSocketFactory = Callable[..., AbstractAsyncContextManager[Any]]
DEFAULT_WEBSOCKET_FACTORY = cast(WebSocketFactory, websocket_connect)


class _WorkerIdentityError(RuntimeError):
    pass


class FakeTtsProvider:
    """Generate a short valid WAV tone for CI and non-macOS fallback."""

    kind = "fake"
    descriptor = TtsProviderDescriptor(
        provider_id=kind,
        display_name="测试提示音",
        model="generated-tone",
        languages=("zh", "ja", "en"),
        supports_voice_cloning=False,
        supports_style=False,
        supports_speed=False,
        supports_pitch=False,
        native_streaming=False,
    )

    def __init__(self, sample_rate: int = 24_000) -> None:
        self._sample_rate = sample_rate

    async def synthesize(self, request: SynthesisRequest) -> SynthesisResult:
        duration_ms = min(max(len(request.text) * 55, 250), 2500)
        await asyncio.to_thread(self._write_tone, request.destination, duration_ms)
        return SynthesisResult(
            request.destination,
            "audio/wav",
            self._sample_rate,
            duration_ms,
            self.kind,
            self.descriptor.model,
        )

    async def health(self) -> TtsProviderHealth:
        return TtsProviderHealth(status="ready", model_loaded=True, device="cpu")

    async def deactivate(self) -> None:
        return None

    async def close(self) -> None:
        return None

    def _write_tone(self, destination: Path, duration_ms: int) -> None:
        destination.parent.mkdir(parents=True, exist_ok=True)
        frame_count = self._sample_rate * duration_ms // 1000
        with wave.open(str(destination), "wb") as output:
            output.setnchannels(1)
            output.setsampwidth(2)
            output.setframerate(self._sample_rate)
            frames = bytearray()
            for index in range(frame_count):
                envelope = min(1.0, index / 240, (frame_count - index) / 240)
                value = int(
                    1800 * envelope * math.sin(2 * math.pi * 440 * index / self._sample_rate)
                )
                frames.extend(struct.pack("<h", value))
            output.writeframes(frames)


class MacOsSayTtsProvider:
    kind = "macos_say"
    descriptor = TtsProviderDescriptor(
        provider_id=kind,
        display_name="macOS 系统语音",
        model="say",
        languages=("zh", "ja", "en"),
        supports_voice_cloning=False,
        supports_style=False,
        supports_speed=True,
        supports_pitch=False,
        native_streaming=False,
    )

    def __init__(self, *, voice: str, sample_rate: int, rate: int, timeout_seconds: float) -> None:
        self._voice = voice
        self._sample_rate = sample_rate
        self._rate = rate
        self._timeout_seconds = timeout_seconds
        if sys.platform != "darwin" or not shutil.which("say") or not shutil.which("afconvert"):
            raise RuntimeError("macOS say and afconvert are required")

    async def synthesize(self, request: SynthesisRequest) -> SynthesisResult:
        request.destination.parent.mkdir(parents=True, exist_ok=True)
        source = request.destination.with_suffix(".aiff")
        try:
            await self._run(
                "say",
                "-v",
                self._voice,
                "-r",
                str(self._rate),
                "-o",
                str(source),
                "-f",
                "-",
                input_text=request.text,
            )
            await self._run(
                "afconvert",
                "-f",
                "WAVE",
                "-d",
                f"LEI16@{self._sample_rate}",
                str(source),
                str(request.destination),
            )
            duration_ms = await asyncio.to_thread(_wave_duration_ms, request.destination)
            return SynthesisResult(
                request.destination,
                "audio/wav",
                self._sample_rate,
                duration_ms,
                self.kind,
                self.descriptor.model,
            )
        finally:
            source.unlink(missing_ok=True)

    async def close(self) -> None:
        return None

    async def health(self) -> TtsProviderHealth:
        return TtsProviderHealth(status="ready", model_loaded=True, device="cpu")

    async def deactivate(self) -> None:
        return None

    async def _run(self, *command: str, input_text: str | None = None) -> None:
        process = await asyncio.create_subprocess_exec(
            *command,
            stdin=(
                asyncio.subprocess.PIPE if input_text is not None else asyncio.subprocess.DEVNULL
            ),
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdin = input_text.encode("utf-8") if input_text is not None else None
            _, stderr = await asyncio.wait_for(
                process.communicate(stdin), timeout=self._timeout_seconds
            )
        except BaseException:
            if process.returncode is None:
                process.terminate()
                await process.wait()
            raise
        if process.returncode:
            detail = stderr.decode(errors="replace").strip()
            raise RuntimeError(f"speech command failed ({process.returncode}): {detail}")


class WorkerTtsProvider:
    """Adapter for the versioned, provider-neutral local TTS worker API."""

    def __init__(
        self,
        *,
        descriptor: TtsProviderDescriptor,
        base_url: str,
        token: str | None,
        timeout_seconds: float,
        client: httpx2.AsyncClient | None = None,
        websocket_factory: WebSocketFactory = DEFAULT_WEBSOCKET_FACTORY,
        max_stream_audio_bytes: int = 64_000_000,
    ) -> None:
        self._descriptor = replace(descriptor, native_streaming=False)
        self._configured_native_streaming = descriptor.native_streaming
        self._base_url = base_url.rstrip("/")
        self._headers = {"Authorization": f"Bearer {token}"} if token else {}
        self._client = client or httpx2.AsyncClient(timeout=timeout_seconds)
        self._websocket_factory = websocket_factory
        self._timeout_seconds = timeout_seconds
        self._max_stream_audio_bytes = max_stream_audio_bytes
        self._capabilities_negotiated = False
        self._capability_lock = asyncio.Lock()

    @property
    def kind(self) -> str:
        return self._descriptor.provider_id

    @property
    def descriptor(self) -> TtsProviderDescriptor:
        return self._descriptor

    async def synthesize(self, request: SynthesisRequest) -> SynthesisResult:
        body = self._worker_request(request)
        try:
            response = await self._client.post(
                f"{self._base_url}/v1/synthesize",
                headers=self._headers,
                json=body.model_dump(mode="json"),
            )
            response.raise_for_status()
        except asyncio.CancelledError:
            try:
                await asyncio.shield(self._cancel(request.generation_id))
            except Exception:
                logger.warning(
                    "failed to propagate TTS cancellation for generation %s",
                    request.generation_id,
                    exc_info=True,
                )
            raise
        result = TtsSynthesisResult.model_validate(response.json())
        self._validate_identity(body, result)
        audio = result.audio_bytes()
        request.destination.parent.mkdir(parents=True, exist_ok=True)
        request.destination.write_bytes(audio)
        return SynthesisResult(
            request.destination,
            result.media_type,
            result.sample_rate,
            result.duration_ms,
            self.kind,
            result.model,
        )

    async def stream(self, request: SynthesisRequest) -> AsyncIterator[TtsStreamEvent]:
        if not await self._supports_pcm_v2():
            async for event in self._stream_complete_wave(request):
                yield event
            return
        body = self._worker_request(request)
        start = TtsStreamStart(request=body)
        request.destination.parent.mkdir(parents=True, exist_ok=True)
        request.destination.unlink(missing_ok=True)
        audio = bytearray()
        expected_sequence = 0
        sample_rate: int | None = None
        channels: int | None = None
        completion: WorkerTtsStreamCompleted | None = None
        headers = dict(self._headers)
        ready_received = False
        try:
            async with self._websocket_factory(
                _worker_stream_url(self._base_url),
                additional_headers=headers,
                open_timeout=self._timeout_seconds,
                close_timeout=2,
                max_size=4_100_000,
            ) as websocket:
                await websocket.send(start.model_dump_json())
                ready_frame = await asyncio.wait_for(
                    websocket.recv(), timeout=self._timeout_seconds
                )
                if not isinstance(ready_frame, str):
                    raise RuntimeError("TTS worker did not acknowledge the stream")
                ready = WorkerTtsStreamReady.model_validate_json(ready_frame)
                self._validate_stream_identity(body, ready)
                ready_received = True
                while True:
                    frame = await asyncio.wait_for(websocket.recv(), timeout=self._timeout_seconds)
                    if isinstance(frame, bytes):
                        chunk = unpack_tts_pcm_frame(frame)
                        if (
                            chunk.generation_id != request.generation_id
                            or chunk.job_id != request.segment_id
                        ):
                            raise RuntimeError("TTS worker returned a stale PCM frame")
                        if chunk.sequence != expected_sequence:
                            raise RuntimeError("TTS worker returned an out-of-order PCM frame")
                        if sample_rate is None:
                            sample_rate, channels = chunk.sample_rate, chunk.channels
                        elif (sample_rate, channels) != (chunk.sample_rate, chunk.channels):
                            raise RuntimeError("TTS worker changed PCM format during one stream")
                        if len(audio) + len(chunk.pcm16) > self._max_stream_audio_bytes:
                            raise RuntimeError("TTS worker stream exceeded the safety limit")
                        audio.extend(chunk.pcm16)
                        yield TtsPcmChunk(
                            sequence=chunk.sequence,
                            pcm16=chunk.pcm16,
                            sample_rate=chunk.sample_rate,
                            channels=chunk.channels,
                            native_streaming=True,
                        )
                        expected_sequence += 1
                        continue
                    event = json.loads(frame)
                    if event.get("event") == "tts.stream.failed":
                        failed = WorkerTtsStreamFailed.model_validate(event)
                        self._validate_stream_identity(body, failed)
                        raise RuntimeError(f"TTS worker {failed.code}: {failed.detail}")
                    if event.get("event") != "tts.stream.completed":
                        raise RuntimeError("TTS worker returned an unknown stream event")
                    completion = WorkerTtsStreamCompleted.model_validate(event)
                    self._validate_stream_identity(body, completion)
                    break
        except asyncio.CancelledError:
            request.destination.unlink(missing_ok=True)
            try:
                await asyncio.shield(self._cancel(request.generation_id))
            except Exception:
                logger.warning(
                    "failed to propagate streaming TTS cancellation for generation %s",
                    request.generation_id,
                    exc_info=True,
                )
            raise
        except Exception as error:
            request.destination.unlink(missing_ok=True)
            if not ready_received and not isinstance(error, _WorkerIdentityError):
                self._disable_pcm_v2()
                async for event in self._stream_complete_wave(request):
                    yield event
                return
            raise
        if sample_rate is None or channels is None or not audio:
            raise RuntimeError("TTS worker ended without completed PCM audio")
        if (
            completion.chunk_count != expected_sequence
            or completion.sample_rate != sample_rate
            or completion.channels != channels
        ):
            raise RuntimeError("TTS worker completion metadata does not match the PCM stream")
        await asyncio.to_thread(
            _write_pcm_wave,
            request.destination,
            bytes(audio),
            sample_rate,
            channels,
        )
        result = SynthesisResult(
            request.destination,
            "audio/wav",
            sample_rate,
            completion.duration_ms,
            completion.provider,
            completion.model,
        )
        yield TtsStreamCompleted(result=result)

    @staticmethod
    def _validate_identity(body: TtsSynthesisRequest, result: TtsSynthesisResult) -> None:
        expected_identity = (
            body.request_id,
            body.session_id,
            body.turn_id,
            body.generation_id,
            body.job_id,
            body.speaker_id,
        )
        actual_identity = (
            result.request_id,
            result.session_id,
            result.turn_id,
            result.generation_id,
            result.job_id,
            result.speaker_id,
        )
        if actual_identity != expected_identity:
            raise _WorkerIdentityError("TTS worker returned mismatched request identity")

    @staticmethod
    def _validate_stream_identity(body: TtsSynthesisRequest, event: Any) -> None:
        expected_identity = (
            body.request_id,
            body.session_id,
            body.turn_id,
            body.generation_id,
            body.job_id,
        )
        actual_identity = (
            event.request_id,
            event.session_id,
            event.turn_id,
            event.generation_id,
            event.job_id,
        )
        if actual_identity != expected_identity:
            raise _WorkerIdentityError("TTS worker returned mismatched stream identity")

    @staticmethod
    def _worker_request(request: SynthesisRequest) -> TtsSynthesisRequest:
        return TtsSynthesisRequest(
            request_id=uuid4(),
            session_id=request.session_id,
            turn_id=request.turn_id,
            generation_id=request.generation_id,
            job_id=request.segment_id,
            text=request.text,
            language=request.language,
            voice_id=request.voice_id,
            speaker_id=request.speaker_id,
            speed=request.speed,
            style=request.style,
            pitch=request.pitch,
        )

    async def health(self) -> TtsProviderHealth:
        try:
            response = await self._client.get(
                f"{self._base_url}/v1/health",
                headers=self._headers,
                timeout=2.0,
            )
            response.raise_for_status()
            result = WorkerHealth.model_validate(response.json())
        except Exception as error:
            return TtsProviderHealth(
                status="unavailable",
                model_loaded=False,
                detail=str(error),
            )
        return TtsProviderHealth(
            status=result.status,
            model_loaded=result.model_loaded,
            queue_depth=result.queue_depth,
            device=result.device,
        )

    async def refresh_descriptor(self) -> TtsProviderDescriptor:
        return await self.refresh_capabilities()

    async def refresh_capabilities(self) -> TtsProviderDescriptor:
        """Negotiate optional protocols; old or sleeping workers remain valid v1 peers."""

        async with self._capability_lock:
            try:
                response = await self._client.get(
                    f"{self._base_url}/v1/capabilities",
                    headers=self._headers,
                    timeout=min(self._timeout_seconds, 2.0),
                )
                response.raise_for_status()
                result = TtsWorkerCapabilities.model_validate(response.json())
            except asyncio.CancelledError:
                raise
            except Exception:
                self._disable_pcm_v2()
                return self._descriptor
            if result.provider_id != self.kind:
                self._disable_pcm_v2()
                raise _WorkerIdentityError("TTS worker returned mismatched provider identity")
            self._descriptor = TtsProviderDescriptor(
                provider_id=result.provider_id,
                display_name=result.display_name,
                model=result.model,
                languages=tuple(result.languages),
                supports_voice_cloning=result.supports_voice_cloning,
                supports_style=result.supports_style,
                supports_speed=result.supports_speed,
                supports_pitch=result.supports_pitch,
                native_streaming=(
                    self._configured_native_streaming
                    and result.native_streaming
                    and "pcm.v2" in result.stream_protocols
                ),
                local_only=result.local_only,
            )
            self._capabilities_negotiated = True
            return self._descriptor

    async def _supports_pcm_v2(self) -> bool:
        if not self._capabilities_negotiated:
            await self.refresh_capabilities()
        return self._descriptor.native_streaming

    def _disable_pcm_v2(self) -> None:
        self._descriptor = replace(self._descriptor, native_streaming=False)
        self._capabilities_negotiated = True

    async def _stream_complete_wave(
        self, request: SynthesisRequest
    ) -> AsyncIterator[TtsStreamEvent]:
        result = await self.synthesize(request)
        chunks, sample_rate, channels = await asyncio.to_thread(
            _read_pcm_wave_chunks, request.destination
        )
        for sequence, pcm16 in enumerate(chunks):
            yield TtsPcmChunk(
                sequence=sequence,
                pcm16=pcm16,
                sample_rate=sample_rate,
                channels=channels,
                native_streaming=False,
            )
        yield TtsStreamCompleted(result=result)

    async def _cancel(self, generation_id: UUID) -> None:
        response = await self._client.post(
            f"{self._base_url}/v1/jobs/{generation_id}/cancel",
            headers=self._headers,
        )
        response.raise_for_status()

    async def deactivate(self) -> None:
        try:
            response = await self._client.post(
                f"{self._base_url}/v1/model/unload",
                headers=self._headers,
                timeout=15.0,
            )
            if response.status_code == 404:
                return
            response.raise_for_status()
        except httpx2.ConnectError:
            return
        finally:
            # A sleeping/restarted worker may expose a different protocol set.
            # Negotiate again on the next synthesis instead of trusting stale v2.
            self._capabilities_negotiated = False

    async def close(self) -> None:
        await self._client.aclose()


class SherpaKokoroWorkerTtsProvider(WorkerTtsProvider):
    def __init__(
        self,
        *,
        base_url: str,
        token: str,
        timeout_seconds: float,
        client: httpx2.AsyncClient | None = None,
    ) -> None:
        super().__init__(
            descriptor=TtsProviderDescriptor(
                provider_id="sherpa_kokoro_worker",
                display_name="Kokoro 轻量语音",
                model="kokoro-multi-lang-v1_1",
                languages=("zh", "en"),
                supports_voice_cloning=False,
                supports_style=False,
                supports_speed=True,
                supports_pitch=False,
                native_streaming=False,
            ),
            base_url=base_url,
            token=token,
            timeout_seconds=timeout_seconds,
            client=client,
        )


def _wave_duration_ms(path: Path) -> int:
    with wave.open(str(path), "rb") as audio:
        return round(audio.getnframes() * 1000 / audio.getframerate())


def _worker_stream_url(base_url: str) -> str:
    parsed = urlsplit(base_url)
    if parsed.scheme not in {"http", "https"}:
        raise ValueError("TTS worker base URL must use http or https")
    scheme = "wss" if parsed.scheme == "https" else "ws"
    path = f"{parsed.path.rstrip('/')}/v2/stream/tts"
    return urlunsplit((scheme, parsed.netloc, path, "", ""))


def _write_pcm_wave(path: Path, pcm16: bytes, sample_rate: int, channels: int) -> None:
    if len(pcm16) % (channels * 2):
        raise ValueError("PCM16 stream is not frame aligned")
    with wave.open(str(path), "wb") as output:
        output.setnchannels(channels)
        output.setsampwidth(2)
        output.setframerate(sample_rate)
        output.writeframes(pcm16)


def _read_pcm_wave_chunks(path: Path, chunk_ms: int = 100) -> tuple[list[bytes], int, int]:
    with wave.open(str(path), "rb") as source:
        if source.getsampwidth() != 2:
            raise ValueError("worker WAV fallback requires PCM16 audio")
        sample_rate = source.getframerate()
        channels = source.getnchannels()
        frames_per_chunk = max(1, sample_rate * chunk_ms // 1000)
        chunks: list[bytes] = []
        while chunk := source.readframes(frames_per_chunk):
            chunks.append(chunk)
        return chunks, sample_rate, channels
