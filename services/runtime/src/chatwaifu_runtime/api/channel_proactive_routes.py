"""Operator-only fixed-owner proactive policy; preview never sends a message."""

from __future__ import annotations

from typing import TYPE_CHECKING, NoReturn, cast
from uuid import UUID

from chatwaifu_protocol.channel_proactive import (
    ChannelOutboundIntentCancelRequest,
    ChannelOutboundIntentPage,
    ChannelOutboundIntentSnapshot,
    ChannelProactivePolicySnapshot,
    ChannelProactivePolicyUpdate,
    ChannelProactivePreview,
)
from chatwaifu_protocol.channels import ChannelErrorResponse
from chatwaifu_protocol.errors import StructuredError
from fastapi import APIRouter, HTTPException, Query, Request

from chatwaifu_runtime.external_channels.service import ExternalChannelError

if TYPE_CHECKING:
    from chatwaifu_runtime.external_channels.proactive import ChannelProactiveService

router = APIRouter(prefix="/v1/channel-connections", tags=["channels"])


def _service(request: Request) -> ChannelProactiveService:
    return cast("ChannelProactiveService", request.app.state.container.channel_proactive)


def _raise_error(error: ExternalChannelError) -> NoReturn:
    response = ChannelErrorResponse(
        error=StructuredError(
            code=error.code,
            message=str(error),
            retryable=error.retryable,
            component="channel_proactive",
        ),
        retry_after_ms=500 if error.retryable else None,
    )
    raise HTTPException(
        status_code=error.http_status, detail=response.model_dump(mode="json")
    ) from error


@router.get("/{connection_id}/proactive-policy", response_model=ChannelProactivePolicySnapshot)
async def read_policy(request: Request, connection_id: UUID) -> ChannelProactivePolicySnapshot:
    try:
        return await _service(request).get_policy(connection_id)
    except ExternalChannelError as error:
        _raise_error(error)


@router.put("/{connection_id}/proactive-policy", response_model=ChannelProactivePolicySnapshot)
async def update_policy(
    request: Request, connection_id: UUID, body: ChannelProactivePolicyUpdate
) -> ChannelProactivePolicySnapshot:
    try:
        return await _service(request).update_policy(connection_id, body)
    except ExternalChannelError as error:
        _raise_error(error)


@router.post("/{connection_id}/proactive-preview", response_model=ChannelProactivePreview)
async def preview_policy(request: Request, connection_id: UUID) -> ChannelProactivePreview:
    try:
        return await _service(request).preview(connection_id)
    except ExternalChannelError as error:
        _raise_error(error)


@router.get("/{connection_id}/outbound-intents", response_model=ChannelOutboundIntentPage)
async def read_intents(
    request: Request,
    connection_id: UUID,
    limit: int = Query(default=25, ge=1, le=50),
    cursor: str | None = Query(default=None, min_length=1, max_length=256),
) -> ChannelOutboundIntentPage:
    try:
        return await _service(request).list_intents(connection_id, limit=limit, cursor=cursor)
    except ExternalChannelError as error:
        _raise_error(error)


@router.post(
    "/{connection_id}/outbound-intents/{request_id}/cancel",
    response_model=ChannelOutboundIntentSnapshot,
)
async def cancel_intent(
    request: Request,
    connection_id: UUID,
    request_id: UUID,
    body: ChannelOutboundIntentCancelRequest,
) -> ChannelOutboundIntentSnapshot:
    try:
        return await _service(request).cancel_intent(connection_id, request_id, body)
    except ExternalChannelError as error:
        _raise_error(error)
