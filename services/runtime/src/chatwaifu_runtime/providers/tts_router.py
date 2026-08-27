"""Session-scoped routing over replaceable TTS providers."""

import asyncio
import wave
from collections.abc import AsyncIterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast
from uuid import UUID

from chatwaifu_runtime.providers.contracts import (
    SynthesisRequest,
    SynthesisResult,
    TtsPcmChunk,
    TtsProvider,
    TtsProviderDescriptor,
    TtsProviderHealth,
    TtsStreamCompleted,
    TtsStreamEvent,
)


@dataclass(frozen=True, slots=True)
class TtsProviderSnapshot:
    descriptor: TtsProviderDescriptor
    health: TtsProviderHealth
    selected: bool


class TtsRouter:
    """Choose a provider per session without leaking engine APIs upstream."""

    def __init__(self, providers: dict[str, TtsProvider], default_provider: str) -> None:
        if default_provider not in providers:
            raise ValueError(f"default TTS provider is not configured: {default_provider}")
        self._providers = dict(providers)
        self._default_provider = default_provider
        self._session_providers: dict[UUID, str] = {}
        self._active_jobs: dict[str, int] = {provider_id: 0 for provider_id in providers}
        self._lock = asyncio.Lock()

    @property
    def kind(self) -> str:
        return self._default_provider

    def bind_session(self, session_id: UUID) -> str:
        return self._session_providers.setdefault(session_id, self._default_provider)

    def provider_for(self, session_id: UUID) -> str:
        return self._session_providers.get(session_id, self._default_provider)

    async def select(self, session_id: UUID, provider_id: str) -> str:
        if provider_id not in self._providers:
            raise KeyError(provider_id)
        async with self._lock:
            previous = self.bind_session(session_id)
            self._session_providers[session_id] = provider_id
            if previous != provider_id:
                await self._deactivate_if_unused(previous)
        return provider_id

    async def release_session(self, session_id: UUID) -> None:
        async with self._lock:
            previous = self._session_providers.pop(session_id, None)
            if previous is not None:
                await self._deactivate_if_unused(previous)

    async def synthesize(self, request: SynthesisRequest) -> SynthesisResult:
        async with self._lock:
            provider_id = self.bind_session(request.session_id)
            provider = self._providers[provider_id]
            self._active_jobs[provider_id] += 1
        try:
            return await provider.synthesize(request)
        finally:
            async with self._lock:
                self._active_jobs[provider_id] -= 1

    async def stream(self, request: SynthesisRequest) -> AsyncIterator[TtsStreamEvent]:
        """Normalize native streams and batch WAV providers into ordered PCM16 events."""

        async with self._lock:
            provider_id = self.bind_session(request.session_id)
            provider = self._providers[provider_id]
            self._active_jobs[provider_id] += 1
        try:
            native_stream = getattr(provider, "stream", None)
            if callable(native_stream):
                iterator = cast(Any, native_stream)(request)
                async for event in iterator:
                    yield cast(TtsStreamEvent, event)
                return
            result = await provider.synthesize(request)
            chunks, sample_rate, channels = await asyncio.to_thread(_read_wave_chunks, result.path)
            for sequence, pcm16 in enumerate(chunks):
                yield TtsPcmChunk(
                    sequence=sequence,
                    pcm16=pcm16,
                    sample_rate=sample_rate,
                    channels=channels,
                )
            yield TtsStreamCompleted(result=result)
        finally:
            async with self._lock:
                self._active_jobs[provider_id] -= 1

    async def snapshots(self, session_id: UUID | None = None) -> tuple[TtsProviderSnapshot, ...]:
        selected = (
            self.provider_for(session_id) if session_id is not None else self._default_provider
        )
        snapshots: list[TtsProviderSnapshot] = []
        for provider_id, provider in self._providers.items():
            snapshots.append(
                TtsProviderSnapshot(
                    descriptor=provider.descriptor,
                    health=await provider.health(),
                    selected=provider_id == selected,
                )
            )
        snapshots.sort(key=lambda item: (not item.selected, item.descriptor.display_name))
        return tuple(snapshots)

    async def probe(self, provider_id: str) -> dict[str, object]:
        provider = self._providers.get(provider_id)
        if provider is None:
            raise KeyError(provider_id)
        probe = getattr(provider, "probe", None)
        if not callable(probe):
            health = await provider.health()
            return {"status": health.status, "detail": health.detail}
        return cast(dict[str, object], await cast(Any, probe)())

    async def close(self) -> None:
        self._session_providers.clear()
        await asyncio.gather(
            *(provider.close() for provider in self._providers.values()),
            return_exceptions=False,
        )

    @property
    def active_jobs(self) -> int:
        return sum(self._active_jobs.values())

    async def deactivate_idle(self) -> bool:
        """Unload model weights without changing session routing selections."""

        deactivated = False
        async with self._lock:
            for provider_id, provider in self._providers.items():
                if self._active_jobs[provider_id] > 0:
                    continue
                await provider.deactivate()
                deactivated = True
        return deactivated

    async def _deactivate_if_unused(self, provider_id: str) -> None:
        selected_elsewhere = provider_id in self._session_providers.values()
        if selected_elsewhere or self._active_jobs[provider_id] > 0:
            return
        await self._providers[provider_id].deactivate()


def _read_wave_chunks(path: Path, chunk_ms: int = 100) -> tuple[list[bytes], int, int]:
    with wave.open(str(path), "rb") as source:
        if source.getsampwidth() != 2:
            raise ValueError(f"streaming playback requires PCM16 WAV: {path}")
        sample_rate = source.getframerate()
        channels = source.getnchannels()
        frames_per_chunk = max(1, sample_rate * chunk_ms // 1000)
        chunks: list[bytes] = []
        while chunk := source.readframes(frames_per_chunk):
            chunks.append(chunk)
        return chunks, sample_rate, channels
