"""Loopback Runtime HTTP and WebSocket routes."""

from uuid import UUID

from fastapi import APIRouter, HTTPException, Query, Request, WebSocket, WebSocketDisconnect, status

from chatwaifu_runtime import __version__
from chatwaifu_runtime.api.models import CreateSessionRequest, RuntimeHealth
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
    )


@router.get("/runtime/version")
async def runtime_version() -> dict[str, str]:
    return {"name": "chatwaifu-runtime", "version": __version__, "protocol": "1.0"}


@router.get("/config")
async def runtime_config(request: Request) -> dict[str, object]:
    return _container(request).settings.public_dict()


@router.post("/sessions", status_code=status.HTTP_201_CREATED)
async def create_session(request: Request, body: CreateSessionRequest) -> dict[str, object]:
    snapshot = await _container(request).sessions.create_session(body.character_id)
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
        snapshot = await _container(request).sessions.close_session(session_id)
    except KeyError as error:
        raise HTTPException(status_code=404, detail="session not found") from error
    return snapshot.model_dump(mode="json")


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
            await websocket.send_json(await subscription.receive())
    except WebSocketDisconnect:
        pass
    finally:
        container.event_hub.unsubscribe(subscription)
