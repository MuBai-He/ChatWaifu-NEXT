"""Proactive management stays behind operator auth and bounded DTOs."""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Protocol, cast
from uuid import UUID, uuid4

import pytest
from chatwaifu_protocol.channel_proactive import (
    ChannelOutboundIntentCancelRequest,
    ChannelOutboundIntentPage,
    ChannelOutboundIntentSnapshot,
    ChannelOutboundIntentStatus,
    ChannelProactivePolicySnapshot,
    ChannelProactivePolicyUpdate,
    ChannelProactivePreview,
    ChannelProactiveReason,
)
from chatwaifu_protocol.channels import ChannelDeliveryStatus
from chatwaifu_runtime.api.channel_proactive_routes import router
from chatwaifu_runtime.api.guard import LocalClientGuardMiddleware, WebSocketTicketStore
from chatwaifu_runtime.config.settings import Settings
from chatwaifu_runtime.external_channels.service import ChannelConflictError
from fastapi import FastAPI
from fastapi.testclient import TestClient
from httpx2 import Response

OPERATOR_TOKEN = "test-only-proactive-operator-capability"
NOW = datetime(2026, 10, 3, 12, 0, tzinfo=UTC)


class _Http(Protocol):
    def request(
        self,
        method: str,
        url: str,
        *,
        json: object = None,
        headers: dict[str, str] | None = None,
    ) -> Response: ...


class _PolicyService:
    def __init__(self) -> None:
        self.connection_id = uuid4()
        self.policy = ChannelProactivePolicySnapshot(connection_id=self.connection_id)
        self.intent = ChannelOutboundIntentSnapshot(
            request_id=uuid4(),
            connection_id=self.connection_id,
            binding_id=uuid4(),
            session_id=uuid4(),
            turn_id=uuid4(),
            generation_id=uuid4(),
            status=ChannelOutboundIntentStatus.SETTLED,
            policy_revision=1,
            route_revision=1,
            revision=2,
            not_before_at=NOW,
            expires_at=NOW,
            created_at=NOW,
            updated_at=NOW,
            delivery_status=ChannelDeliveryStatus.DELIVERED,
            provider_receipt_present=True,
            cancelable=False,
        )
        self.calls: list[str] = []
        self.list_arguments: tuple[int, str | None] | None = None
        self.conflict = False

    async def get_policy(self, connection_id: UUID) -> ChannelProactivePolicySnapshot:
        assert connection_id == self.connection_id
        self.calls.append("get_policy")
        return self.policy

    async def update_policy(
        self, connection_id: UUID, body: ChannelProactivePolicyUpdate
    ) -> ChannelProactivePolicySnapshot:
        assert connection_id == self.connection_id
        self.calls.append("update_policy")
        if self.conflict:
            raise ChannelConflictError("proactive policy revision changed")
        self.policy = self.policy.model_copy(
            update={"policy": body.policy, "revision": body.expected_revision + 1}
        )
        return self.policy

    async def preview(self, connection_id: UUID) -> ChannelProactivePreview:
        assert connection_id == self.connection_id
        self.calls.append("preview")
        return ChannelProactivePreview(
            connection_id=connection_id,
            policy_revision=0,
            eligible=False,
            reason=ChannelProactiveReason.DISABLED,
            evaluated_at=NOW,
            remaining_daily_budget=3,
        )

    async def list_intents(
        self, connection_id: UUID, *, limit: int, cursor: str | None
    ) -> ChannelOutboundIntentPage:
        assert connection_id == self.connection_id
        self.calls.append("list_intents")
        self.list_arguments = (limit, cursor)
        return ChannelOutboundIntentPage(items=[self.intent])

    async def cancel_intent(
        self,
        connection_id: UUID,
        request_id: UUID,
        body: ChannelOutboundIntentCancelRequest,
    ) -> ChannelOutboundIntentSnapshot:
        assert connection_id == self.connection_id
        assert request_id == self.intent.request_id
        self.calls.append("cancel_intent")
        if body.expected_revision != self.intent.revision:
            raise ChannelConflictError("proactive intent revision changed")
        return self.intent


def _client(service: _PolicyService, settings: Settings) -> TestClient:
    app = FastAPI()
    app.state.container = SimpleNamespace(channel_proactive=service)
    app.include_router(router)
    secured = settings.model_copy(
        update={"security": settings.security.model_copy(update={"auth_enabled": True})}
    )
    app.add_middleware(
        LocalClientGuardMiddleware,
        settings=secured,
        capability_token=OPERATOR_TOKEN,
        ticket_store=WebSocketTicketStore(),
    )
    return TestClient(app, base_url="http://127.0.0.1")


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


@pytest.mark.parametrize("token", [None, "test-only-channel-adapter-token"])
@pytest.mark.parametrize(
    ("method", "suffix"),
    [
        ("GET", "proactive-policy"),
        ("PUT", "proactive-policy"),
        ("POST", "proactive-preview"),
        ("GET", "outbound-intents"),
        ("POST", "outbound-intents/{request_id}/cancel"),
    ],
)
def test_all_management_paths_require_operator_auth(
    runtime_settings: Settings, method: str, suffix: str, token: str | None
) -> None:
    service = _PolicyService()
    suffix = suffix.format(request_id=service.intent.request_id)
    with _client(service, runtime_settings) as client:
        result = _request(
            client,
            method,
            f"/v1/channel-connections/{service.connection_id}/{suffix}",
            body={"expected_revision": 0, "policy": {"enabled": True}},
            token=token,
        )
    assert result.status_code == 401
    assert service.calls == []


def test_preview_calls_no_write_or_generation_surface(runtime_settings: Settings) -> None:
    service = _PolicyService()
    before = service.policy
    with _client(service, runtime_settings) as client:
        result = _request(
            client,
            "POST",
            f"/v1/channel-connections/{service.connection_id}/proactive-preview",
        )
    assert result.status_code == 200
    preview = ChannelProactivePreview.model_validate(result.json())
    assert preview.eligible is False
    assert preview.reason is ChannelProactiveReason.DISABLED
    assert service.calls == ["preview"]
    assert service.policy == before


@pytest.mark.parametrize(
    "body",
    [
        {"policy": {"enabled": True}},
        {"expected_revision": 0, "policy": {"enabled": True, "ttl_minutes": 61}},
        {"expected_revision": 0, "policy": {"recipient": "another-owner"}},
        {"expected_revision": 0, "policy": {}, "connection_id": "forged-route"},
    ],
)
def test_invalid_policy_never_reaches_service(
    runtime_settings: Settings, body: dict[str, object]
) -> None:
    service = _PolicyService()
    with _client(service, runtime_settings) as client:
        result = _request(
            client,
            "PUT",
            f"/v1/channel-connections/{service.connection_id}/proactive-policy",
            body=body,
        )
    assert result.status_code == 422
    assert service.calls == []


def test_policy_conflict_is_not_silently_retried(runtime_settings: Settings) -> None:
    service = _PolicyService()
    service.conflict = True
    with _client(service, runtime_settings) as client:
        result = _request(
            client,
            "PUT",
            f"/v1/channel-connections/{service.connection_id}/proactive-policy",
            body={"expected_revision": 0, "policy": {"enabled": True}},
        )
    assert result.status_code == 409
    assert service.calls == ["update_policy"]
    assert not service.policy.policy.enabled


@pytest.mark.parametrize("query", ["limit=51", "limit=0", "cursor=" + "x" * 257])
def test_history_requests_are_bounded_before_service(
    runtime_settings: Settings, query: str
) -> None:
    service = _PolicyService()
    with _client(service, runtime_settings) as client:
        result = _request(
            client,
            "GET",
            f"/v1/channel-connections/{service.connection_id}/outbound-intents?{query}",
        )
    assert result.status_code == 422
    assert service.calls == []


def test_cancel_returns_actual_delivery_fact(runtime_settings: Settings) -> None:
    service = _PolicyService()
    with _client(service, runtime_settings) as client:
        result = _request(
            client,
            "POST",
            f"/v1/channel-connections/{service.connection_id}/outbound-intents/"
            f"{service.intent.request_id}/cancel",
            body={"expected_revision": 2},
        )
    assert result.status_code == 200
    snapshot = ChannelOutboundIntentSnapshot.model_validate(result.json())
    assert snapshot.delivery_status is ChannelDeliveryStatus.DELIVERED
    assert snapshot.provider_receipt_present is True
    assert snapshot.cancelable is False
    assert service.calls == ["cancel_intent"]
