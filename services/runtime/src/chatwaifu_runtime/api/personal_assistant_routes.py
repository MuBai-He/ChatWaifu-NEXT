"""Authenticated assistant status and direct-TLS-only OAuth handoff."""

from dataclasses import asdict
from datetime import datetime, timedelta
from typing import Literal
from urllib.parse import urlsplit
from uuid import UUID

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field, SecretStr

from chatwaifu_runtime.bootstrap.container import RuntimeContainer
from chatwaifu_runtime.personal_assistant.accounts import GoogleAccountService
from chatwaifu_runtime.personal_assistant.google_calendar import GoogleCalendarError
from chatwaifu_runtime.personal_assistant.oauth import GoogleOAuthCoordinator
from chatwaifu_runtime.personal_assistant.repository import AssistantAccessError

router = APIRouter(prefix="/v1/personal-assistant", tags=["personal-assistant"])


class AssistantStatusResponse(BaseModel):
    schema_version: Literal["1.0"] = "1.0"
    state: Literal["disabled", "unconfigured", "ready", "cleanup_failed"]
    authorization_available: bool = False


class AccountStatusResponse(BaseModel):
    account_id: str
    status: str


class OAuthBeginRequest(BaseModel):
    session_id: UUID
    redirect_uri: str = Field(max_length=512)


class OAuthFlowRequest(BaseModel):
    session_id: UUID
    state: str = Field(min_length=32, max_length=256)


class OAuthCompleteRequest(OAuthFlowRequest):
    code: SecretStr = Field(min_length=1, max_length=4096)


class OAuthBeginResponse(BaseModel):
    schema_version: Literal["1.0"] = "1.0"
    state: str
    authorization_url: str
    expires_in: int


def _protected(request: Request, container: RuntimeContainer) -> bool:
    origin = container.settings.personal_assistant.google_oauth_https_origin
    # Initial admission is direct end-to-end TLS only. Reject raw proxy headers
    # even if Uvicorn has already interpreted one into scope['scheme'].
    expected = urlsplit(origin) if origin else None
    actual = request.url
    same_origin = expected is not None and (actual.hostname, actual.port or 443) == (
        expected.hostname,
        expected.port or 443,
    )
    return bool(
        origin
        and request.url.scheme == "https"
        and same_origin
        and not any(
            name.lower() == "forwarded" or name.lower().startswith("x-forwarded-")
            for name in request.headers
        )
    )


def _oauth(request: Request) -> GoogleOAuthCoordinator:
    container: RuntimeContainer = request.app.state.container
    if not _protected(request, container):
        raise HTTPException(403, "oauth_requires_configured_direct_https")
    if container.personal_assistant.oauth is None:
        raise HTTPException(409, "personal_assistant_not_configured")
    return container.personal_assistant.oauth


@router.get("/status", response_model=AssistantStatusResponse)
async def assistant_status(request: Request) -> AssistantStatusResponse:
    container: RuntimeContainer = request.app.state.container
    return AssistantStatusResponse(
        state=container.personal_assistant.state,
        authorization_available=bool(
            container.personal_assistant.oauth and _protected(request, container)
        ),
    )


@router.post("/oauth/begin", response_model=OAuthBeginResponse)
async def oauth_begin(request: Request, body: OAuthBeginRequest) -> OAuthBeginResponse:
    coordinator = _oauth(request)
    try:
        flow = await coordinator.begin(str(body.session_id), body.redirect_uri)
    except AssistantAccessError as error:
        raise HTTPException(403, str(error)) from None
    return OAuthBeginResponse(
        state=flow.state, authorization_url=flow.authorization_url, expires_in=flow.expires_in
    )


@router.post("/oauth/complete", response_model=AccountStatusResponse)
async def oauth_complete(request: Request, body: OAuthCompleteRequest) -> AccountStatusResponse:
    coordinator = _oauth(request)
    try:
        account = await coordinator.complete(
            str(body.session_id), body.state, body.code.get_secret_value()
        )
    except AssistantAccessError as error:
        raise HTTPException(403, str(error)) from None
    except GoogleCalendarError as error:
        raise HTTPException(502, error.code) from None
    return AccountStatusResponse(account_id=account.account_id, status=account.status)


@router.post("/oauth/cancel")
async def oauth_cancel(request: Request, body: OAuthFlowRequest) -> dict[str, bool]:
    coordinator = _oauth(request)
    try:
        await coordinator.cancel(str(body.session_id), body.state)
    except AssistantAccessError:
        raise HTTPException(403, "personal_account_requires_owner") from None
    return {"cancelled": True}


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


class CalendarRequest(BaseModel):
    session_id: UUID
    account_id: UUID
    calendar_id: str = Field(min_length=1, max_length=2048)
    selected: bool


def _accounts_service(request: Request) -> GoogleAccountService:
    container: RuntimeContainer = request.app.state.container
    service = container.personal_assistant.accounts
    if service is None:
        raise HTTPException(409, "personal_assistant_not_configured")
    return service


@router.get("/calendars")
async def calendars(request: Request, session_id: UUID, account_id: UUID, discover: bool = False):
    service = _accounts_service(request)
    try:
        if discover:
            await service.discover(str(session_id), str(account_id))
        return {
            "items": [
                asdict(item) for item in await service.calendars(str(session_id), str(account_id))
            ]
        }
    except AssistantAccessError as error:
        raise HTTPException(403, str(error)) from None
    except GoogleCalendarError as error:
        raise HTTPException(502, error.code) from None


@router.put("/calendars/selection")
async def select_calendar(request: Request, body: CalendarRequest):
    try:
        await _accounts_service(request).select(
            str(body.session_id), str(body.account_id), body.calendar_id, body.selected
        )
    except AssistantAccessError as error:
        raise HTTPException(403, str(error)) from None
    return {"selected": body.selected}


@router.get("/events")
async def query_events(
    request: Request,
    session_id: UUID,
    account_id: UUID,
    calendar_id: str,
    start: datetime,
    end: datetime,
):
    if (
        start.utcoffset() is None
        or end.utcoffset() is None
        or not timedelta(0) < end - start <= timedelta(days=31)
    ):
        raise HTTPException(422, "query_requires_timezone_and_maximum_31_days")
    try:
        events = await _accounts_service(request).query(
            str(session_id), str(account_id), calendar_id, start, end
        )
        return {"items": [asdict(event) for event in events], "source": "google_live"}
    except AssistantAccessError as error:
        raise HTTPException(403, str(error)) from None
    except GoogleCalendarError as error:
        raise HTTPException(502, error.code) from None
