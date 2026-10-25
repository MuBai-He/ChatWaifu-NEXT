"""Runtime owns model routing; mobile receives only validated, owner-private proposals."""

import json
from typing import Any, TypedDict, cast
from uuid import uuid4

import pytest
from chatwaifu_runtime.bootstrap.container import RuntimeContainer
from chatwaifu_runtime.providers.model_config import ModelRole, ModelRoleConfig
from fastapi import FastAPI
from fastapi.testclient import TestClient


class ModelState(TypedDict):
    calls: list[tuple[str, str, dict[str, Any]]]
    result: dict[str, Any]
    error: Exception | None


type InformationClient = tuple[TestClient, ModelState, str, ModelRoleConfig]


REF = "wechat:" + "a" * 64 + ":" + "b" * 64 + ":0"
ROOM = "c" * 64


def request_body(session_id: str) -> dict[str, Any]:
    return {
        "schema_version": "1.1",
        "session_id": session_id,
        "request_id": str(uuid4()),
        "timezone": "Asia/Shanghai",
        "records": [
            {
                "ref": REF,
                "room_ref": ROOM,
                "sent_by_owner": True,
                "message_time_ms": 1791781200000,
                "text": "周五之前提交资料",
            }
        ],
    }


def suggestion(**updates: Any) -> dict[str, Any]:
    return {
        "title": "提交资料",
        "kind": "todo",
        "importance": "high",
        "reason": "本人明确承诺，时间以原消息为依据",
        "source_refs": [REF],
        "involvement": "self",
        **updates,
    }


@pytest.fixture
def information_client(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> InformationClient:
    models = cast(RuntimeContainer, cast(FastAPI, client.app).state.container).model_configurations
    original_get = models.get
    config = original_get("memory_extraction").model_copy(
        update={"provider": "openai_compatible", "enabled": True}
    )

    def get_config(role: ModelRole) -> ModelRoleConfig:
        return config if role == "memory_extraction" else original_get(role)

    monkeypatch.setattr(models, "get", get_config)
    state: ModelState = {"calls": [], "result": {"items": [suggestion()]}, "error": None}

    async def complete(role: ModelRole, system: str, user: str) -> str:
        state["calls"].append((role, system, json.loads(user)))
        if state["error"] is not None:
            raise state["error"]
        return json.dumps(state["result"], ensure_ascii=False)

    monkeypatch.setattr(models, "complete", complete)
    session = client.post("/v1/sessions", json={}).json()["session_id"]
    return client, state, session, config


def test_status_has_no_provider_credentials(information_client: InformationClient) -> None:
    client, _, _, _ = information_client
    response = client.get("/v1/personal-information/status")
    assert response.status_code == 200
    assert response.json()["model_ready"] is True
    assert response.json()["provider_api_key_required_on_client"] is False
    assert response.headers["cache-control"] == "no-store"
    assert "api_key" not in response.json()


def test_backend_model_route_and_no_conversation_generation(
    information_client: InformationClient,
) -> None:
    client, state, session, _ = information_client
    body = request_body(session)
    response = client.post("/v1/personal-information/analyze", json=body)
    assert response.status_code == 200
    data = response.json()
    assert data["request_id"] == body["request_id"]
    assert data["processed_refs"] == [REF]
    assert data["requires_confirmation"] is True
    assert data["model_owner"] == "chatwaifu_runtime"
    assert state["calls"][0][0] == "memory_extraction"
    assert "session_id" not in state["calls"][0][2]
    assert "request_id" not in state["calls"][0][2]
    assert client.get(f"/v1/sessions/{session}/messages").json()["items"] == []
    assert client.get("/v1/memory", params={"session_id": session}).json()["items"] == []


def test_unauthenticated_request_never_calls_model(information_client: InformationClient) -> None:
    client, state, session, _ = information_client
    response = client.post(
        "/v1/personal-information/analyze",
        json=request_body(session),
        headers={"Authorization": "Bearer invalid"},
    )
    assert response.status_code == 401
    assert state["calls"] == []


def test_nonexistent_owner_session_never_calls_model(information_client: InformationClient) -> None:
    client, state, _, _ = information_client
    response = client.post("/v1/personal-information/analyze", json=request_body(str(uuid4())))
    assert response.status_code == 403
    assert state["calls"] == []


@pytest.mark.parametrize("kind", ["namespace", "api_key", "model"])
def test_client_cannot_supply_namespace_model_or_provider_key(
    information_client: InformationClient, kind: str
) -> None:
    client, state, session, _ = information_client
    body = request_body(session)
    body[kind] = "private-input-marker"
    response = client.post("/v1/personal-information/analyze", json=body)
    assert response.status_code == 422
    assert "private-input-marker" not in response.text
    assert state["calls"] == []


def test_input_budget_keeps_source_instead_of_truncating(
    information_client: InformationClient,
) -> None:
    client, state, session, _ = information_client
    body = request_body(session)
    body["records"] = [
        {**body["records"][0], "ref": REF[:-1] + str(i), "text": "字" * 3000} for i in range(6)
    ]
    response = client.post("/v1/personal-information/analyze", json=body)
    assert response.status_code == 422
    assert state["calls"] == []


def test_duplicate_sources_are_rejected(information_client: InformationClient) -> None:
    client, state, session, _ = information_client
    body = request_body(session)
    body["records"] *= 2
    assert client.post("/v1/personal-information/analyze", json=body).status_code == 422
    assert state["calls"] == []


@pytest.mark.parametrize(
    "update",
    [
        {"source_refs": ["wechat:" + "f" * 64]},
        {"operation": "update", "target_id": "unknown", "expected_revision": 1},
        {"operation": "create", "target_id": "existing"},
        {"due_at": "2026-10-16T18:00:00"},
        {"operation": "cancel", "due_at": "2026-10-16T18:00:00+08:00"},
        {"kind": "event", "starts_at": "2026-10-16T18:00:00Z", "ends_at": "2026-10-16T17:00:00Z"},
    ],
)
def test_invalid_model_output_does_not_advance_source(
    information_client: InformationClient, update: dict[str, Any]
) -> None:
    client, state, session, _ = information_client
    state["result"] = {"items": [suggestion(**update)]}
    response = client.post("/v1/personal-information/analyze", json=request_body(session))
    assert response.status_code == 502
    assert response.json() == {"detail": "information_model_output_invalid"}
    assert "processed_refs" not in response.json()


def test_known_task_change_preserves_target_revision(information_client: InformationClient) -> None:
    client, state, session, _ = information_client
    body = request_body(session)
    body["known_items"] = [
        {
            "id": "work:fixture",
            "room_ref": ROOM,
            "revision": 2,
            "kind": "todo",
            "title": "提交资料",
            "state": "open",
            "involvement": "self",
        }
    ]
    state["result"] = {
        "items": [
            suggestion(
                operation="update",
                target_id="work:fixture",
                expected_revision=2,
                due_at="2026-10-16T18:00:00+08:00",
            )
        ]
    }
    response = client.post("/v1/personal-information/analyze", json=body)
    assert response.status_code == 200
    assert response.json()["items"][0]["expected_revision"] == 2
    state["result"]["items"][0]["expected_revision"] = 1
    assert client.post("/v1/personal-information/analyze", json=body).status_code == 502


def test_outside_room_context_is_rejected(information_client: InformationClient) -> None:
    client, state, session, _ = information_client
    body = request_body(session)
    body["known_items"] = [
        {
            "id": "work:fixture",
            "room_ref": "d" * 64,
            "revision": 2,
            "kind": "todo",
            "title": "另一个会话",
            "state": "open",
            "involvement": "other",
        }
    ]
    assert client.post("/v1/personal-information/analyze", json=body).status_code == 422
    assert state["calls"] == []


def test_provider_failure_never_echoes_credentials_or_source(
    information_client: InformationClient,
) -> None:
    client, state, session, _ = information_client
    state["error"] = RuntimeError("secret-provider-token private-message-body")
    response = client.post("/v1/personal-information/analyze", json=request_body(session))
    assert response.status_code == 503
    assert "secret-provider-token" not in response.text
    assert "private-message-body" not in response.text
