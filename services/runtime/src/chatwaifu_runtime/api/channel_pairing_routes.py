"""Authenticated local pairing; transport credentials never appear in snapshots."""

from typing import cast
from uuid import UUID

from chatwaifu_protocol.channels import ChannelPairingSnapshot, ChannelPairingStartRequest
from fastapi import APIRouter, HTTPException, Query, Request

from chatwaifu_runtime.bootstrap.container import RuntimeContainer

router = APIRouter(prefix="/v1/channel-pairing-sessions", tags=["channels"])


def _container(request: Request) -> RuntimeContainer:
    return cast(RuntimeContainer, request.app.state.container)


@router.post("", response_model=ChannelPairingSnapshot)
async def begin_pairing(
    request: Request, body: ChannelPairingStartRequest
) -> ChannelPairingSnapshot:
    try:
        return await _container(request).qq_channels.begin_pairing(body)
    except ValueError as error:
        raise HTTPException(400, detail=str(error)) from error


@router.get("/{pairing_id}", response_model=ChannelPairingSnapshot)
async def read_pairing(
    request: Request, pairing_id: UUID, wait_seconds: float = Query(default=0, ge=0, le=25)
) -> ChannelPairingSnapshot:
    try:
        return await _container(request).qq_channels.pairing(pairing_id, wait_seconds)
    except KeyError as error:
        raise HTTPException(404, detail="Pairing not found") from error


@router.delete("/{pairing_id}")
async def cancel_pairing(request: Request, pairing_id: UUID) -> dict[str, object]:
    try:
        await _container(request).qq_channels.cancel_pairing(pairing_id)
    except KeyError as error:
        raise HTTPException(404, detail="Pairing not found") from error
    return {"removed": True, "pairing_id": str(pairing_id)}
