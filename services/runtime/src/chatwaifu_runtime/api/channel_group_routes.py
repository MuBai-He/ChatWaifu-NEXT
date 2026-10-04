"""Operator management of observed audiences and fixed default-off QQ group routes.

These paths are ordinary operator-authenticated endpoints, never channel-token
ingress or a public way to submit trusted group identity or send a message.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, NoReturn, cast
from uuid import UUID

from chatwaifu_protocol.channel_groups import (
    ChannelGroupAudienceRequest,
    ChannelGroupAudienceSnapshot,
    ChannelGroupRouteCreate,
    ChannelGroupRoutePage,
    ChannelGroupRouteSnapshot,
    ChannelGroupRouteUpdate,
    ChannelGroupTurnCancelRequest,
    ChannelGroupTurnPage,
    ChannelGroupTurnSnapshot,
    ChannelParticipantLinkCreate,
    ChannelParticipantLinkPage,
    ChannelParticipantLinkSnapshot,
    ChannelParticipantLinkUpdate,
)
from chatwaifu_protocol.channels import ChannelErrorResponse
from chatwaifu_protocol.errors import StructuredError
from fastapi import APIRouter, HTTPException, Query, Request

from chatwaifu_runtime.external_channels.service import ExternalChannelError

if TYPE_CHECKING:
    from chatwaifu_runtime.external_channels.groups import ChannelGroupService

router = APIRouter(prefix="/v1/channel-connections", tags=["channels"])


def _service(request: Request) -> ChannelGroupService:
    return cast("ChannelGroupService", request.app.state.container.channel_groups)


def _raise_error(error: ExternalChannelError) -> NoReturn:
    response = ChannelErrorResponse(
        error=StructuredError(
            code=error.code,
            message=str(error),
            retryable=error.retryable,
            component="channel_groups",
        ),
        retry_after_ms=500 if error.retryable else None,
    )
    raise HTTPException(
        status_code=error.http_status, detail=response.model_dump(mode="json")
    ) from error


@router.post(
    "/{connection_id}/group-audience-observations", response_model=ChannelGroupAudienceSnapshot
)
async def observe_audience(
    request: Request, connection_id: UUID, body: ChannelGroupAudienceRequest
) -> ChannelGroupAudienceSnapshot:
    try:
        return await _service(request).observe_audience(connection_id, body)
    except ExternalChannelError as error:
        _raise_error(error)


@router.get("/{connection_id}/participant-links", response_model=ChannelParticipantLinkPage)
async def read_links(
    request: Request,
    connection_id: UUID,
    limit: int = Query(default=25, ge=1, le=50),
    cursor: str | None = Query(default=None, min_length=1, max_length=256),
) -> ChannelParticipantLinkPage:
    try:
        return await _service(request).list_links(connection_id, limit=limit, cursor=cursor)
    except ExternalChannelError as error:
        _raise_error(error)


@router.post("/{connection_id}/participant-links", response_model=ChannelParticipantLinkSnapshot)
async def create_link(
    request: Request, connection_id: UUID, body: ChannelParticipantLinkCreate
) -> ChannelParticipantLinkSnapshot:
    try:
        return await _service(request).create_link(connection_id, body)
    except ExternalChannelError as error:
        _raise_error(error)


@router.put(
    "/{connection_id}/participant-links/{link_id}", response_model=ChannelParticipantLinkSnapshot
)
async def update_link(
    request: Request, connection_id: UUID, link_id: UUID, body: ChannelParticipantLinkUpdate
) -> ChannelParticipantLinkSnapshot:
    try:
        return await _service(request).update_link(connection_id, link_id, body)
    except ExternalChannelError as error:
        _raise_error(error)


@router.get("/{connection_id}/group-routes", response_model=ChannelGroupRoutePage)
async def read_routes(
    request: Request,
    connection_id: UUID,
    limit: int = Query(default=25, ge=1, le=50),
    cursor: str | None = Query(default=None, min_length=1, max_length=256),
) -> ChannelGroupRoutePage:
    try:
        return await _service(request).list_routes(connection_id, limit=limit, cursor=cursor)
    except ExternalChannelError as error:
        _raise_error(error)


@router.post("/{connection_id}/group-routes", response_model=ChannelGroupRouteSnapshot)
async def create_route(
    request: Request, connection_id: UUID, body: ChannelGroupRouteCreate
) -> ChannelGroupRouteSnapshot:
    try:
        return await _service(request).create_route(connection_id, body)
    except ExternalChannelError as error:
        _raise_error(error)


@router.put("/{connection_id}/group-routes/{route_id}", response_model=ChannelGroupRouteSnapshot)
async def update_route(
    request: Request, connection_id: UUID, route_id: UUID, body: ChannelGroupRouteUpdate
) -> ChannelGroupRouteSnapshot:
    try:
        return await _service(request).update_route(connection_id, route_id, body)
    except ExternalChannelError as error:
        _raise_error(error)


@router.get("/{connection_id}/group-routes/{route_id}/turns", response_model=ChannelGroupTurnPage)
async def read_turns(
    request: Request,
    connection_id: UUID,
    route_id: UUID,
    limit: int = Query(default=25, ge=1, le=50),
    cursor: str | None = Query(default=None, min_length=1, max_length=256),
) -> ChannelGroupTurnPage:
    try:
        return await _service(request).list_turns(
            connection_id, route_id, limit=limit, cursor=cursor
        )
    except ExternalChannelError as error:
        _raise_error(error)


@router.post(
    "/{connection_id}/group-routes/{route_id}/turns/{channel_turn_id}/cancel",
    response_model=ChannelGroupTurnSnapshot,
)
async def cancel_turn(
    request: Request,
    connection_id: UUID,
    route_id: UUID,
    channel_turn_id: UUID,
    body: ChannelGroupTurnCancelRequest,
) -> ChannelGroupTurnSnapshot:
    try:
        return await _service(request).cancel_turn(connection_id, route_id, channel_turn_id, body)
    except ExternalChannelError as error:
        _raise_error(error)
