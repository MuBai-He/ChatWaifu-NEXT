"""Authenticated assistant status and direct-TLS-only OAuth handoff."""

import json
from dataclasses import asdict
from datetime import UTC, datetime, timedelta
from typing import Literal
from urllib.parse import urlsplit
from uuid import UUID

from chatwaifu_protocol.base import JsonObject
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field, SecretStr

from chatwaifu_runtime.bootstrap.container import RuntimeContainer
from chatwaifu_runtime.personal_assistant.accounts import GoogleAccountService
from chatwaifu_runtime.personal_assistant.google_calendar import GoogleCalendarError
from chatwaifu_runtime.personal_assistant.oauth import GoogleOAuthCoordinator
from chatwaifu_runtime.personal_assistant.repository import AssistantAccessError
from chatwaifu_runtime.personal_assistant.tasks import AppleOperation, TaskInput, TaskService

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


# Task/device APIs share Runtime authentication. Device secrets are an additional,
# revocable capability, not a replacement for the Runtime bearer token.


class OwnerRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    session_id: UUID


class PairRequest(OwnerRequest):
    name: str = Field(min_length=1, max_length=80)


class SourceSelection(BaseModel):
    id: str = Field(min_length=1, max_length=512)
    title: str = Field(max_length=200)
    resource: Literal["calendar", "reminder"]
    writable: bool


class DeviceRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    device_id: UUID
    secret: SecretStr = Field(min_length=32, max_length=128)


class DevicePoll(DeviceRequest):
    source_revision: int = Field(default=0, ge=0)
    sources: list[SourceSelection] = Field(
        default_factory=lambda: list[SourceSelection](), max_length=50
    )


class DeviceAck(DeviceRequest):
    item_id: UUID
    action: Literal["presented", "stop", "snooze", "result"]
    result: JsonObject = Field(default_factory=lambda: dict())


class CreateTaskRequest(OwnerRequest):
    task: TaskInput


class ReviseTaskRequest(CreateTaskRequest):
    expected_revision: int = Field(ge=0)


class ChangeTaskRequest(OwnerRequest):
    action: Literal["pause", "resume", "cancel"]


class CreateOperationRequest(OwnerRequest):
    operation: AppleOperation


def _task_service(request: Request) -> TaskService:
    service = request.app.state.container.personal_assistant.tasks
    if service is None:
        raise HTTPException(409, "personal_assistant_disabled")
    return service


def _device_transport(request: Request) -> None:
    # Uvicorn disables proxy_headers. Never trust a proxy's loopback peer alone.
    forwarded = any(
        k in ("forwarded", "x-real-ip") or k.startswith("x-forwarded-") for k in request.headers
    )
    loopback = (
        request.app.state.container.settings.personal_assistant.device_allow_direct_loopback
        and request.client is not None
        and request.client.host in ("127.0.0.1", "::1")
        and request.url.hostname in ("127.0.0.1", "localhost", "::1")
    )
    if forwarded or not (request.url.scheme == "https" or loopback):
        raise HTTPException(403, "device_pairing_requires_https_or_direct_loopback")


async def _owner_tasks(request: Request, session_id: UUID) -> TaskService:
    service = _task_service(request)
    try:
        await service.owner(str(session_id))
    except AssistantAccessError as error:
        raise HTTPException(403, str(error)) from None
    return service


def _task_error(error: ValueError) -> HTTPException:
    return HTTPException(403 if isinstance(error, AssistantAccessError) else 409, str(error))


@router.get("/organizer")
async def organizer(request: Request, session_id: UUID):
    service = await _owner_tasks(request, session_id)
    return {
        "schema_version": "1.0",
        "devices": await service.repository.devices(),
        "tasks": await service.repository.tasks(),
        "operations": await service.repository.operations(),
        "scheduler_error": service.last_error,
        "history": await service.repository.history(),
    }


@router.post("/devices")
async def pair_device(request: Request, body: PairRequest):
    _device_transport(request)
    service = await _owner_tasks(request, body.session_id)
    return await service.repository.pair(body.name)


@router.post("/devices/{device_id}/revoke")
async def revoke_device(request: Request, device_id: UUID, body: OwnerRequest):
    service = await _owner_tasks(request, body.session_id)
    await service.repository.revoke(str(device_id))
    return {"revoked": True}


@router.post("/devices/poll")
async def poll_device(request: Request, body: DevicePoll):
    _device_transport(request)
    try:
        return await _task_service(request).repository.poll(
            str(body.device_id),
            body.secret.get_secret_value(),
            [s.model_dump() for s in body.sources],
            datetime.now(UTC).timestamp(),
            source_revision=body.source_revision,
        )
    except ValueError as error:
        raise _task_error(error) from None


@router.post("/devices/ack")
async def ack_device(request: Request, body: DeviceAck):
    _device_transport(request)
    if len(json.dumps(body.result)) > 128_000:
        raise HTTPException(413, "device_result_too_large")
    try:
        await _task_service(request).repository.acknowledge(
            str(body.device_id),
            body.secret.get_secret_value(),
            str(body.item_id),
            body.action,
            body.result,
            datetime.now(UTC).timestamp(),
        )
    except ValueError as error:
        raise _task_error(error) from None
    return {"accepted": True}


@router.post("/tasks")
async def create_task(request: Request, body: CreateTaskRequest):
    service = await _owner_tasks(request, body.session_id)
    try:
        return await service.repository.create_task(body.task, datetime.now(UTC).timestamp())
    except ValueError as error:
        raise _task_error(error) from None


@router.post("/tasks/{task_id}")
async def change_task(request: Request, task_id: UUID, body: ChangeTaskRequest):
    service = await _owner_tasks(request, body.session_id)
    try:
        await service.repository.change_task(
            str(task_id), body.action, datetime.now(UTC).timestamp()
        )
    except ValueError as error:
        raise _task_error(error) from None
    return {"accepted": True}


@router.post("/apple/operations")
async def create_apple_operation(request: Request, body: CreateOperationRequest):
    service = await _owner_tasks(request, body.session_id)
    try:
        return await service.repository.enqueue(body.operation, datetime.now(UTC).timestamp())
    except ValueError as error:
        raise _task_error(error) from None


@router.post("/devices/sources")
async def update_device_sources(request: Request, body: DevicePoll):
    _device_transport(request)
    try:
        return await _task_service(request).repository.poll(
            str(body.device_id),
            body.secret.get_secret_value(),
            [source.model_dump() for source in body.sources],
            datetime.now(UTC).timestamp(),
            deliver=False,
            source_revision=body.source_revision,
        )
    except ValueError as error:
        raise _task_error(error) from None


@router.post("/tasks/{task_id}/replace")
async def revise_task(request: Request, task_id: UUID, body: ReviseTaskRequest):
    service = await _owner_tasks(request, body.session_id)
    if task_id != body.task.request_id:
        raise HTTPException(422, "task_id_mismatch")
    try:
        await service.repository.revise_task(
            body.task, body.expected_revision, datetime.now(UTC).timestamp()
        )
    except ValueError as error:
        raise _task_error(error) from None
    return {"accepted": True}
