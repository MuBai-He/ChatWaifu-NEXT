"""Dependency-light speech adapters with subprocess cancellation."""

import asyncio
import math
import shutil
import struct
import sys
import wave
from pathlib import Path

from chatwaifu_runtime.providers.contracts import SynthesisResult


class FakeTtsProvider:
    """Generate a short valid WAV tone for CI and non-macOS fallback."""

    kind = "fake"

    def __init__(self, sample_rate: int = 24_000) -> None:
        self._sample_rate = sample_rate

    async def synthesize(self, text: str, destination: Path) -> SynthesisResult:
        duration_ms = min(max(len(text) * 55, 250), 2500)
        await asyncio.to_thread(self._write_tone, destination, duration_ms)
        return SynthesisResult(destination, "audio/wav", self._sample_rate, duration_ms)

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

    async def synthesize(self, text: str, destination: Path) -> SynthesisResult:
        destination.parent.mkdir(parents=True, exist_ok=True)
        source = destination.with_suffix(".aiff")
        try:
            await self._run(
                "say",
                "-v",
                self._voice,
                "-r",
                str(self._rate),
                "-o",
                str(source),
                text,
            )
            await self._run(
                "afconvert",
                "-f",
                "WAVE",
                "-d",
                f"LEI16@{self._sample_rate}",
                str(source),
                str(destination),
            )
            duration_ms = await asyncio.to_thread(_wave_duration_ms, destination)
            return SynthesisResult(destination, "audio/wav", self._sample_rate, duration_ms)
        finally:
            source.unlink(missing_ok=True)

    async def _run(self, *command: str) -> None:
        process = await asyncio.create_subprocess_exec(
            *command, stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.PIPE
        )
        try:
            _, stderr = await asyncio.wait_for(process.communicate(), timeout=self._timeout_seconds)
        except BaseException:
            if process.returncode is None:
                process.terminate()
                await process.wait()
            raise
        if process.returncode:
            detail = stderr.decode(errors="replace").strip()
            raise RuntimeError(f"speech command failed ({process.returncode}): {detail}")


def _wave_duration_ms(path: Path) -> int:
    with wave.open(str(path), "rb") as audio:
        return round(audio.getnframes() * 1000 / audio.getframerate())
