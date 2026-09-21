"""Authenticated assistant status; no OAuth code/token HTTP handoff yet."""

from typing import Literal
from uuid import UUID

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from chatwaifu_runtime.bootstrap.container import RuntimeContainer
from chatwaifu_runtime.personal_assistant.repository import AssistantAccessError

router = APIRouter(prefix="/v1/personal-assistant", tags=["personal-assistant"])


class AssistantStatusResponse(BaseModel):
    schema_version: Literal["1.0"] = "1.0"
    state: Literal["disabled", "unconfigured", "ready", "cleanup_failed"]
    authorization_available: bool = False


class AccountStatusResponse(BaseModel):
    account_id: str
    status: str


@router.get("/status", response_model=AssistantStatusResponse)
async def assistant_status(request: Request) -> AssistantStatusResponse:
    container: RuntimeContainer = request.app.state.container
    return AssistantStatusResponse(state=container.personal_assistant.state)


@router.get("/accounts", response_model=list[AccountStatusResponse])
async def assistant_accounts(request: Request, session_id: UUID) -> list[AccountStatusResponse]:
    container: RuntimeContainer = request.app.state.container
    service = container.personal_assistant.accounts
    if service is None:
        raise HTTPException(409, "personal_assistant_not_configured")
    try:
        accounts = await service.status(str(session_id))
    except AssistantAccessError:
        raise HTTPException(403, "personal_account_requires_owner") from None
    return [AccountStatusResponse(account_id=a.account_id, status=a.status) for a in accounts]
