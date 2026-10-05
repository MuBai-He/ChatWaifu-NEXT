"""Owner-scoped learned sticker library HTTP routes."""

from __future__ import annotations

import asyncio
from typing import Annotated, cast
from uuid import UUID

from chatwaifu_protocol.sticker_library import (
    StickerLibraryDeleteResult,
    StickerLibrarySettings,
    StickerLibrarySettingsUpdate,
    StickerLibrarySnapshot,
    StickerUsageHistory,
)
from fastapi import APIRouter, HTTPException, Query, Request, Response, status

from chatwaifu_runtime.bootstrap.container import RuntimeContainer
from chatwaifu_runtime.character_kernel.service import USER_SCOPE
from chatwaifu_runtime.external_channels.service import ChannelNotFoundError
from chatwaifu_runtime.sticker_library.models import StickerLibraryRevisionConflict
from chatwaifu_runtime.sticker_library.usage import StickerUsagePreset

router = APIRouter(prefix="/v1/sticker-library", tags=["sticker-library"])


def _container(request: Request) -> RuntimeContainer:
    return cast(RuntimeContainer, request.app.state.container)


def _assert_supported_character(character_id: str) -> None:
    if character_id != "default":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Sticker library is currently only supported for the 'default' character.",
        )


async def _library_scope(
    container: RuntimeContainer,
    group_route_id: UUID | None,
    group_scene_id: str | None,
    *,
    require_scene: bool = False,
) -> str:
    if group_route_id is None:
        if group_scene_id is not None:
            raise HTTPException(status_code=400, detail="A group scene requires a group route.")
        return USER_SCOPE
    try:
        scope = await container.channel_groups.sticker_library_scope(group_route_id)
    except ChannelNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    if require_scene and group_scene_id is None:
        raise HTTPException(status_code=400, detail="Group mutations require the observed scene.")
    if group_scene_id is not None and scope != f"scene:{group_scene_id}":
        raise HTTPException(status_code=409, detail="Group scene changed; reload group management.")
    return scope


@router.get("", response_model=StickerLibrarySnapshot)
@router.get("/", response_model=StickerLibrarySnapshot, include_in_schema=False)
async def get_sticker_library_snapshot(
    request: Request,
    character_id: str = Query(default="default"),
    group_route_id: UUID | None = None,
    group_scene_id: Annotated[str | None, Query(max_length=128)] = None,
) -> StickerLibrarySnapshot:
    _assert_supported_character(character_id)
    container = _container(request)
    scope = await _library_scope(container, group_route_id, group_scene_id)
    return await container.sticker_repository.snapshot(scope, character_id)


@router.put("/settings", response_model=StickerLibrarySettings)
async def update_sticker_library_settings(
    request: Request,
    payload: StickerLibrarySettingsUpdate,
    character_id: str = Query(default="default"),
    group_route_id: UUID | None = None,
    group_scene_id: Annotated[str | None, Query(max_length=128)] = None,
) -> StickerLibrarySettings:
    _assert_supported_character(character_id)
    container = _container(request)
    scope = await _library_scope(container, group_route_id, group_scene_id, require_scene=True)
    try:
        return await container.sticker_repository.update_settings(
            scope,
            character_id,
            learning_enabled=payload.learning_enabled,
            expected_revision=payload.expected_revision,
        )
    except StickerLibraryRevisionConflict as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(exc),
        ) from exc


@router.delete("/{sticker_id}", response_model=StickerLibraryDeleteResult)
async def delete_learned_sticker(
    request: Request,
    sticker_id: str,
    character_id: str = Query(default="default"),
    group_route_id: UUID | None = None,
    group_scene_id: Annotated[str | None, Query(max_length=128)] = None,
) -> StickerLibraryDeleteResult:
    _assert_supported_character(character_id)
    container = _container(request)
    scope = await _library_scope(container, group_route_id, group_scene_id, require_scene=True)
    return await container.sticker_repository.delete(scope, character_id, sticker_id)


@router.get("/{sticker_id}/image")
async def get_learned_sticker_image(
    request: Request,
    sticker_id: str,
    character_id: str = Query(default="default"),
    group_route_id: UUID | None = None,
    group_scene_id: Annotated[str | None, Query(max_length=128)] = None,
) -> Response:
    _assert_supported_character(character_id)
    container = _container(request)
    scope = await _library_scope(container, group_route_id, group_scene_id)
    data = await container.sticker_repository.get_image(scope, character_id, sticker_id)
    if data is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Sticker '{sticker_id}' not found.",
        )
    return Response(
        content=data,
        media_type="image/png",
        headers={
            "Cache-Control": "no-store",
            "X-Content-Type-Options": "nosniff",
        },
    )


@router.get("/usage", response_model=StickerUsageHistory)
async def get_sticker_usage(
    request: Request,
    response: Response,
    character_id: str = Query(default="default"),
    limit: int = Query(default=50, ge=1, le=50),
    group_route_id: UUID | None = None,
    group_scene_id: Annotated[str | None, Query(max_length=128)] = None,
) -> StickerUsageHistory:
    _assert_supported_character(character_id)
    container = _container(request)
    # Local manifest I/O stays off the event loop. No image bytes are loaded.
    entries = await asyncio.to_thread(container.sticker_catalog.load_manifest)
    labels = {"kitten_happy": "开心小猫", "kitten_shy": "害羞小猫", "kitten_comfort": "安慰小猫"}
    presets = {
        entry.sticker_id: StickerUsagePreset(
            sha256=entry.sha256, label=labels.get(entry.sticker_id, "预设表情")
        )
        for entry in entries
    }
    response.headers["Cache-Control"] = "no-store"
    scope = await _library_scope(container, group_route_id, group_scene_id)
    return await container.sticker_usage.history(scope, character_id, presets, limit=limit)
