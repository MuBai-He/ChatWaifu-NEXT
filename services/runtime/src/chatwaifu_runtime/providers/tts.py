"""Dependency-light speech adapters with subprocess cancellation."""

import asyncio
import logging
import math
import shutil
import struct
import sys
import wave
from pathlib import Path
from uuid import UUID, uuid4

import httpx2
from chatwaifu_model_worker import (
    TtsSynthesisRequest,
    TtsSynthesisResult,
    TtsWorkerCapabilities,
    WorkerHealth,
)

from chatwaifu_runtime.providers.contracts import (
    SynthesisRequest,
    SynthesisResult,
    TtsProviderDescriptor,
    TtsProviderHealth,
)

logger = logging.getLogger(__name__)


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
        if sys.platform != "darwin" or not shutil.which("say") or not shutil.which("afconvert"):
            raise RuntimeError("macOS say and afconvert are required")
        self._voice = voice
        self._sample_rate = sample_rate
        self._rate = rate
        self._timeout_seconds = timeout_seconds

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
    ) -> None:
        self._descriptor = descriptor
        self._base_url = base_url.rstrip("/")
        self._headers = {"Authorization": f"Bearer {token}"} if token else {}
        self._client = client or httpx2.AsyncClient(timeout=timeout_seconds)

    @property
    def kind(self) -> str:
        return self._descriptor.provider_id

    @property
    def descriptor(self) -> TtsProviderDescriptor:
        return self._descriptor

    async def synthesize(self, request: SynthesisRequest) -> SynthesisResult:
        body = TtsSynthesisRequest(
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
        expected_identity = (
            body.request_id,
            request.session_id,
            request.turn_id,
            request.generation_id,
            request.segment_id,
            request.speaker_id,
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
            raise RuntimeError("TTS worker returned mismatched request identity")
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

    async def health(self) -> TtsProviderHealth:
        try:
            response = await self._client.get(
                f"{self._base_url}/v1/health",
                headers=self._headers,
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
        response = await self._client.get(
            f"{self._base_url}/v1/capabilities",
            headers=self._headers,
        )
        response.raise_for_status()
        result = TtsWorkerCapabilities.model_validate(response.json())
        if result.provider_id != self.kind:
            raise RuntimeError("TTS worker returned mismatched provider identity")
        return TtsProviderDescriptor(
            provider_id=result.provider_id,
            display_name=result.display_name,
            model=result.model,
            languages=tuple(result.languages),
            supports_voice_cloning=result.supports_voice_cloning,
            supports_style=result.supports_style,
            supports_speed=result.supports_speed,
            supports_pitch=result.supports_pitch,
            native_streaming=result.native_streaming,
            local_only=result.local_only,
        )

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
            )
            if response.status_code == 404:
                return
            response.raise_for_status()
        except httpx2.ConnectError:
            return

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
