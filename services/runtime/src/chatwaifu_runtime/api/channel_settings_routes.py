"""Trusted operator settings, never accessible with an adapter ingress token."""

from chatwaifu_protocol.channel_settings import (
    ChannelRuntimeSettingsResponse,
    ChannelRuntimeSettingsSnapshot,
    ChannelRuntimeSettingsUpdate,
)
from fastapi import APIRouter, HTTPException, Request

from chatwaifu_runtime.bootstrap.container import RuntimeContainer
from chatwaifu_runtime.external_channels.service import ChannelConflictError

router = APIRouter(prefix="/v1/channels/settings", tags=["channels"])


def _response(
    container: RuntimeContainer, saved: ChannelRuntimeSettingsSnapshot | None = None
) -> ChannelRuntimeSettingsResponse:
    return ChannelRuntimeSettingsResponse(
        **(saved or container.channel_settings.get()).model_dump(),
        search_provider=container.settings.public_web.search_provider,
        reader_provider=container.settings.public_web.reader_provider,
        stt_provider=container.stt.kind,
    )


@router.get("", response_model=ChannelRuntimeSettingsResponse)
async def read_settings(request: Request) -> ChannelRuntimeSettingsResponse:
    return _response(request.app.state.container)


@router.put("", response_model=ChannelRuntimeSettingsResponse)
async def update_settings(
    request: Request, body: ChannelRuntimeSettingsUpdate
) -> ChannelRuntimeSettingsResponse:
    container: RuntimeContainer = request.app.state.container
    try:
        saved = await container.channel_settings.update(body)
    except ChannelConflictError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    return _response(container, saved)
