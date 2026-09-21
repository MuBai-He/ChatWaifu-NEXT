"""Authenticated owner photo retention, previews and deletion."""

from typing import cast
from uuid import UUID

from chatwaifu_protocol.photo_memory import (
    PhotoMemoryDeleteResult,
    PhotoMemorySettings,
    PhotoMemorySettingsUpdate,
    PhotoMemorySnapshot,
)
from fastapi import APIRouter, HTTPException, Query, Request, Response

from chatwaifu_runtime.bootstrap.container import RuntimeContainer
from chatwaifu_runtime.photo_memory.models import PhotoMemoryRevisionConflict

router = APIRouter(prefix="/v1/photo-memory", tags=["photo-memory"])


def _container(request: Request, character_id: str) -> RuntimeContainer:
    if character_id != "default":
        raise HTTPException(400, "Photo memory is currently supported for the default character.")
    return cast(RuntimeContainer, request.app.state.container)


async def _scope(container: RuntimeContainer, character_id: str, session_id: UUID | None) -> str:
    if session_id is None:
        return "local"
    session = await container.sessions.get_session(session_id)
    if session is None or session.character_id != character_id:
        raise HTTPException(404, "Session not found.")
    return session.user_scope


@router.get("", response_model=PhotoMemorySnapshot)
@router.get("/", response_model=PhotoMemorySnapshot, include_in_schema=False)
async def snapshot(
    request: Request, character_id: str = Query(default="default"), session_id: UUID | None = None
) -> PhotoMemorySnapshot:
    container = _container(request, character_id)
    scope = await _scope(container, character_id, session_id)
    return await container.photo_repository.snapshot(scope, character_id)


@router.put("/settings", response_model=PhotoMemorySettings)
async def settings(
    request: Request,
    payload: PhotoMemorySettingsUpdate,
    character_id: str = Query(default="default"),
    session_id: UUID | None = None,
) -> PhotoMemorySettings:
    container = _container(request, character_id)
    scope = await _scope(container, character_id, session_id)
    try:
        return await container.photo_repository.update_settings(
            scope,
            character_id,
            retention_enabled=payload.retention_enabled,
            expected_revision=payload.expected_revision,
        )
    except PhotoMemoryRevisionConflict as error:
        raise HTTPException(409, str(error)) from error


@router.get("/{photo_id}/image")
async def image(
    request: Request,
    photo_id: UUID,
    character_id: str = Query(default="default"),
    session_id: UUID | None = None,
) -> Response:
    container = _container(request, character_id)
    scope = await _scope(container, character_id, session_id)
    asset = await container.photo_repository.get_image(scope, character_id, photo_id)
    if asset is None:
        raise HTTPException(404, "Photo not found.")
    return Response(
        content=asset.data,
        media_type=asset.mime_type,
        headers={"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"},
    )


@router.delete("/{photo_id}", response_model=PhotoMemoryDeleteResult)
async def delete(
    request: Request,
    photo_id: UUID,
    character_id: str = Query(default="default"),
    session_id: UUID | None = None,
) -> PhotoMemoryDeleteResult:
    container = _container(request, character_id)
    scope = await _scope(container, character_id, session_id)
    deletion = await container.photo_repository.delete(scope, character_id, photo_id)
    for affected in deletion.affected_generations:
        await container.conversation.cancel(
            affected.session_id,
            "photo_deleted",
            expected_generation_id=affected.generation_id,
        )
    return deletion.result
