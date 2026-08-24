"""Authenticated loopback HTTP entrypoint for the isolated Kokoro worker."""

# The local SDK is installed from a path dependency.
# pyright: reportMissingTypeStubs=false

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from typing import Annotated
from uuid import UUID

import uvicorn
from chatwaifu_model_worker import TtsSynthesisRequest, TtsSynthesisResult, WorkerHealth
from fastapi import Depends, FastAPI, Header, HTTPException, status

from chatwaifu_tts_worker.config import WorkerSettings
from chatwaifu_tts_worker.service import SynthesisService


def create_app(
    settings: WorkerSettings | None = None,
    service: SynthesisService | None = None,
) -> FastAPI:
    resolved = settings or WorkerSettings()  # pyright: ignore[reportCallIssue]
    synthesis = service or SynthesisService(resolved)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncGenerator[None]:
        app.state.settings = resolved
        app.state.synthesis = synthesis
        await synthesis.start()
        try:
            yield
        finally:
            await synthesis.close()

    app = FastAPI(title="ChatWaifu sherpa-onnx Kokoro worker", version="0.1.0", lifespan=lifespan)

    def authorize(authorization: Annotated[str | None, Header()] = None) -> None:
        expected = f"Bearer {resolved.token.get_secret_value()}"
        if authorization != expected:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="unauthorized")

    @app.get("/v1/health", response_model=WorkerHealth, dependencies=[Depends(authorize)])
    async def health() -> WorkerHealth:
        return synthesis.health()

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

    _ = health, synthesize, cancel
    return app


def run() -> None:
    settings = WorkerSettings()  # pyright: ignore[reportCallIssue]
    uvicorn.run(create_app(settings), host=settings.host, port=settings.port, log_config=None)


if __name__ == "__main__":
    run()
