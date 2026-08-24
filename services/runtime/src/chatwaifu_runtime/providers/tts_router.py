"""Session-scoped routing over replaceable TTS providers."""

import asyncio
from dataclasses import dataclass
from uuid import UUID

from chatwaifu_runtime.providers.contracts import (
    SynthesisRequest,
    SynthesisResult,
    TtsProvider,
    TtsProviderDescriptor,
    TtsProviderHealth,
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
        provider_id = self.bind_session(request.session_id)
        provider = self._providers[provider_id]
        self._active_jobs[provider_id] += 1
        try:
            return await provider.synthesize(request)
        finally:
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
        return tuple(snapshots)

    async def close(self) -> None:
        self._session_providers.clear()
        await asyncio.gather(
            *(provider.close() for provider in self._providers.values()),
            return_exceptions=False,
        )

    async def _deactivate_if_unused(self, provider_id: str) -> None:
        selected_elsewhere = provider_id in self._session_providers.values()
        if selected_elsewhere or self._active_jobs[provider_id] > 0:
            return
        await self._providers[provider_id].deactivate()
