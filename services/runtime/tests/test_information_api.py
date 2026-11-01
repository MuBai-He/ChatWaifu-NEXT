"""Runtime owns model routing; mobile receives only validated, owner-private proposals."""

import json
from datetime import UTC, datetime, timedelta
from typing import Any, TypedDict, cast
from uuid import uuid4
from zoneinfo import ZoneInfo

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


@pytest.mark.parametrize(
    ("age_days", "updates", "expected"),
    [
        (800, {"due_at": "2024-06-05T17:00:00+08:00"}, "historical"),
        (800, {}, "uncertain"),
        (800, {"due_date": "2026-11-10"}, "current"),
        (1, {"due_at": "2026-10-29T17:00:00+08:00"}, "current"),
        (800, {"kind": "memory"}, "current"),
        (1, {"kind": "event", "starts_at": "2026-10-30T09:00:00+08:00"}, "historical"),
        (1, {"kind": "event", "starts_at": "2026-11-02T09:00:00+08:00"}, "current"),
        (
            800,
            {"kind": "event", "start_date": "2026-10-31", "end_date": "2026-11-01"},
            "historical",
        ),
    ],
)
def test_historical_work_is_not_asserted_current_by_model(
    information_client: InformationClient,
    monkeypatch: pytest.MonkeyPatch,
    age_days: int,
    updates: dict[str, Any],
    expected: str,
) -> None:
    from datetime import timedelta

    from chatwaifu_runtime.information import service

    now = datetime(2026, 11, 1, 1, tzinfo=UTC)

    class Clock(datetime):
        @classmethod
        def now(cls, tz: Any = None) -> datetime:
            return now if tz is None else now.astimezone(tz)

    monkeypatch.setattr(service, "datetime", Clock)
    client, state, session, _ = information_client
    body = request_body(session)
    body["records"][0].update(
        message_time_ms=int((now - timedelta(days=age_days)).timestamp() * 1000),
        text="提交合成资料，原文只说明当时的安排。",
    )
    state["result"] = {"items": [suggestion(temporal_status="current", **updates)]}
    response = client.post("/v1/personal-information/analyze", json=body)
    assert response.status_code == 200
    item = response.json()["items"][0]
    assert item["temporal_status"] == expected
    assert item["state"] == "open"  # Classification never fabricates completion.
    assert item["kind"] == updates.get("kind", "todo")
    assert response.json()["requires_confirmation"] is True
    assert response.json()["processed_refs"] == [REF]
    payload = state["calls"][0][2]
    assert payload["current_local_time"] == "2026-11-01T09:00:00+08:00"
    assert payload["records"][0]["message_time_ms"] == body["records"][0]["message_time_ms"]


def test_uncited_recent_message_cannot_reactivate_old_work(
    information_client: InformationClient,
) -> None:
    client, state, session, _ = information_client
    body = request_body(session)
    body["records"][0].update(message_time_ms=1717570800000, text="当时待处理的合成资料")
    body["records"].append(
        {
            **body["records"][0],
            "ref": "wechat:" + "d" * 64,
            "message_time_ms": int(datetime.now(UTC).timestamp() * 1000),
            "text": "无关的最近闲聊",
        }
    )
    state["result"] = {"items": [suggestion()]}
    response = client.post("/v1/personal-information/analyze", json=body)
    assert response.status_code == 200
    assert response.json()["items"][0]["temporal_status"] == "uncertain"


def test_unknown_optional_location_is_canonicalized_without_extra_model_call(
    information_client: InformationClient,
) -> None:
    client, state, session, _ = information_client
    state["result"] = {"items": [suggestion(location=None)]}
    response = client.post("/v1/personal-information/analyze", json=request_body(session))
    assert response.status_code == 200
    assert response.json()["items"][0]["location"] == ""
    assert len(state["calls"]) == 1


def test_relative_deadline_uses_source_local_date_and_repairs_day_drift(
    information_client: InformationClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    client, _state, session, _ = information_client
    runtime = cast(RuntimeContainer, cast(FastAPI, client.app).state.container)
    body = request_body(session)
    body["records"][0]["text"] = "这次作业明晚21点截止。"
    anchor = datetime.fromtimestamp(
        body["records"][0]["message_time_ms"] / 1000, ZoneInfo("Asia/Shanghai")
    )
    calls: list[dict[str, Any]] = []

    async def complete(role: ModelRole, system: str, user: str) -> str:
        calls.append(json.loads(user))
        due = (anchor + timedelta(days=2 if len(calls) == 1 else 1)).replace(
            hour=21, minute=0, second=0, microsecond=0
        )
        return json.dumps({"items": [suggestion(due_at=due.isoformat(), due_text="明晚21点")]})

    monkeypatch.setattr(runtime.model_configurations, "complete", complete)
    response = client.post("/v1/personal-information/analyze", json=body)
    assert response.status_code == 200
    assert len(calls) == 2
    assert calls[0]["records"][0]["message_local_time"] == anchor.isoformat()
    assert datetime.fromisoformat(response.json()["items"][0]["due_at"]).date() == (
        anchor.date() + timedelta(days=1)
    )


def test_reschedule_cannot_merge_new_start_with_an_old_earlier_end(
    information_client: InformationClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    client, _state, session, _ = information_client
    runtime = cast(RuntimeContainer, cast(FastAPI, client.app).state.container)
    body = request_body(session)
    body["records"][0]["text"] = "明天上午的课程从9点改到11点，教室不变。"
    start = datetime.fromtimestamp(
        body["records"][0]["message_time_ms"] / 1000, ZoneInfo("Asia/Shanghai")
    ).replace(hour=9, minute=0, second=0, microsecond=0) + timedelta(days=1)
    body["known_items"] = [
        {
            "id": "bound-course",
            "room_ref": ROOM,
            "revision": 1,
            "kind": "event",
            "title": "课程",
            "state": "open",
            "involvement": "self",
            "starts_at": start.isoformat(),
            "ends_at": (start + timedelta(hours=1)).isoformat(),
        }
    ]
    calls = 0

    async def complete(role: ModelRole, system: str, user: str) -> str:
        nonlocal calls
        calls += 1
        return json.dumps(
            {
                "items": [
                    suggestion(
                        kind="event",
                        operation="update",
                        target_id="bound-course",
                        expected_revision=1,
                        starts_at=(start + timedelta(hours=2)).isoformat(),
                        ends_at=None if calls == 1 else (start + timedelta(hours=3)).isoformat(),
                    )
                ]
            }
        )

    monkeypatch.setattr(runtime.model_configurations, "complete", complete)
    response = client.post("/v1/personal-information/analyze", json=body)
    assert response.status_code == 200
    assert calls == 2
    assert datetime.fromisoformat(response.json()["items"][0]["ends_at"]) == (
        start + timedelta(hours=3)
    )


def test_format_repair_reuses_original_evidence_and_is_bounded(
    information_client: InformationClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    client, _state, session, _ = information_client
    runtime = cast(RuntimeContainer, cast(FastAPI, client.app).state.container)
    prompts: list[tuple[str, str]] = []

    async def complete(role: ModelRole, system: str, user: str) -> str:
        prompts.append((system, user))
        return json.dumps({"items": [suggestion(requested_fields=["location"])]})

    monkeypatch.setattr(runtime.model_configurations, "complete", complete)
    response = client.post("/v1/personal-information/analyze", json=request_body(session))
    assert response.status_code == 502
    assert len(prompts) == 2
    assert prompts[0][1] == prompts[1][1]
    assert "校验摘要" in prompts[1][0]


def test_format_repair_can_recover_without_logging_untrusted_output(
    information_client: InformationClient,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    client, _, session, _ = information_client
    runtime = cast(RuntimeContainer, cast(FastAPI, client.app).state.container)
    calls: list[str] = []

    async def complete(role: ModelRole, system: str, user: str) -> str:
        calls.append(user)
        return json.dumps(
            {
                "items": [
                    suggestion(
                        **(
                            {"private_unexpected_field": "private-model-output-value"}
                            if len(calls) == 1
                            else {}
                        )
                    )
                ]
            }
        )

    monkeypatch.setattr(runtime.model_configurations, "complete", complete)
    response = client.post("/v1/personal-information/analyze", json=request_body(session))
    assert response.status_code == 200
    assert len(calls) == 2 and calls[0] == calls[1]
    assert response.json()["processed_refs"] == [REF]
    assert "information_output_invalid" in caplog.text
    assert "unknown_field" in caplog.text
    assert "private_unexpected_field" not in caplog.text
    assert "private-model-output-value" not in caplog.text


def test_repaired_format_still_rejects_unknown_evidence(
    information_client: InformationClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    client, _state, session, _ = information_client
    runtime = cast(RuntimeContainer, cast(FastAPI, client.app).state.container)
    calls = 0

    async def complete(role: ModelRole, system: str, user: str) -> str:
        nonlocal calls
        calls += 1
        item = (
            suggestion(requested_fields=["location"])
            if calls == 1
            else suggestion(source_refs=["wechat:" + "f" * 64])
        )
        return json.dumps({"items": [item]})

    monkeypatch.setattr(runtime.model_configurations, "complete", complete)
    response = client.post("/v1/personal-information/analyze", json=request_body(session))
    assert response.status_code == 502
    assert calls == 2


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


def test_context_evidence_is_citable_but_does_not_advance_old_progress(
    information_client: InformationClient,
) -> None:
    client, state, session, _ = information_client
    body = request_body(session)
    body["schema_version"] = "1.2"
    context_ref = "wechat:" + "e" * 64
    body["records"].insert(
        0,
        {
            **body["records"][0],
            "ref": context_ref,
            "context_only": True,
            "text": "原先计划周五",
            "sender_name": "合成联系人",
            "sender_ref": "f" * 64,
        },
    )
    state["result"] = {
        "items": [
            suggestion(
                kind="reference",
                details="原先计划周五，当前改为周一，仍待确认。",
                source_refs=[context_ref, REF],
            )
        ]
    }
    response = client.post("/v1/personal-information/analyze", json=body)
    assert response.status_code == 200
    assert response.json()["schema_version"] == "1.2"
    assert response.json()["processed_refs"] == [REF]
    assert response.json()["items"][0]["source_refs"] == [context_ref, REF]
    state["result"]["items"][0]["source_refs"] = [context_ref]
    assert client.post("/v1/personal-information/analyze", json=body).status_code == 502


def test_contextual_request_rejects_mixed_rooms_and_missing_summary(
    information_client: InformationClient,
) -> None:
    client, state, session, _ = information_client
    body = request_body(session)
    body["schema_version"] = "1.2"
    body["records"].append(
        {
            **body["records"][0],
            "ref": "wechat:" + "e" * 64,
            "room_ref": "d" * 64,
            "context_only": True,
        }
    )
    assert client.post("/v1/personal-information/analyze", json=body).status_code == 422
    assert state["calls"] == []
    body["records"].pop()
    assert client.post("/v1/personal-information/analyze", json=body).status_code == 502


def test_prior_knowledge_stays_background_and_keeps_review_state(
    information_client: InformationClient,
) -> None:
    client, state, session, _ = information_client
    body = request_body(session)
    body["schema_version"] = "1.2"
    body["prior_knowledge"] = [
        {
            "id": "earlier-candidate",
            "room_ref": ROOM,
            "kind": "memory",
            "summary": "先前提到的安排仍待核对，不能当成已确认承诺。",
            "state": "pending_review",
            "source_time_ms": 1791781199000,
        }
    ]
    state["result"] = {"items": [suggestion(details="本人明确承诺提交资料，具体日期尚待核对。")]}
    response = client.post("/v1/personal-information/analyze", json=body)
    assert response.status_code == 200
    assert response.json()["processed_refs"] == [REF]
    assert state["calls"][-1][2]["prior_knowledge"][0]["state"] == "pending_review"
    state["result"] = {"items": [suggestion(details="完整结论", source_refs=["earlier-candidate"])]}
    assert client.post("/v1/personal-information/analyze", json=body).status_code == 502


@pytest.mark.parametrize("outside", ["room", "future"])
def test_prior_knowledge_cannot_cross_room_or_read_future(
    information_client: InformationClient,
    outside: str,
) -> None:
    client, state, session, _ = information_client
    body = request_body(session)
    body["prior_knowledge"] = [
        {
            "id": "confirmed-memory",
            "room_ref": "d" * 64 if outside == "room" else ROOM,
            "kind": "memory",
            "summary": "已确认的历史信息",
            "state": "confirmed",
            "source_time_ms": 1791781200001 if outside == "future" else 1791781199000,
        }
    ]
    assert client.post("/v1/personal-information/analyze", json=body).status_code == 422
    assert state["calls"] == []


@pytest.mark.asyncio
async def test_parallel_rooms_are_bounded_and_cancellation_releases_room() -> None:
    import asyncio
    from typing import Literal

    from chatwaifu_runtime.information.contracts import AnalyzeInformationRequest
    from chatwaifu_runtime.information.service import (
        InformationUnavailable,
        PersonalInformationService,
    )

    entered = asyncio.Event()
    replacement = asyncio.Event()
    release = asyncio.Event()
    calls = 0

    class Models:
        def get(self, role: Literal["memory_extraction"]) -> ModelRoleConfig:
            return ModelRoleConfig(
                role=role,
                provider="openai_compatible",
                model="fixture",
                base_url="https://fixture.invalid/v1",
                updated_at=datetime.now(UTC),
            )

        async def complete(
            self,
            role: Literal["memory_extraction"],
            system: str,
            user: str,
        ) -> str:
            nonlocal calls
            calls += 1
            if calls == 4:
                entered.set()
            if calls == 5:
                replacement.set()
            await release.wait()
            return '{"items":[]}'

    class Owners:
        async def session_owner(self, session_id: str) -> str:
            return "local"

    service = PersonalInformationService(Models(), Owners())

    def body(room: int) -> AnalyzeInformationRequest:
        value = request_body(str(uuid4()))
        value["records"][0]["room_ref"] = str(room) * 64
        return AnalyzeInformationRequest.model_validate(value)

    tasks = [asyncio.create_task(service.analyze(body(i))) for i in range(4)]
    try:
        await asyncio.wait_for(entered.wait(), 2)
        assert service.status()["max_concurrent_batches"] == 4
        with pytest.raises(InformationUnavailable, match="information_analysis_busy"):
            await service.analyze(body(4))
        tasks[0].cancel()
        with pytest.raises(asyncio.CancelledError):
            await tasks[0]
        # A free global slot still cannot overtake another batch in the same room.
        with pytest.raises(InformationUnavailable, match="information_analysis_busy"):
            await service.analyze(body(1))
        tasks.append(asyncio.create_task(service.analyze(body(0))))
        await asyncio.wait_for(replacement.wait(), 2)
        assert calls == 5
    finally:
        release.set()
        await asyncio.gather(*tasks, return_exceptions=True)
    assert (await service.analyze(body(1))).processed_refs == [REF]
