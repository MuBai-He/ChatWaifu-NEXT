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
from chatwaifu_runtime.character_kernel.service import USER_SCOPE
from chatwaifu_runtime.photo_memory.models import PhotoMemoryRevisionConflict

router = APIRouter(prefix="/v1/photo-memory", tags=["photo-memory"])


def _container(request: Request, character_id: str) -> RuntimeContainer:
    if character_id != "default":
        raise HTTPException(400, "Photo memory is currently supported for the default character.")
    return cast(RuntimeContainer, request.app.state.container)


@router.get("", response_model=PhotoMemorySnapshot)
@router.get("/", response_model=PhotoMemorySnapshot, include_in_schema=False)
async def snapshot(
    request: Request, character_id: str = Query(default="default")
) -> PhotoMemorySnapshot:
    container = _container(request, character_id)
    return await container.photo_repository.snapshot(USER_SCOPE, character_id)


@router.put("/settings", response_model=PhotoMemorySettings)
async def settings(
    request: Request,
    payload: PhotoMemorySettingsUpdate,
    character_id: str = Query(default="default"),
) -> PhotoMemorySettings:
    container = _container(request, character_id)
    try:
        return await container.photo_repository.update_settings(
            USER_SCOPE,
            character_id,
            retention_enabled=payload.retention_enabled,
            expected_revision=payload.expected_revision,
        )
    except PhotoMemoryRevisionConflict as error:
        raise HTTPException(409, str(error)) from error


@router.get("/{photo_id}/image")
async def image(
    request: Request, photo_id: UUID, character_id: str = Query(default="default")
) -> Response:
    container = _container(request, character_id)
    asset = await container.photo_repository.get_image(USER_SCOPE, character_id, photo_id)
    if asset is None:
        raise HTTPException(404, "Photo not found.")
    return Response(
        content=asset.data,
        media_type=asset.mime_type,
        headers={"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"},
    )


@router.delete("/{photo_id}", response_model=PhotoMemoryDeleteResult)
async def delete(
    request: Request, photo_id: UUID, character_id: str = Query(default="default")
) -> PhotoMemoryDeleteResult:
    container = _container(request, character_id)
    deletion = await container.photo_repository.delete(USER_SCOPE, character_id, photo_id)
    for affected in deletion.affected_generations:
        await container.conversation.cancel(
            affected.session_id,
            "photo_deleted",
            expected_generation_id=affected.generation_id,
        )
    return deletion.result
