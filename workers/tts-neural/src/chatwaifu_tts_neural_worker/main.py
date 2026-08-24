"""Authenticated loopback API shared by Qwen MLX and GPT-SoVITS."""

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from typing import Annotated
from uuid import UUID

import uvicorn
from chatwaifu_model_worker import TtsSynthesisRequest, TtsSynthesisResult, TtsWorkerCapabilities
from fastapi import Depends, FastAPI, Header, HTTPException, status

from chatwaifu_tts_neural_worker import __version__
from chatwaifu_tts_neural_worker.config import WorkerSettings
from chatwaifu_tts_neural_worker.service import SynthesisService


def create_app(
    settings: WorkerSettings | None = None,
    service: SynthesisService | None = None,
) -> FastAPI:
    resolved = settings or WorkerSettings()  # pyright: ignore[reportCallIssue]
    synthesis = service or SynthesisService(resolved)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
        app.state.settings = resolved
        app.state.synthesis = synthesis
        await synthesis.start()
        try:
            yield
        finally:
            await synthesis.close()

    app = FastAPI(
        title="ChatWaifu unified neural TTS worker",
        version=__version__,
        lifespan=lifespan,
    )

    def authorize(authorization: Annotated[str | None, Header()] = None) -> None:
        expected = f"Bearer {resolved.token.get_secret_value()}"
        if authorization != expected:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="unauthorized")

    @app.get("/v1/health", dependencies=[Depends(authorize)])
    async def health() -> dict[str, object]:
        return synthesis.health().model_dump(mode="json")

    @app.get(
        "/v1/capabilities",
        response_model=TtsWorkerCapabilities,
        dependencies=[Depends(authorize)],
    )
    async def capabilities() -> TtsWorkerCapabilities:
        return synthesis.capabilities()

    @app.post(
        "/v1/synthesize",
        response_model=TtsSynthesisResult,
        dependencies=[Depends(authorize)],
    )
    async def synthesize(body: TtsSynthesisRequest) -> TtsSynthesisResult:
        return await synthesis.synthesize(body)

    @app.post("/v1/jobs/{generation_id}/cancel", dependencies=[Depends(authorize)])
    async def cancel(generation_id: UUID) -> dict[str, object]:
        return {
            "generation_id": str(generation_id),
            "cancelled": synthesis.cancel(generation_id),
        }

    @app.post("/v1/model/unload", dependencies=[Depends(authorize)])
    async def unload() -> dict[str, object]:
        return {"unloaded": await synthesis.unload()}

    _ = health, capabilities, synthesize, cancel, unload
    return app


def run() -> None:
    settings = WorkerSettings()  # pyright: ignore[reportCallIssue]
    uvicorn.run(
        create_app(settings),
        host=settings.host,
        port=settings.port,
        log_config=None,
        loop="asyncio",
    )


if __name__ == "__main__":
    run()
