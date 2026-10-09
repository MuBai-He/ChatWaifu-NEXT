"""HTTP/DTO and real middleware tests; the recorded service is not domain acceptance."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Protocol, cast
from urllib.parse import urlencode
from uuid import UUID, uuid4

import pytest
from chatwaifu_protocol.channel_groups import (
    ChannelGroupAudienceRequest,
    ChannelGroupAudienceSnapshot,
    ChannelGroupRegistrationRequest,
    ChannelGroupRouteCreate,
    ChannelGroupRouteMemberSnapshot,
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
from chatwaifu_protocol.channels import (
    ChannelChatType,
    ChannelDeliveryStatus,
    ChannelErrorResponse,
    ChannelTurnSnapshot,
    ChannelTurnStatus,
)
from chatwaifu_runtime.api.channel_group_routes import router
from chatwaifu_runtime.api.guard import (
    CHANNEL_EXEMPT_RE,
    LocalClientGuardMiddleware,
    WebSocketTicketStore,
)
from chatwaifu_runtime.config.settings import Settings
from chatwaifu_runtime.external_channels.service import (
    ChannelBusyError,
    ChannelConflictError,
    ChannelNotFoundError,
    ChannelPolicyError,
    ExternalChannelError,
)
from fastapi import FastAPI
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient
from httpx2 import Response

OPERATOR_TOKEN = "test-only-group-operator-capability"
NOW = datetime(2026, 10, 4, 12, 0, tzinfo=UTC)
CASES = (
    ("observe_audience", "POST", "group-audience-observations"),
    ("register_audience", "POST", "group-participant-registrations"),
    ("list_links", "GET", "participant-links"),
    ("create_link", "POST", "participant-links"),
    ("update_link", "PUT", "participant-links/{link_id}"),
    ("list_routes", "GET", "group-routes"),
    ("create_route", "POST", "group-routes"),
    ("update_route", "PUT", "group-routes/{route_id}"),
    ("list_turns", "GET", "group-routes/{route_id}/turns"),
    ("cancel_turn", "POST", "group-routes/{route_id}/turns/{turn_id}/cancel"),
)


class _Http(Protocol):
    def request(
        self, method: str, url: str, *, json: object = None, headers: dict[str, str] | None = None
    ) -> Response: ...


class _GroupService:
    """Only records typed router calls and returns fixed contract snapshots."""

    def __init__(self) -> None:
        self.connection_id = uuid4()
        self.route_id = uuid4()
        self.link_id = uuid4()
        self.turn_id = uuid4()
        self.observation_id = uuid4()
        self.calls: list[tuple[str, tuple[object, ...], dict[str, object]]] = []
        self.error: ExternalChannelError | None = None
        self.unavailable = False
        self.audience = ChannelGroupAudienceSnapshot(
            observation_id=self.observation_id,
            connection_id=self.connection_id,
            connection_revision=2,
            account_key="900",
            group_id="123",
            member_ids=["100", "200"],
            member_fingerprint="a" * 64,
            observed_at=NOW,
            expires_at=NOW + timedelta(seconds=30),
        )
        self.link = ChannelParticipantLinkSnapshot(
            link_id=self.link_id,
            account_key="900",
            sender_key="100",
            participant_id="alice",
            enabled=True,
            revision=2,
            created_at=NOW,
            updated_at=NOW,
        )
        self.route = ChannelGroupRouteSnapshot(
            route_id=self.route_id,
            connection_id=self.connection_id,
            account_key="900",
            group_id="123",
            character_id="default",
            scene_id="scene-new",
            display_name="Registered group",
            revision=2,
            observation_id=self.observation_id,
            audience_fingerprint="a" * 64,
            members=[
                ChannelGroupRouteMemberSnapshot(
                    link_id=self.link_id,
                    sender_key="100",
                    participant_id="alice",
                    can_speak=True,
                ),
                ChannelGroupRouteMemberSnapshot(
                    link_id=uuid4(), sender_key="200", participant_id="bob", can_speak=False
                ),
            ],
            created_at=NOW,
            updated_at=NOW,
        )
        self.turn = ChannelGroupTurnSnapshot(
            route_id=self.route_id,
            route_revision=1,
            scene_id="scene-old",
            participant_id="alice",
            provider_receipt_present=True,
            cancelable=False,
            turn=ChannelTurnSnapshot(
                channel_turn_id=self.turn_id,
                connection_id=self.connection_id,
                account_key="900",
                external_message_id="group:123:one-message",
                sender_key="100",
                conversation_key="123",
                principal_scope="scene:scene-old",
                chat_type=ChannelChatType.GROUP,
                session_id=uuid4(),
                turn_id=uuid4(),
                generation_id=uuid4(),
                status=ChannelTurnStatus.CANCELLED,
                revision=3,
                reply_text="Fixture confirmed reply",
                delivery_status=ChannelDeliveryStatus.DELIVERED,
                created_at=NOW,
                updated_at=NOW,
            ),
        )

    def _record(self, name: str, *arguments: object, **keywords: object) -> None:
        self.calls.append((name, arguments, keywords))
        if self.error:
            raise self.error
        if self.unavailable and not name.startswith("list_"):
            raise ChannelPolicyError("fixture connection unavailable")

    async def observe_audience(
        self, connection_id: UUID, body: ChannelGroupAudienceRequest
    ) -> ChannelGroupAudienceSnapshot:
        self._record("observe_audience", connection_id, body)
        return self.audience

    async def register_audience(
        self, connection_id: UUID, body: ChannelGroupRegistrationRequest
    ) -> ChannelGroupAudienceSnapshot:
        self._record("register_audience", connection_id, body)
        return self.audience

    async def list_links(
        self, connection_id: UUID, *, limit: int, cursor: str | None
    ) -> ChannelParticipantLinkPage:
        self._record("list_links", connection_id, limit=limit, cursor=cursor)
        return ChannelParticipantLinkPage(items=[self.link], next_cursor="links-cursor")

    async def create_link(
        self, connection_id: UUID, body: ChannelParticipantLinkCreate
    ) -> ChannelParticipantLinkSnapshot:
        self._record("create_link", connection_id, body)
        return self.link

    async def update_link(
        self, connection_id: UUID, link_id: UUID, body: ChannelParticipantLinkUpdate
    ) -> ChannelParticipantLinkSnapshot:
        self._record("update_link", connection_id, link_id, body)
        return self.link

    async def list_routes(
        self, connection_id: UUID, *, limit: int, cursor: str | None
    ) -> ChannelGroupRoutePage:
        self._record("list_routes", connection_id, limit=limit, cursor=cursor)
        return ChannelGroupRoutePage(items=[self.route], next_cursor="routes-cursor")

    async def create_route(
        self, connection_id: UUID, body: ChannelGroupRouteCreate
    ) -> ChannelGroupRouteSnapshot:
        self._record("create_route", connection_id, body)
        return self.route

    async def update_route(
        self, connection_id: UUID, route_id: UUID, body: ChannelGroupRouteUpdate
    ) -> ChannelGroupRouteSnapshot:
        self._record("update_route", connection_id, route_id, body)
        return self.route

    async def list_turns(
        self, connection_id: UUID, route_id: UUID, *, limit: int, cursor: str | None
    ) -> ChannelGroupTurnPage:
        self._record("list_turns", connection_id, route_id, limit=limit, cursor=cursor)
        return ChannelGroupTurnPage(items=[self.turn], next_cursor="turns-cursor")

    async def cancel_turn(
        self,
        connection_id: UUID,
        route_id: UUID,
        channel_turn_id: UUID,
        body: ChannelGroupTurnCancelRequest,
    ) -> ChannelGroupTurnSnapshot:
        self._record("cancel_turn", connection_id, route_id, channel_turn_id, body)
        return self.turn


def _client(service: _GroupService, settings: Settings) -> TestClient:
    app = FastAPI()
    app.state.container = SimpleNamespace(channel_groups=service)
    app.include_router(router)
    app.add_middleware(
        LocalClientGuardMiddleware,
        settings=settings,
        capability_token=OPERATOR_TOKEN,
        ticket_store=WebSocketTicketStore(),
    )
    return TestClient(app, base_url="http://127.0.0.1")


def _url(service: _GroupService, suffix: str) -> str:
    suffix = suffix.format(
        link_id=service.link_id, route_id=service.route_id, turn_id=service.turn_id
    )
    return f"/v1/channel-connections/{service.connection_id}/{suffix}"


def _body(service: _GroupService, name: str) -> dict[str, object] | None:
    bodies: dict[str, dict[str, object]] = {
        "observe_audience": {"group_id": "123"},
        "register_audience": {"observation_id": str(service.observation_id)},
        "create_link": {
            "observation_id": str(service.observation_id),
            "sender_key": "100",
            "participant_id": "alice",
        },
        "update_link": {"enabled": False, "expected_revision": 2},
        "create_route": {
            "observation_id": str(service.observation_id),
            "display_name": "Registered group",
            "speaker_sender_keys": ["100"],
        },
        "update_route": {
            "enabled": True,
            "expected_revision": 2,
            "observation_id": str(service.observation_id),
            "speaker_sender_keys": ["100"],
        },
        "cancel_turn": {"expected_revision": 3},
    }
    return bodies.get(name)


def _request(
    client: TestClient,
    method: str,
    url: str,
    *,
    body: object = None,
    token: str | None = OPERATOR_TOKEN,
) -> Response:
    headers = {"Authorization": f"Bearer {token}"} if token is not None else {}
    return cast(_Http, client).request(method, url, json=body, headers=headers)


@pytest.mark.parametrize(("name", "method", "suffix"), CASES)
@pytest.mark.parametrize("token", [None, "test-only-adapter-credential"])
def test_every_path_requires_operator_not_adapter_auth(
    runtime_settings: Settings, name: str, method: str, suffix: str, token: str | None
) -> None:
    service = _GroupService()
    url = _url(service, suffix)
    assert CHANNEL_EXEMPT_RE.fullmatch(url) is None
    with _client(service, runtime_settings) as client:
        response = _request(client, method, url, body=_body(service, name), token=token)
    assert response.status_code == 401
    assert service.calls == []


@pytest.mark.parametrize(("name", "method", "suffix"), CASES)
def test_all_response_dtos_and_typed_service_dispatch(
    runtime_settings: Settings, name: str, method: str, suffix: str
) -> None:
    service = _GroupService()
    body = _body(service, name)
    with _client(service, runtime_settings) as client:
        response = _request(client, method, _url(service, suffix), body=body)
    assert response.status_code == 200
    assert len(service.calls) == 1
    called, arguments, keywords = service.calls[0]
    assert called == name
    assert arguments[0] == service.connection_id
    assert isinstance(arguments[0], UUID)
    if "{link_id}" in suffix:
        assert arguments[1] == service.link_id
    if "{route_id}" in suffix:
        assert arguments[1] == service.route_id
    if "{turn_id}" in suffix:
        assert arguments[2] == service.turn_id
    if body is not None:
        expected_type = {
            "observe_audience": ChannelGroupAudienceRequest,
            "register_audience": ChannelGroupRegistrationRequest,
            "create_link": ChannelParticipantLinkCreate,
            "update_link": ChannelParticipantLinkUpdate,
            "create_route": ChannelGroupRouteCreate,
            "update_route": ChannelGroupRouteUpdate,
            "cancel_turn": ChannelGroupTurnCancelRequest,
        }[name]
        assert isinstance(arguments[-1], expected_type)
        assert arguments[-1] == expected_type.model_validate(body)
    else:
        assert keywords == {"limit": 25, "cursor": None}
    expected = {
        "observe_audience": service.audience,
        "register_audience": service.audience,
        "list_links": ChannelParticipantLinkPage(items=[service.link], next_cursor="links-cursor"),
        "create_link": service.link,
        "update_link": service.link,
        "list_routes": ChannelGroupRoutePage(items=[service.route], next_cursor="routes-cursor"),
        "create_route": service.route,
        "update_route": service.route,
        "list_turns": ChannelGroupTurnPage(items=[service.turn], next_cursor="turns-cursor"),
        "cancel_turn": service.turn,
    }[name]
    assert response.json() == expected.model_dump(mode="json")
    if name == "create_route":
        assert ChannelGroupRouteSnapshot.model_validate(response.json()).enabled is False


@pytest.mark.parametrize(
    "suffix", ["participant-links", "group-routes", "group-routes/{route_id}/turns"]
)
@pytest.mark.parametrize(
    "query", ["limit=0", "limit=51", "limit=word", "cursor=", "cursor=" + "x" * 257]
)
def test_pagination_is_bounded_before_dispatch(
    runtime_settings: Settings, suffix: str, query: str
) -> None:
    service = _GroupService()
    with _client(service, runtime_settings) as client:
        response = _request(client, "GET", _url(service, suffix) + "?" + query)
    assert response.status_code == 422
    assert service.calls == []


@pytest.mark.parametrize(
    "suffix", ["participant-links", "group-routes", "group-routes/{route_id}/turns"]
)
def test_cursor_is_opaque_and_limit_reaches_service(
    runtime_settings: Settings, suffix: str
) -> None:
    service = _GroupService()
    cursor = "opaque:+&/=?# Unicode 雪"
    with _client(service, runtime_settings) as client:
        response = _request(
            client, "GET", _url(service, suffix) + "?" + urlencode({"limit": 50, "cursor": cursor})
        )
    assert response.status_code == 200
    assert service.calls[0][2] == {"limit": 50, "cursor": cursor}


@pytest.mark.parametrize(("name", "method", "suffix"), [case for case in CASES if case[1] != "GET"])
def test_extra_trusted_identity_or_recipient_fields_never_reach_service(
    runtime_settings: Settings, name: str, method: str, suffix: str
) -> None:
    service = _GroupService()
    body = dict(_body(service, name) or {})
    body.update({"trusted_identity": {"participant_id": "someone-else"}, "recipient": "456"})
    with _client(service, runtime_settings) as client:
        response = _request(client, method, _url(service, suffix), body=body)
    assert response.status_code == 422
    assert service.calls == []


@pytest.mark.parametrize(
    ("method", "suffix", "body"),
    [
        ("POST", "group-audience-observations", {"group_id": "0123"}),
        ("POST", "group-routes", {"display_name": "group", "enabled": True}),
        ("PUT", "participant-links/{link_id}", {"enabled": "false", "expected_revision": 2}),
        ("PUT", "participant-links/{link_id}", {"enabled": False, "expected_revision": True}),
        (
            "PUT",
            "group-routes/{route_id}",
            {"enabled": True, "expected_revision": 2, "speaker_sender_keys": ["100"]},
        ),
        (
            "PUT",
            "group-routes/{route_id}",
            {"enabled": False, "expected_revision": 2, "speaker_sender_keys": ["100", "100"]},
        ),
        ("POST", "group-routes/{route_id}/turns/{turn_id}/cancel", {"expected_revision": -1}),
    ],
)
def test_invalid_or_incomplete_request_dtos_never_dispatch(
    runtime_settings: Settings, method: str, suffix: str, body: dict[str, object]
) -> None:
    service = _GroupService()
    with _client(service, runtime_settings) as client:
        response = _request(client, method, _url(service, suffix), body=body)
    assert response.status_code == 422
    assert service.calls == []


@pytest.mark.parametrize("invalid_part", ["connection", "link", "route", "turn"])
def test_path_identity_requires_uuid_before_dispatch(
    runtime_settings: Settings, invalid_part: str
) -> None:
    service = _GroupService()
    if invalid_part == "connection":
        url = _url(service, "group-routes").replace(str(service.connection_id), "not-a-uuid")
        method, body = "GET", None
    elif invalid_part == "link":
        url = _url(service, "participant-links/{link_id}").replace(str(service.link_id), "bad")
        method, body = "PUT", _body(service, "update_link")
    else:
        url = _url(service, "group-routes/{route_id}/turns/{turn_id}/cancel")
        url = url.replace(
            str(service.route_id if invalid_part == "route" else service.turn_id), "bad"
        )
        method, body = "POST", _body(service, "cancel_turn")
    with _client(service, runtime_settings) as client:
        response = _request(client, method, url, body=body)
    assert response.status_code == 422
    assert service.calls == []


@pytest.mark.parametrize(
    "error_type", [ChannelConflictError, ChannelPolicyError, ChannelNotFoundError, ChannelBusyError]
)
@pytest.mark.parametrize(("name", "method", "suffix"), CASES)
def test_structured_domain_errors_keep_status_component_and_retry_contract(
    runtime_settings: Settings,
    error_type: type[ExternalChannelError],
    name: str,
    method: str,
    suffix: str,
) -> None:
    service = _GroupService()
    service.error = error_type("bounded fixture error")
    with _client(service, runtime_settings) as client:
        response = _request(client, method, _url(service, suffix), body=_body(service, name))
    assert response.status_code == service.error.http_status
    result = ChannelErrorResponse.model_validate(response.json()["detail"])
    assert result.error.component == "channel_groups"
    assert result.error.code == service.error.code
    assert result.error.retryable == service.error.retryable
    assert result.retry_after_ms == (500 if service.error.retryable else None)
    assert len(service.calls) == 1  # A conflict is never silently retried.


def test_disabled_unbound_history_preserves_cancelled_and_confirmed_delivery_facts(
    runtime_settings: Settings,
) -> None:
    service = _GroupService()
    service.unavailable = True
    with _client(service, runtime_settings) as client:
        result = _request(client, "GET", _url(service, "group-routes/{route_id}/turns"))
        cancellation = _request(
            client,
            "POST",
            _url(service, "group-routes/{route_id}/turns/{turn_id}/cancel"),
            body=_body(service, "cancel_turn"),
        )
    assert result.status_code == 200
    page = ChannelGroupTurnPage.model_validate(result.json())
    assert len(page.items) == 1
    assert page.items[0].turn.status is ChannelTurnStatus.CANCELLED
    assert page.items[0].turn.delivery_status is ChannelDeliveryStatus.DELIVERED
    assert page.items[0].provider_receipt_present is True
    assert page.items[0].cancelable is False
    assert cancellation.status_code == 403
    assert page.items[0] == service.turn


def test_router_exposes_only_operator_management_not_group_ingress_or_send() -> None:
    paths = {route.path for route in router.routes if isinstance(route, APIRoute)}
    assert len(router.routes) == len(CASES)
    assert len(paths) == len({suffix for _, _, suffix in CASES})
    assert all("/messages" not in path and "/send" not in path for path in paths)
    assert all(CHANNEL_EXEMPT_RE.fullmatch(path) is None for path in paths)
