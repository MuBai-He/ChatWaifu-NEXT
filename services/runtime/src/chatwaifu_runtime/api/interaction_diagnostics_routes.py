"""Bounded owner-only interaction trace reads."""

from uuid import UUID

from chatwaifu_protocol.diagnostics import InteractionTraceDetail, InteractionTracePage
from fastapi import APIRouter, HTTPException, Query, Request

from chatwaifu_runtime.bootstrap.container import RuntimeContainer

router = APIRouter(prefix="/v1/sessions/{session_id}/interactions", tags=["diagnostics"])


def _container(request: Request) -> RuntimeContainer:
    return request.app.state.container


async def _existing_session(request: Request, session_id: UUID) -> RuntimeContainer:
    container = _container(request)
    if await container.sessions.get_session(session_id) is None:
        raise HTTPException(status_code=404, detail="session not found")
    return container


@router.get("", response_model=InteractionTracePage)
async def list_interactions(
    request: Request,
    session_id: UUID,
    cursor: str | None = Query(default=None, max_length=128),
    limit: int = Query(default=50, ge=1, le=50),
    include_nonparticipation: bool = Query(default=False),
) -> InteractionTracePage:
    container = await _existing_session(request, session_id)
    try:
        return await container.interaction_diagnostics.list_interactions(
            session_id,
            cursor=cursor,
            limit=limit,
            include_nonparticipation=include_nonparticipation,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail="invalid interaction cursor") from exc


@router.get("/{interaction_id}", response_model=InteractionTraceDetail)
async def read_interaction(
    request: Request,
    session_id: UUID,
    interaction_id: UUID,
    after_sequence: int = Query(default=0, ge=0),
    limit: int = Query(default=200, ge=1, le=200),
) -> InteractionTraceDetail:
    container = await _existing_session(request, session_id)
    visible_namespaces = await container.memory.namespaces_for_session(session_id)
    result = await container.interaction_diagnostics.read_interaction(
        session_id,
        interaction_id,
        visible_namespaces=visible_namespaces,
        after_sequence=after_sequence,
        limit=limit,
    )
    if result is None:
        raise HTTPException(status_code=404, detail="interaction not found")
    return result
