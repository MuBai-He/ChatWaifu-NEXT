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
from chatwaifu_model_worker import TtsSynthesisRequest, TtsSynthesisResult

from chatwaifu_runtime.providers.contracts import SynthesisRequest, SynthesisResult

logger = logging.getLogger(__name__)


class FakeTtsProvider:
    """Generate a short valid WAV tone for CI and non-macOS fallback."""

    kind = "fake"

    def __init__(self, sample_rate: int = 24_000) -> None:
        self._sample_rate = sample_rate

    async def synthesize(self, request: SynthesisRequest) -> SynthesisResult:
        duration_ms = min(max(len(request.text) * 55, 250), 2500)
        await asyncio.to_thread(self._write_tone, request.destination, duration_ms)
        return SynthesisResult(request.destination, "audio/wav", self._sample_rate, duration_ms)

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
            return SynthesisResult(request.destination, "audio/wav", self._sample_rate, duration_ms)
        finally:
            source.unlink(missing_ok=True)

    async def close(self) -> None:
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


class SherpaKokoroWorkerTtsProvider:
    kind = "sherpa_kokoro_worker"

    def __init__(
        self,
        *,
        base_url: str,
        token: str,
        timeout_seconds: float,
        client: httpx2.AsyncClient | None = None,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._headers = {"Authorization": f"Bearer {token}"}
        self._client = client or httpx2.AsyncClient(timeout=timeout_seconds)

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
        )

    async def _cancel(self, generation_id: UUID) -> None:
        response = await self._client.post(
            f"{self._base_url}/v1/jobs/{generation_id}/cancel",
            headers=self._headers,
        )
        response.raise_for_status()

    async def close(self) -> None:
        await self._client.aclose()


def _wave_duration_ms(path: Path) -> int:
    with wave.open(str(path), "rb") as audio:
        return round(audio.getnframes() * 1000 / audio.getframerate())
