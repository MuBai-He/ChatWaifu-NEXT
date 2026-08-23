"""STT backend adapters; model code remains in an isolated worker process."""

import base64
from typing import Literal, cast
from uuid import UUID, uuid4

import httpx2
from chatwaifu_model_worker import SttTranscriptionRequest, SttTranscriptionResult

from chatwaifu_runtime.config.settings import Settings
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


class FasterWhisperWorkerSttBackend:
    kind = "faster_whisper_worker"

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

    async def transcribe(self, request: SttRequest) -> SttResult | None:
        if request.channels not in {1, 2}:
            raise ValueError(f"unsupported STT channel count: {request.channels}")
        channels = cast(Literal[1, 2], request.channels)
        body = SttTranscriptionRequest(
            request_id=uuid4(),
            session_id=request.identity.session_id,
            turn_id=request.identity.turn_id,
            generation_id=request.identity.generation_id,
            job_id=uuid4(),
            audio_base64=base64.b64encode(request.audio).decode("ascii"),
            sample_rate=request.sample_rate,
            channels=channels,
            language=request.language,
        )
        response = await self._client.post(
            f"{self._base_url}/v1/transcribe",
            headers=self._headers,
            json=body.model_dump(mode="json"),
        )
        response.raise_for_status()
        result = SttTranscriptionResult.model_validate(response.json())
        if result.generation_id != request.identity.generation_id:
            raise RuntimeError("STT worker returned a mismatched generation_id")
        return SttResult(
            text=result.text,
            language=result.language,
            provider=result.provider,
        )

    async def cancel(self, generation_id: UUID) -> None:
        response = await self._client.post(
            f"{self._base_url}/v1/jobs/{generation_id}/cancel",
            headers=self._headers,
        )
        response.raise_for_status()

    async def close(self) -> None:
        await self._client.aclose()


def build_stt_backend(settings: Settings) -> DisabledSttBackend | FasterWhisperWorkerSttBackend:
    if settings.stt.provider == "disabled":
        return DisabledSttBackend()
    if settings.stt.provider == "faster_whisper_worker":
        if settings.stt.worker_token is None:
            raise ValueError("STT worker token is required")
        return FasterWhisperWorkerSttBackend(
            base_url=settings.stt.worker_url,
            token=settings.stt.worker_token.get_secret_value(),
            timeout_seconds=settings.stt.timeout_seconds,
        )
    raise ValueError(f"unsupported STT provider: {settings.stt.provider}")
