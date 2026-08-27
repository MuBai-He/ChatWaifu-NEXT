"""Authenticated loopback HTTP entrypoint for the isolated ASR worker."""

# The local SDK is installed from a path dependency; some editable installers do not
# expose its py.typed marker to Pyright until the environment is rebuilt.
# pyright: reportMissingTypeStubs=false

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from typing import Annotated
from uuid import UUID

import uvicorn
from chatwaifu_model_worker import SttTranscriptionRequest, SttTranscriptionResult, WorkerHealth
from fastapi import Depends, FastAPI, Header, HTTPException, status

from chatwaifu_asr_worker.config import WorkerSettings
from chatwaifu_asr_worker.service import TranscriptionService


def create_app(
    settings: WorkerSettings | None = None,
    service: TranscriptionService | None = None,
) -> FastAPI:
    resolved = settings or WorkerSettings()  # pyright: ignore[reportCallIssue]
    transcription = service or TranscriptionService(resolved)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncGenerator[None]:
        app.state.settings = resolved
        app.state.transcription = transcription
        await transcription.start()
        try:
            yield
        finally:
            await transcription.close()

    app = FastAPI(title="ChatWaifu faster-whisper worker", version="0.1.0", lifespan=lifespan)

    def authorize(authorization: Annotated[str | None, Header()] = None) -> None:
        expected = f"Bearer {resolved.token.get_secret_value()}"
        if authorization != expected:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="unauthorized")

    @app.get("/v1/health", response_model=WorkerHealth, dependencies=[Depends(authorize)])
    async def health() -> WorkerHealth:
        return transcription.health()

    @app.post(
        "/v1/transcribe",
        response_model=SttTranscriptionResult,
        dependencies=[Depends(authorize)],
    )
    async def transcribe(body: SttTranscriptionRequest) -> SttTranscriptionResult:
        return await transcription.transcribe(body)

    @app.post(
        "/v1/jobs/{generation_id}/cancel",
        dependencies=[Depends(authorize)],
    )
    async def cancel(generation_id: UUID) -> dict[str, object]:
        return {
            "generation_id": str(generation_id),
            "cancelled": transcription.cancel(generation_id),
        }

    @app.post("/v1/model/unload", dependencies=[Depends(authorize)])
    async def unload() -> dict[str, object]:
        return {"unloaded": await transcription.unload()}

    _ = health, transcribe, cancel, unload

    return app


def run() -> None:
    settings = WorkerSettings()  # pyright: ignore[reportCallIssue]
    uvicorn.run(create_app(settings), host=settings.host, port=settings.port, log_config=None)


if __name__ == "__main__":
    run()
