"""Owner-scoped learned sticker library HTTP routes."""

from __future__ import annotations

from typing import cast

from chatwaifu_protocol.sticker_library import (
    StickerLibraryDeleteResult,
    StickerLibrarySettings,
    StickerLibrarySettingsUpdate,
    StickerLibrarySnapshot,
)
from fastapi import APIRouter, HTTPException, Query, Request, Response, status

from chatwaifu_runtime.bootstrap.container import RuntimeContainer
from chatwaifu_runtime.character_kernel.service import USER_SCOPE
from chatwaifu_runtime.media.image import async_extract_static_poster
from chatwaifu_runtime.sticker_library.models import StickerLibraryRevisionConflict

router = APIRouter(prefix="/v1/sticker-library", tags=["sticker-library"])


def _container(request: Request) -> RuntimeContainer:
    return cast(RuntimeContainer, request.app.state.container)


def _assert_supported_character(character_id: str) -> None:
    if character_id != "default":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Sticker library is currently only supported for the 'default' character.",
        )


@router.get("", response_model=StickerLibrarySnapshot)
@router.get("/", response_model=StickerLibrarySnapshot, include_in_schema=False)
async def get_sticker_library_snapshot(
    request: Request,
    character_id: str = Query(default="default"),
) -> StickerLibrarySnapshot:
    _assert_supported_character(character_id)
    container = _container(request)
    return await container.sticker_repository.snapshot(USER_SCOPE, character_id)


@router.put("/settings", response_model=StickerLibrarySettings)
async def update_sticker_library_settings(
    request: Request,
    payload: StickerLibrarySettingsUpdate,
    character_id: str = Query(default="default"),
) -> StickerLibrarySettings:
    _assert_supported_character(character_id)
    container = _container(request)
    try:
        return await container.sticker_repository.update_settings(
            USER_SCOPE,
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
) -> StickerLibraryDeleteResult:
    _assert_supported_character(character_id)
    container = _container(request)
    return await container.sticker_repository.delete(USER_SCOPE, character_id, sticker_id)


@router.get("/{sticker_id}/image")
async def get_learned_sticker_image(
    request: Request,
    sticker_id: str,
    character_id: str = Query(default="default"),
    poster: bool = Query(default=False),
) -> Response:
    _assert_supported_character(character_id)
    container = _container(request)
    asset = await container.sticker_repository.get_asset(USER_SCOPE, character_id, sticker_id)
    if asset is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Sticker '{sticker_id}' not found.",
        )
    data, mime_type, is_animated = asset
    if poster and is_animated:
        poster_data = await async_extract_static_poster(data, mime_type)
        return Response(
            content=poster_data,
            media_type="image/png",
            headers={
                "Cache-Control": "no-store",
                "X-Content-Type-Options": "nosniff",
            },
        )
    return Response(
        content=data,
        media_type=mime_type,
        headers={
            "Cache-Control": "no-store",
            "X-Content-Type-Options": "nosniff",
        },
    )
