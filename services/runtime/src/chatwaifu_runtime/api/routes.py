"""Loopback Runtime HTTP and WebSocket routes."""

import asyncio
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query, Request, WebSocket, WebSocketDisconnect, status
from fastapi.responses import FileResponse

from chatwaifu_runtime import __version__
from chatwaifu_runtime.api.models import (
    CreateSessionRequest,
    InterruptRequest,
    ResetSessionRequest,
    RuntimeHealth,
    SubmitTextRequest,
    WebRtcOfferRequest,
    WebRtcPatchRequest,
)
from chatwaifu_runtime.bootstrap.container import RuntimeContainer

router = APIRouter(prefix="/v1")


def _container(request: Request) -> RuntimeContainer:
    return request.app.state.container


@router.get("/runtime/health", response_model=RuntimeHealth)
async def runtime_health(request: Request) -> RuntimeHealth:
    container = _container(request)
    return RuntimeHealth(
        status="ok",
        version=__version__,
        database="ready",
        subscribers=container.event_hub.subscriber_count,
        dropped_events=container.event_hub.dropped_events,
        providers=container.providers.public_status(),
    )


@router.get("/runtime/version")
async def runtime_version() -> dict[str, str]:
    return {"name": "chatwaifu-runtime", "version": __version__, "protocol": "1.0"}


@router.get("/config")
async def runtime_config(request: Request) -> dict[str, object]:
    return _container(request).settings.public_dict()


@router.post("/sessions", status_code=status.HTTP_201_CREATED)
async def create_session(request: Request, body: CreateSessionRequest) -> dict[str, object]:
    container = _container(request)
    if container.characters.get(body.character_id) is None:
        raise HTTPException(status_code=404, detail="character not found")
    snapshot = await container.sessions.create_session(body.character_id)
    return snapshot.model_dump(mode="json")


@router.get("/sessions/{session_id}")
async def get_session(request: Request, session_id: UUID) -> dict[str, object]:
    snapshot = await _container(request).sessions.get_session(session_id)
    if snapshot is None:
        raise HTTPException(status_code=404, detail="session not found")
    return snapshot.model_dump(mode="json")


@router.delete("/sessions/{session_id}")
async def close_session(request: Request, session_id: UUID) -> dict[str, object]:
    try:
        await _container(request).voice_media.close_session(session_id)
        await _container(request).conversation.cancel(session_id, "session_closing")
        snapshot = await _container(request).sessions.close_session(session_id)
    except KeyError as error:
        raise HTTPException(status_code=404, detail="session not found") from error
    return snapshot.model_dump(mode="json")


@router.post("/sessions/{session_id}/webrtc/offer")
async def create_webrtc_offer(
    request: Request,
    session_id: UUID,
    body: WebRtcOfferRequest,
) -> dict[str, str]:
    container = _container(request)
    session = await container.sessions.get_session(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="session not found")
    if not container.settings.realtime.enabled:
        raise HTTPException(status_code=503, detail="realtime media is disabled")
    try:
        return await container.voice_media.offer(
            session_id,
            sdp=body.sdp,
            type=body.type,
            pc_id=body.pc_id,
            restart_pc=body.restart_pc,
        )
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error


@router.patch("/webrtc/offer")
async def patch_webrtc_offer(request: Request, body: WebRtcPatchRequest) -> dict[str, str]:
    await _container(request).voice_media.patch(
        pc_id=body.pc_id,
        candidates=[
            (item.candidate, item.sdp_mid, item.sdp_mline_index) for item in body.candidates
        ],
    )
    return {"status": "accepted"}


@router.delete("/sessions/{session_id}/webrtc")
async def close_webrtc_session(request: Request, session_id: UUID) -> dict[str, object]:
    closed = await _container(request).voice_media.close_session(session_id)
    return {"session_id": str(session_id), "connections_closed": closed}


@router.get("/sessions/{session_id}/events")
async def read_session_events(
    request: Request,
    session_id: UUID,
    after_sequence: int = Query(default=0, ge=0),
    limit: int = Query(default=100, ge=1, le=500),
) -> dict[str, object]:
    events = await _container(request).event_store.read_stream(
        session_id, after_sequence=after_sequence, limit=limit
    )
    return {"items": events, "count": len(events)}


@router.post("/sessions/{session_id}/turns", status_code=status.HTTP_202_ACCEPTED)
async def submit_text_turn(
    request: Request, session_id: UUID, body: SubmitTextRequest
) -> dict[str, object]:
    try:
        accepted = await _container(request).conversation.submit_text(session_id, body.text)
    except KeyError as error:
        raise HTTPException(status_code=404, detail="session not found") from error
    except (ValueError, RuntimeError) as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    return {
        "session_id": str(accepted.session_id),
        "turn_id": str(accepted.turn_id),
        "generation_id": str(accepted.generation_id),
        "state": accepted.state.value,
    }


@router.post("/sessions/{session_id}/interrupt")
async def interrupt_generation(
    request: Request, session_id: UUID, body: InterruptRequest
) -> dict[str, object]:
    interrupted = await _container(request).conversation.cancel(session_id, body.reason)
    return {"session_id": str(session_id), "interrupted": interrupted}


@router.post("/sessions/{session_id}/reset")
async def reset_session_data(
    request: Request, session_id: UUID, body: ResetSessionRequest
) -> dict[str, object]:
    try:
        result = await _container(request).conversation.reset(session_id)
    except KeyError as error:
        raise HTTPException(status_code=404, detail="session not found") from error
    except RuntimeError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    return {
        "session_id": str(result.session_id),
        "turns_deleted": result.turns_deleted,
        "events_deleted": result.events_deleted,
        "memories_deleted": result.memories_deleted,
        "audio_assets_deleted": result.audio_assets_deleted,
    }


@router.get("/sessions/{session_id}/messages")
async def read_session_messages(
    request: Request,
    session_id: UUID,
    limit: int = Query(default=100, ge=1, le=500),
) -> dict[str, object]:
    session = await _container(request).sessions.get_session(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="session not found")
    messages = await _container(request).conversation.list_messages(session_id, limit)
    return {"items": messages, "count": len(messages)}


@router.get("/audio/{asset_id}.wav", response_class=FileResponse)
async def read_audio_asset(request: Request, asset_id: UUID) -> FileResponse:
    path = _container(request).audio_assets.resolve(asset_id)
    if path is None:
        raise HTTPException(status_code=404, detail="audio asset not found")
    return FileResponse(path, media_type="audio/wav", filename=f"{asset_id}.wav")


@router.get("/characters")
async def read_characters(request: Request) -> dict[str, object]:
    profiles = [
        profile.model_dump(mode="json", exclude={"system_prompt"})
        for profile in _container(request).characters.list()
    ]
    return {"items": profiles, "count": len(profiles)}


@router.get("/memory")
async def read_memory(
    request: Request, include_tombstoned: bool = Query(default=False)
) -> dict[str, object]:
    items = await _container(request).memory.list(include_tombstoned=include_tombstoned)
    serialized = [
        {
            "memory_id": str(item.memory_id),
            "content": item.content,
            "state": item.state,
            "source_session_id": str(item.source_session_id),
            "source_turn_id": str(item.source_turn_id),
            "created_at": item.created_at.isoformat(),
            "updated_at": item.updated_at.isoformat(),
            "tombstoned_at": item.tombstoned_at.isoformat() if item.tombstoned_at else None,
        }
        for item in items
    ]
    return {"items": serialized, "count": len(serialized)}


@router.get("/skills")
async def read_runtime_skills(request: Request) -> dict[str, object]:
    definitions = [
        definition.model_dump(mode="json")
        for definition in _container(request).runtime_skills.list()
    ]
    return {"items": definitions, "count": len(definitions)}


@router.post("/sessions/{session_id}/skills/runtime.status")
async def run_runtime_status_skill(request: Request, session_id: UUID) -> dict[str, object]:
    session = await _container(request).sessions.get_session(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="session not found")
    result = await _container(request).runtime_skills.run_status(session_id)
    return result.model_dump(mode="json")


@router.websocket("/events")
async def runtime_events(websocket: WebSocket) -> None:
    await websocket.accept()
    container: RuntimeContainer = websocket.app.state.container
    requested_session = websocket.query_params.get("session_id")

    def event_filter(event: dict[str, object]) -> bool:
        return requested_session is None or str(event.get("session_id")) == requested_session

    subscription = container.event_hub.subscribe(event_filter)
    await websocket.send_json(
        {
            "schema_version": "1.0",
            "event_type": "system.runtime_started",
            "payload": {"version": __version__},
        }
    )
    try:
        while True:
            event_task = asyncio.create_task(subscription.receive())
            disconnect_task = asyncio.create_task(websocket.receive())
            completed, pending = await asyncio.wait(
                {event_task, disconnect_task}, return_when=asyncio.FIRST_COMPLETED
            )
            for task in pending:
                task.cancel()
            await asyncio.gather(*pending, return_exceptions=True)
            if disconnect_task in completed:
                message = disconnect_task.result()
                if message["type"] == "websocket.disconnect":
                    break
                continue
            await websocket.send_json(event_task.result())
    except (WebSocketDisconnect, asyncio.CancelledError):
        pass
    finally:
        container.event_hub.unsubscribe(subscription)
