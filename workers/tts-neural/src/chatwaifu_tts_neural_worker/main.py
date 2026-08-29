"""Authenticated loopback API shared by Qwen MLX and GPT-SoVITS."""

import asyncio
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from typing import Annotated, Literal
from uuid import UUID

import uvicorn
from chatwaifu_model_worker import (
    TtsPcmFrame,
    TtsStreamCompleted,
    TtsStreamFailed,
    TtsStreamReady,
    TtsStreamStart,
    TtsSynthesisRequest,
    TtsSynthesisResult,
    TtsWorkerCapabilities,
    pack_tts_pcm_frame,
)
from fastapi import Depends, FastAPI, Header, HTTPException, WebSocket, status
from pydantic import ValidationError

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

    @app.websocket("/v2/stream/tts")
    async def stream_tts(websocket: WebSocket) -> None:
        expected = f"Bearer {resolved.token.get_secret_value()}"
        if websocket.headers.get("authorization") != expected:
            await websocket.close(code=4401, reason="unauthorized")
            return
        await websocket.accept()
        try:
            start = TtsStreamStart.model_validate_json(await websocket.receive_text())
        except (ValidationError, ValueError):
            await websocket.close(code=1003, reason="invalid stream start")
            return

        async def pump() -> None:
            request = start.request
            await websocket.send_json(
                TtsStreamReady(
                    request_id=request.request_id,
                    session_id=request.session_id,
                    turn_id=request.turn_id,
                    generation_id=request.generation_id,
                    job_id=request.job_id,
                ).model_dump(mode="json")
            )
            sequence = 0
            total_bytes = 0
            sample_rate: int | None = None
            channels: int | None = None
            try:
                async for chunk in synthesis.stream(request):
                    if sample_rate is None:
                        sample_rate, channels = chunk.sample_rate, chunk.channels
                    elif (sample_rate, channels) != (chunk.sample_rate, chunk.channels):
                        raise RuntimeError("TTS engine changed PCM format during one stream")
                    total_bytes += len(chunk.pcm16)
                    if total_bytes > resolved.max_stream_audio_bytes:
                        raise RuntimeError("TTS stream exceeded the configured safety limit")
                    await websocket.send_bytes(
                        pack_tts_pcm_frame(
                            TtsPcmFrame(
                                generation_id=request.generation_id,
                                job_id=request.job_id,
                                sequence=sequence,
                                sample_rate=chunk.sample_rate,
                                channels=chunk.channels,
                                pcm16=chunk.pcm16,
                            )
                        )
                    )
                    sequence += 1
                if sample_rate is None or channels is None or sequence == 0:
                    raise RuntimeError("TTS stream completed without audio")
                duration_ms = round(total_bytes * 1000 / (sample_rate * channels * 2))
                await websocket.send_json(
                    TtsStreamCompleted(
                        request_id=request.request_id,
                        session_id=request.session_id,
                        turn_id=request.turn_id,
                        generation_id=request.generation_id,
                        job_id=request.job_id,
                        sample_rate=sample_rate,
                        channels=channels,
                        duration_ms=duration_ms,
                        chunk_count=sequence,
                        provider=resolved.provider_id,
                        model=resolved.model,
                        speaker_id=request.speaker_id,
                    ).model_dump(mode="json")
                )
            except asyncio.CancelledError:
                raise
            except Exception as error:
                code = (
                    "generation_busy"
                    if "generation already has" in str(error)
                    else "synthesis_failed"
                )
                await _send_stream_failure(websocket, start, code, error)

        pump_task = asyncio.create_task(pump(), name=f"tts-stream-{start.request.job_id}")
        disconnect_task = asyncio.create_task(
            websocket.receive(), name=f"tts-stream-watch-{start.request.job_id}"
        )
        done, _ = await asyncio.wait(
            (pump_task, disconnect_task), return_when=asyncio.FIRST_COMPLETED
        )
        if disconnect_task in done:
            synthesis.cancel(start.request.generation_id)
            pump_task.cancel("worker_client_disconnected")
        else:
            disconnect_task.cancel("worker_stream_completed")
        await asyncio.gather(pump_task, disconnect_task, return_exceptions=True)
        try:
            await websocket.close(code=1000)
        except RuntimeError:
            pass

    _ = health, capabilities, synthesize, cancel, unload, stream_tts
    return app


async def _send_stream_failure(
    websocket: WebSocket,
    start: TtsStreamStart,
    code: Literal["generation_busy", "synthesis_failed"],
    error: Exception,
) -> None:
    request = start.request
    detail = str(error).strip() or error.__class__.__name__
    try:
        await websocket.send_json(
            TtsStreamFailed(
                request_id=request.request_id,
                session_id=request.session_id,
                turn_id=request.turn_id,
                generation_id=request.generation_id,
                job_id=request.job_id,
                code=code,
                detail=detail[:500],
            ).model_dump(mode="json")
        )
    except RuntimeError:
        return


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
