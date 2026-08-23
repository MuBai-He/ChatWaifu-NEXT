"""Dependency-free STT fallback used before a local worker is configured."""

from uuid import UUID

from chatwaifu_runtime.realtime.contracts import SttRequest, SttResult


class DisabledSttBackend:
    kind = "disabled"

    async def transcribe(self, request: SttRequest) -> SttResult | None:
        del request
        return None

    async def cancel(self, generation_id: UUID) -> None:
        del generation_id

    async def close(self) -> None:
        return None
