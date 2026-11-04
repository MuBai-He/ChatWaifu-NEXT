"""Real HTTP/SQLite handoff, provenance, review and delegation boundaries."""

# pyright: reportPrivateUsage=false

import json
import time
from typing import Any, cast
from uuid import UUID, uuid4

import pytest
from chatwaifu_protocol.base import JsonObject
from chatwaifu_runtime.bootstrap.container import RuntimeContainer
from chatwaifu_runtime.providers.model_config import ModelRole
from fastapi import FastAPI
from fastapi.testclient import TestClient

ROOM = "a" * 64


def source(
    identifier: str, text: str = "我喜欢喝茶", owner: bool = True, revision: int = 0
) -> dict[str, Any]:
    return {
        "operation_id": str(uuid4()),
        "expected_revision": revision,
        "record": {
            "record_id": identifier,
            "kind": "source",
            "room_id": ROOM,
            "text": text,
            "occurred_at": int(time.time() * 1000) + 1,
            "sent_by_owner": owner,
            "sender_id": "contact:" + ("b" if owner else "c") * 64,
        },
    }


def push(
    c: TestClient,
    session: str,
    device: str,
    value: dict[str, Any],
    *,
    epoch: int = 0,
    policy_epoch: int = 0,
):
    return c.post(
        "/v1/personal-information/sync/push",
        json={
            "session_id": session,
            "device_id": device,
            "policy_epoch": policy_epoch,
            "schema_version": "1.1" if epoch else "1.0",
            "processing_epoch": epoch,
            "ingestion_mode": "live",
            "mutations": [value],
        },
    )


@pytest.fixture
def harness(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> tuple[TestClient, RuntimeContainer, str, str]:
    runtime = cast(RuntimeContainer, cast(FastAPI, client.app).state.container)
    assert client.portal is not None
    client.portal.call(runtime.harness.stop)
    session = client.post("/v1/sessions", json={}).json()["session_id"]
    device = str(uuid4())
    assert push(client, session, device, source("source:seed")).status_code == 200
    config = runtime.model_configurations.get

    def get(role: ModelRole):
        current = config(role)
        return (
            current.model_copy(update={"enabled": True, "provider": "openai_compatible"})
            if role == "memory_extraction"
            else current
        )

    async def complete(role: str, system: str, user: str) -> str:
        data = json.loads(user)
        message = next(r for r in data["records"] if not r["context_only"])
        return json.dumps(
            {
                "items": [
                    {
                        "title": "饮茶偏好",
                        "kind": "memory",
                        "importance": "low",
                        "reason": "原消息明确表达",
                        "source_refs": [message["ref"]],
                        "involvement": "self" if message["sent_by_owner"] else "other",
                        "details": message["text"],
                        "confidence": 0.95,
                        "sensitivity": "private",
                    }
                ]
            },
            ensure_ascii=False,
        )

    monkeypatch.setattr(runtime.model_configurations, "get", get)
    monkeypatch.setattr(runtime.model_configurations, "complete", complete)
    return client, runtime, session, device


def activate(
    c: TestClient, session: str, device: str, completed: list[dict[str, Any]] | None = None
):
    body = {
        "session_id": session,
        "device_id": device,
        "room_ids": [ROOM],
        "action": "prepare",
        "watermarks": {ROOM: int(time.time() * 1000)},
        "completed_sources": completed or [],
    }
    response = c.post("/v1/personal-information/processing", json=body)
    assert response.status_code == 200, response.text
    assert response.json()["epoch"] == 1
    # A retried/paged snapshot does not change the ownership generation.
    response = c.post("/v1/personal-information/processing", json={**body, "expected_epoch": 1})
    assert response.status_code == 200, response.text
    assert response.json()["epoch"] == 1
    response = c.post(
        "/v1/personal-information/processing",
        json={**body, "action": "activate", "expected_epoch": 1, "drained": True},
    )
    assert response.status_code == 200, response.text
    assert response.json()["epoch"] == 2


def test_handoff_preserves_done_progress_and_fences_an_old_client(
    harness: tuple[TestClient, RuntimeContainer, str, str],
) -> None:
    c, r, s, d = harness
    activate(c, s, d, [{"kind": "knowledge", "reference": "source:seed", "revision": 1}])
    assert c.portal is not None
    assert c.portal.call(r.harness.repository.claim) is None
    old = c.post(
        "/v1/personal-information/analyze",
        json={
            "session_id": s,
            "request_id": str(uuid4()),
            "records": [
                {
                    "ref": "wechat:" + "a" * 64,
                    "room_ref": ROOM,
                    "sent_by_owner": True,
                    "message_time_ms": 1,
                    "text": "我喜欢喝茶",
                }
            ],
        },
    )
    assert old.status_code == 503
    assert old.json()["detail"] == "information_processing_moved"
    wrong = push(c, s, d, source("source:late"), epoch=1)
    assert wrong.status_code == 409
    # No source or observation was committed by a stale execution owner.
    assert c.portal.call(r.knowledge.repository.get, "local", "source:late") is None


@pytest.mark.parametrize(
    ("acceptance", "relevance", "can_match_intent"),
    [("pending", "uncertain", False), ("pending", "current", False), ("human", "current", True)],
)
def test_intent_recheck_cannot_promote_aged_unconfirmed_work(
    harness: tuple[TestClient, RuntimeContainer, str, str],
    monkeypatch: pytest.MonkeyPatch,
    acceptance: str,
    relevance: str,
    can_match_intent: bool,
) -> None:
    from datetime import UTC, datetime

    from chatwaifu_protocol.harness import WorkItem

    c, runtime, session, device = harness
    old = source("source:old-todo", "历史上的合成任务")
    old["record"]["occurred_at"] = 1717570800000
    assert push(c, session, device, old).status_code == 200
    matched: list[str] = []

    async def intents(owner: str) -> list[Any]:
        matched.append(owner)
        return []

    monkeypatch.setattr(runtime.harness.repository, "intents", intents)
    work = WorkItem.model_validate(
        {
            "work_item_id": "work:old",
            "room_id": ROOM,
            "kind": "todo",
            "title": "历史任务",
            "involvement": "self",
            "state": "proposed",
            "acceptance": acceptance,
            "temporal_status": relevance,
            "evidence": [{"kind": "knowledge", "reference": "source:old-todo", "revision": 1}],
            "updated_at": datetime.now(UTC),
        }
    )
    assert c.portal is not None
    assert c.portal.call(runtime.harness.repository.save_work, work, 0)
    original = c.portal.call(runtime.knowledge.repository.get, "local", "source:old-todo")
    assert original is not None
    c.portal.call(runtime.harness._dispatch, work, original, UUID(session))
    assert bool(matched) == can_match_intent
    current = c.portal.call(runtime.harness.repository.work_item, work.work_item_id)
    assert current is not None and current.state == "proposed"
    assert current.acceptance == acceptance
    if not can_match_intent:
        assert current.temporal_status == "uncertain"


def test_runtime_extracts_grounded_memory_once_and_keeps_native_receipts(
    harness: tuple[TestClient, RuntimeContainer, str, str],
) -> None:
    c, r, s, d = harness
    activate(c, s, d)
    assert c.portal is not None
    c.portal.call(r.database.execute, "UPDATE harness_observations SET available_at=0", ())
    assert c.portal.call(r.harness.run_once)
    values = c.get("/v1/personal-assistant/work-items", params={"session_id": s}).json()["items"]
    assert len(values) == 1
    assert values[0]["acceptance"] == "policy"
    record = c.portal.call(
        r.knowledge.repository.get, "local", "harness:" + values[0]["work_item_id"]
    )
    assert record is not None and record.source_refs[0].record_id == "source:seed"
    assert record.origin["acceptance"] == "policy"
    assert not c.portal.call(r.harness.run_once)
    r.knowledge.enabled = True
    packet = c.portal.call(
        r.memory.retrieve_context, __import__("uuid").UUID(s), uuid4(), "default", "喝茶"
    )
    assert any(e.evidence.reference == record.record_id for e in packet.knowledge_evidence)


def test_simple_self_fact_uses_original_quote_instead_of_model_paraphrase(
    harness: tuple[TestClient, RuntimeContainer, str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    c, r, s, d = harness
    activate(c, s, d, [{"kind": "knowledge", "reference": "source:seed", "revision": 1}])

    async def complete(role: str, system: str, user: str) -> str:
        message = next(x for x in json.loads(user)["records"] if not x["context_only"])
        return json.dumps(
            {
                "items": [
                    {
                        "title": "咖啡偏好",
                        "kind": "memory",
                        "importance": "low",
                        "reason": "模型摘要不得改变原文事实",
                        "source_refs": [message["ref"]],
                        "involvement": "self",
                        "details": "用户长期喜欢喝浓咖啡。",
                        "confidence": 0.95,
                        "sensitivity": "private",
                    }
                ]
            },
            ensure_ascii=False,
        )

    monkeypatch.setattr(r.model_configurations, "complete", complete)
    assert push(c, s, d, source("source:quote", "我喜欢喝无糖茶。"), epoch=2).status_code == 200
    assert c.portal is not None
    c.portal.call(r.database.execute, "UPDATE harness_observations SET available_at=0", ())
    assert c.portal.call(r.harness.run_once)
    work = c.get("/v1/personal-assistant/work-items", params={"session_id": s}).json()["items"][0]
    assert work["acceptance"] == "policy"
    assert work["title"] == work["details"] == "我喜欢喝无糖茶。"
    assert work["memory_identity"]
    r.knowledge.enabled = True
    packet = c.portal.call(
        r.memory.retrieve_context, UUID(s), uuid4(), "default", "我之前告诉你的饮品偏好是什么？"
    )
    assert any("无糖茶" in e.text for e in packet.knowledge_evidence)
    corrected = c.post(
        "/v1/personal-assistant/work-items/" + work["work_item_id"],
        json={
            "session_id": s,
            "expected_revision": work["revision"],
            "action": "edit",
            "changes": {"details": "我不喜欢喝无糖茶。"},
        },
    )
    assert corrected.status_code == 200, corrected.text
    assert corrected.json()["title"] == corrected.json()["details"] == "我不喜欢喝无糖茶。"
    expanded = "我不喜欢喝无糖茶。" + "补充说明。" * 60
    edited = c.post(
        "/v1/personal-assistant/work-items/" + work["work_item_id"],
        json={
            "session_id": s,
            "expected_revision": corrected.json()["revision"],
            "action": "edit",
            "changes": {"details": expanded},
        },
    )
    assert edited.status_code == 200, edited.text
    assert edited.json()["details"] == expanded
    assert 0 < len(edited.json()["title"]) <= 256


@pytest.mark.parametrize("authorized", [False, True])
def test_unbound_owner_confirmation_persists_continuation_without_expanding_authority(
    harness: tuple[TestClient, RuntimeContainer, str, str],
    monkeypatch: pytest.MonkeyPatch,
    authorized: bool,
) -> None:
    from datetime import UTC, datetime

    from chatwaifu_protocol.agent import DecisionRecord
    from chatwaifu_protocol.harness import EvidenceRef, WorkItem

    c, r, session, _device = harness
    assert c.portal is not None
    intent_id = None
    if authorized:

        class Planner:
            async def decide(
                self, persona: str, situation: JsonObject, source_refs: frozenset[str]
            ) -> DecisionRecord:
                return DecisionRecord(
                    action="task",
                    reason="先补查课程依据",
                    source_refs=list(source_refs),
                    goal="只读补查课程时间，缺少事实则等待主人补充。",
                )

        def hold_task(task_id: UUID) -> None:
            return None

        monkeypatch.setattr(r.agent_tasks, "_launch", hold_task)
        r.harness.behavior.decision_factory = Planner
        intent = c.post(
            "/v1/agent/intents",
            json={
                "session_id": session,
                "goal": "跟进课程并补查必要依据",
                "room_ids": [ROOM],
                "allowed_skill_ids": ["knowledge.search"],
                "allow_writes": False,
                "shadow": False,
            },
        )
        assert intent.status_code == 201, intent.text
        intent_id = intent.json()["intent_id"]
    work = WorkItem(
        work_item_id="review:unbound-course",
        room_id=ROOM,
        source_room_ids=[ROOM],
        kind="event",
        title="课程同周三，需要补查",
        state="proposed",
        acceptance="pending",
        involvement="self",
        ingestion_mode="historical",
        evidence=[EvidenceRef(kind="knowledge", reference="source:seed", revision=1)],
        updated_at=datetime.now(UTC),
    )
    assert c.portal.call(r.harness.repository.save_work, work, 0)
    operation = str(uuid4())
    body = {
        "session_id": session,
        "operation_id": operation,
        "action": "confirm",
        "expected_revision": 1,
    }
    response = c.post("/v1/personal-assistant/work-items/" + work.work_item_id, json=body)
    assert response.status_code == 200, response.text
    assert response.json()["state"] == "waiting"
    assert response.json()["ingestion_mode"] == "historical"
    assert response.json()["owner_decision"]["operation_id"] == operation
    # The durable marker must survive a lost command receipt and must not depend
    # on the original chat staying active or on another source message arriving.
    c.portal.call(
        r.database.execute,
        "DELETE FROM harness_commands WHERE operation_id=?",
        (operation,),
    )
    c.portal.call(r.harness.reconcile)
    saved = c.get("/v1/personal-assistant/work-items", params={"session_id": session}).json()[
        "items"
    ][0]
    assert saved["owner_decision"]["pending"] is False
    if authorized:
        assert saved["task_id"] is not None
        task = c.portal.call(r.agent_tasks.repository.get, UUID(saved["task_id"]))
        assert task is not None and task.state.value == "queued"
        assert task.authorization.allow_writes is False
        assert task.authorization.calendar_ids == []
        assert str(task.authorization.intent_id) == intent_id
        definitions = r.runtime_skills.list()
        with monkeypatch.context() as changed_mode:
            changed_mode.setattr(
                r.runtime_skills,
                "list",
                lambda: [
                    definition.model_copy(
                        update={"execution_modes": ["interactive", "explicit_task"]}
                    )
                    for definition in definitions
                ],
            )
            with pytest.raises(PermissionError, match="capability_execution_mode_changed"):
                c.portal.call(r.harness.validate_task, task)
        # Reproduce interruption after creating the durable task but before
        # attaching it to the reviewed work. Recovery must reuse that task.
        orphan = WorkItem.model_validate(saved)
        assert orphan.owner_decision is not None
        orphan = orphan.model_copy(
            update={
                "task_id": None,
                "revision": orphan.revision + 1,
                "owner_decision": orphan.owner_decision.model_copy(update={"pending": True}),
            }
        )
        assert c.portal.call(r.harness.repository.save_work, orphan, saved["revision"])
        c.portal.call(r.harness.reconcile)
        recovered = c.get(
            "/v1/personal-assistant/work-items", params={"session_id": session}
        ).json()["items"][0]
        assert recovered["task_id"] == saved["task_id"]
    else:
        assert saved["state"] == "blocked" and saved["task_id"] is None
        assert "持续委托" in saved["waiting_reason"]
    replay = c.post("/v1/personal-assistant/work-items/" + work.work_item_id, json=body)
    assert replay.status_code == 200, replay.text
    assert replay.json()["task_id"] == saved["task_id"]
    conflict = c.post(
        "/v1/personal-assistant/work-items/" + work.work_item_id,
        json={**body, "changes": {"title": "不同的操作内容"}},
    )
    assert conflict.status_code == 409


def test_native_confirmed_review_checks_record_version_and_recovers_lost_receipt(
    harness: tuple[TestClient, RuntimeContainer, str, str],
) -> None:
    c, r, session, device = harness
    assert c.portal is not None
    value = source("native:course", "当天课程同周三，需要补查")
    value["record"].update(
        kind="event",
        confirmed=True,
        title="补课课程",
        origin={"involvement": "self", "native_work_revision": 1},
        source_refs=[{"record_id": "source:seed", "revision": 1}],
    )
    assert push(c, session, device, value).status_code == 200
    body = {
        "session_id": session,
        "operation_id": str(uuid4()),
        "record_id": "native:course",
        "expected_record_revision": 1,
    }
    stale = c.post(
        "/v1/personal-assistant/work-items/review-confirmed",
        json={**body, "expected_record_revision": 2},
    )
    assert stale.status_code == 409
    accepted = c.post("/v1/personal-assistant/work-items/review-confirmed", json=body)
    assert accepted.status_code == 200, accepted.text
    assert accepted.json()["owner_decision"]["operation_id"] == body["operation_id"]
    c.portal.call(r.harness.reconcile)
    # An acknowledged native record may be reprojected meanwhile. A replay still
    # returns the same command result, including after a lost final receipt.
    c.portal.call(
        r.database.execute,
        "DELETE FROM harness_commands WHERE operation_id=?",
        (body["operation_id"],),
    )
    replay = c.post("/v1/personal-assistant/work-items/review-confirmed", json=body)
    assert replay.status_code == 200, replay.text
    assert replay.json()["work_item_id"] == accepted.json()["work_item_id"]
    assert replay.json()["state"] == "blocked"
    changed = c.post(
        "/v1/personal-assistant/work-items/review-confirmed",
        json={**body, "record_id": "source:seed"},
    )
    assert changed.status_code == 409


@pytest.mark.parametrize("decision", ["retry", "reject"])
def test_failed_understanding_has_versioned_review_and_bounded_recovery(
    harness: tuple[TestClient, RuntimeContainer, str, str],
    monkeypatch: pytest.MonkeyPatch,
    decision: str,
) -> None:
    c, r, session, device = harness
    assert c.portal is not None
    activate(c, session, device, [{"kind": "knowledge", "reference": "source:seed", "revision": 1}])
    assert (
        push(c, session, device, source("source:failed-understanding"), epoch=2).status_code == 200
    )

    async def fail(request: object, **kwargs: object) -> None:
        raise ValueError("synthetic invalid extraction")

    monkeypatch.setattr(r.information, "understand", fail)
    for _ in range(5):
        c.portal.call(r.database.execute, "UPDATE harness_observations SET available_at=0", ())
        with pytest.raises(ValueError):
            c.portal.call(r.harness.run_once)
    failed = c.portal.call(r.harness.repository.failed_observations, "local")
    assert len(failed) == 1 and failed[0].attempts == 5
    assert failed[0].error_code == "ValueError"
    # New failure variants are negotiated; old clients keep their previous inbox shape.
    legacy = c.get("/v1/agent/review", params={"session_id": session}).json()["items"]
    assert not any(item["kind"] == "observation" for item in legacy)
    inbox = c.get(
        "/v1/agent/review", params={"session_id": session, "include_failures": True}
    ).json()["items"]
    item = next(row for row in inbox if row["kind"] == "observation")
    body = {
        "session_id": session,
        "decision": decision,
        "expected_revision": item["revision"],
        "operation_id": str(uuid4()),
    }
    path = "/v1/agent/review/observation/" + item["reference"]
    response = c.post(path, json=body)
    assert response.status_code == 200, response.text
    assert c.post(path, json=body).status_code == 200
    stale = c.post(path, json={**body, "operation_id": str(uuid4())})
    assert stale.status_code == 409
    assert c.portal.call(r.harness.repository.failed_observations, "local") == []

    async def current():
        row = await r.database.fetchone(
            "SELECT state,payload FROM harness_observations WHERE observation_id=?",
            (item["reference"],),
        )
        assert row is not None
        return row[0], json.loads(str(row[1]))

    state, payload = c.portal.call(current)
    assert state == ("pending" if decision == "retry" else "done")
    assert payload["attempts"] == (0 if decision == "retry" else 5)
    assert payload["revision"] > item["revision"]


def test_cancelled_understanding_releases_lease_without_spending_retry_budget(
    harness: tuple[TestClient, RuntimeContainer, str, str],
) -> None:
    c, r, session, device = harness
    assert c.portal is not None
    activate(c, session, device, [{"kind": "knowledge", "reference": "source:seed", "revision": 1}])
    assert push(c, session, device, source("source:interrupted"), epoch=2).status_code == 200
    for _ in range(6):
        c.portal.call(r.database.execute, "UPDATE harness_observations SET available_at=0", ())
        item = c.portal.call(r.harness.repository.claim)
        assert item is not None and item.attempts == 0
        c.portal.call(r.harness.repository.release_observation, item)
    assert c.portal.call(r.harness.repository.failed_observations, "local") == []


def test_unconfirmed_work_ages_without_a_new_message_and_confirmed_overdue_is_preserved(
    harness: tuple[TestClient, RuntimeContainer, str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from datetime import UTC, date, datetime, timedelta

    from chatwaifu_protocol.harness import EvidenceRef, WorkItem

    c, r, session, device = harness
    assert c.portal is not None
    before = datetime(2026, 11, 3, 15, 59, tzinfo=UTC)
    original = source("source:time-boundary", text="今天提交合成资料")
    original["record"]["occurred_at"] = int(before.timestamp() * 1000)
    assert push(c, session, device, original).status_code == 200
    work = WorkItem(
        work_item_id="work:unconfirmed-deadline",
        room_id=ROOM,
        kind="todo",
        title="今日截止的未确认事项",
        details="保留原有总结",
        state="proposed",
        due_date=date(2026, 11, 3),
        updated_at=before,
        evidence=[EvidenceRef(kind="knowledge", reference="source:time-boundary", revision=1)],
    )
    waiting = work.model_copy(
        update={"work_item_id": "work:unconfirmed-waiting", "state": "waiting"}
    )
    confirmed = work.model_copy(
        update={"work_item_id": "work:confirmed-overdue", "state": "open", "acceptance": "human"}
    )
    future = work.model_copy(
        update={"work_item_id": "work:future-arrangement", "due_date": date(2026, 11, 10)}
    )
    memory = work.model_copy(
        update={"work_item_id": "work:stable-memory", "kind": "memory", "due_date": None}
    )
    for item in [work, waiting, confirmed, future, memory]:
        assert c.portal.call(r.harness.repository.save_work, item, 0)

    async def no_model(*args: Any, **kwargs: Any) -> str:
        raise AssertionError("clock changes must not invoke a model")

    monkeypatch.setattr(r.model_configurations, "complete", no_model)
    from functools import partial

    c.portal.call(partial(r.harness.refresh_temporal_work, "local", now=before, force=True))
    initial = c.portal.call(r.harness.repository.work_item, work.work_item_id)
    assert initial is not None and initial.temporal_status == "current"
    from chatwaifu_runtime.agent import harness as harness_module

    class Clock(datetime):
        @classmethod
        def now(cls, tz: Any = None) -> datetime:
            value = before + timedelta(minutes=2)
            return value if tz is None else value.astimezone(tz)

    monkeypatch.setattr(harness_module, "datetime", Clock)
    page = c.get("/v1/personal-assistant/work-items", params={"session_id": session})
    assert page.status_code == 200, page.text
    assert (
        next(item for item in page.json()["items"] if item["work_item_id"] == work.work_item_id)[
            "temporal_status"
        ]
        == "historical"
    )
    expired = c.portal.call(r.harness.repository.work_item, work.work_item_id)
    assert expired is not None and expired.temporal_status == "historical"
    assert (
        expired.state == "proposed"
        and expired.acceptance == "pending"
        and expired.details == "保留原有总结"
    )
    pending_wait = c.portal.call(r.harness.repository.work_item, waiting.work_item_id)
    assert (
        pending_wait is not None
        and pending_wait.temporal_status == "historical"
        and pending_wait.state == "waiting"
    )
    revision = expired.revision
    c.portal.call(
        partial(
            r.harness.refresh_temporal_work, "local", now=before + timedelta(minutes=4), force=True
        )
    )
    unchanged = c.portal.call(r.harness.repository.work_item, work.work_item_id)
    assert unchanged is not None and unchanged.revision == revision
    for item in [confirmed, future, memory]:
        saved = c.portal.call(r.harness.repository.work_item, item.work_item_id)
        assert (
            saved is not None and saved.temporal_status == "current" and saved.state == item.state
        )
        assert saved.revision == item.revision


def test_default_reminders_preserve_explicit_choice_and_do_not_guess_date_only_times(
    harness: tuple[TestClient, RuntimeContainer, str, str],
) -> None:
    from datetime import UTC, date, datetime, timedelta

    from chatwaifu_protocol.harness import EvidenceRef, StandingIntent, WorkItem

    c, r, session, device = harness
    assert c.portal is not None
    settings = {
        "session_id": session,
        "goal": "负责该来源的个人事项和提醒",
        "room_ids": [ROOM],
        "allowed_skill_ids": ["knowledge.search"],
        "allow_writes": True,
        "shadow": False,
        "reminder_defaults": {
            "event_offset_seconds": 300,
            "todo_offset_seconds": 600,
            "primary_device_id": device,
        },
    }
    reply = c.post("/v1/agent/intents", json=settings)
    assert reply.status_code == 201, reply.text
    intent = StandingIntent.model_validate(reply.json())
    start = datetime.now(UTC) + timedelta(days=1)
    work = WorkItem(
        work_item_id="work:default-reminder",
        room_id=ROOM,
        kind="event",
        title="合成会议",
        state="proposed",
        evidence=[EvidenceRef(kind="knowledge", reference="source:seed", revision=1)],
        involvement="self",
        starts_at=start,
        ends_at=start + timedelta(hours=1),
        updated_at=datetime.now(UTC),
    )
    assert c.portal.call(r.harness.repository.save_work, work, 0)
    assert (
        c.portal.call(
            r.harness._apply_reminder_defaults,
            work,
            intent.model_copy(update={"reminder_defaults": None}),
        )
        == work
    )
    declined = work.model_copy(update={"reminder_policy": "none"})
    assert c.portal.call(r.harness._apply_reminder_defaults, declined, intent) == declined
    explicit = work.model_copy(update={"remind_at": start - timedelta(seconds=900)})
    assert c.portal.call(r.harness._apply_reminder_defaults, explicit, intent) == explicit
    configured = c.portal.call(r.harness._apply_reminder_defaults, work, intent)
    assert configured is not None
    assert configured.remind_at == start - timedelta(seconds=300)
    assert str(configured.primary_device_id) == device
    changed = c.post(
        "/v1/personal-assistant/work-items/" + work.work_item_id,
        json={
            "session_id": session,
            "expected_revision": configured.revision,
            "action": "edit",
            "changes": {
                "starts_at": (start + timedelta(hours=1)).isoformat(),
                "ends_at": (start + timedelta(hours=2)).isoformat(),
            },
        },
    )
    assert changed.status_code == 200, changed.text
    moved = WorkItem.model_validate(changed.json())
    assert moved.remind_at == start + timedelta(hours=1) - timedelta(seconds=300)
    cleared = c.post(
        "/v1/personal-assistant/work-items/" + work.work_item_id,
        json={
            "session_id": session,
            "expected_revision": moved.revision,
            "action": "edit",
            "changes": {"remind_at": None},
        },
    )
    assert cleared.status_code == 200, cleared.text
    disabled = WorkItem.model_validate(cleared.json())
    assert disabled.reminder_policy == "none" and disabled.remind_at is None
    assert c.portal.call(r.harness._apply_reminder_defaults, disabled, intent) == disabled
    todo = work.model_copy(
        update={
            "work_item_id": "work:date-only-default",
            "kind": "todo",
            "starts_at": None,
            "ends_at": None,
            "due_date": date.today() + timedelta(days=1),
        }
    )
    assert c.portal.call(r.harness.repository.save_work, todo, 0)
    date_only = c.portal.call(r.harness._apply_reminder_defaults, todo, intent)
    assert date_only is not None
    assert (
        date_only.reminder_rule == "relative"
        and date_only.remind_at is None
        and date_only.due_at is None
    )
    assert c.get("/v1/agent/tasks", params={"session_id": session}).json()["items"] == []


def test_conflicting_reminder_defaults_wait_and_unknown_device_is_rejected(
    harness: tuple[TestClient, RuntimeContainer, str, str],
) -> None:
    from datetime import UTC, datetime

    from chatwaifu_protocol.harness import EvidenceRef, StandingIntent, WorkItem

    c, r, session, device = harness
    assert c.portal is not None
    body: dict[str, Any] = {
        "session_id": session,
        "goal": "合成默认提醒",
        "room_ids": [ROOM],
        "allowed_skill_ids": ["knowledge.search"],
        "allow_writes": True,
        "shadow": False,
        "reminder_defaults": {"event_offset_seconds": 300, "primary_device_id": str(uuid4())},
    }
    assert c.post("/v1/agent/intents", json=body).status_code == 409
    body["reminder_defaults"]["primary_device_id"] = device
    first = c.post("/v1/agent/intents", json=body)
    assert first.status_code == 201, first.text
    body["reminder_defaults"]["event_offset_seconds"] = 900
    assert c.post("/v1/agent/intents", json=body).status_code == 201
    work = WorkItem(
        work_item_id="work:conflicting-defaults",
        room_id=ROOM,
        kind="event",
        title="合成待核对会议",
        state="proposed",
        evidence=[EvidenceRef(kind="knowledge", reference="source:seed", revision=1)],
        updated_at=datetime.now(UTC),
    )
    assert c.portal.call(r.harness.repository.save_work, work, 0)
    assert (
        c.portal.call(
            r.harness._apply_reminder_defaults, work, StandingIntent.model_validate(first.json())
        )
        is None
    )
    blocked = c.portal.call(r.harness.repository.work_item, work.work_item_id)
    assert blocked is not None and blocked.state == "blocked" and blocked.remind_at is None
    assert "不同的默认提醒规则" in (blocked.waiting_reason or "")


@pytest.mark.parametrize("revoke_context", [False, True])
def test_missing_facts_use_bounded_shared_context_with_real_evidence(
    harness: tuple[TestClient, RuntimeContainer, str, str],
    monkeypatch: pytest.MonkeyPatch,
    revoke_context: bool,
) -> None:
    c, r, session, device = harness
    assert c.portal is not None
    monkeypatch.setattr(r.knowledge, "enabled", True)
    extra = source("source:wednesday", text="周三课表:课程资料见原文。")
    extra["record"]["room_id"] = "e" * 64
    assert push(c, session, device, extra).status_code == 200
    activate(c, session, device, [{"kind": "knowledge", "reference": "source:seed", "revision": 1}])
    assert (
        push(
            c,
            session,
            device,
            source("source:seed", text="查一下周三课表资料", revision=1),
            epoch=2,
        ).status_code
        == 200
    )
    calls: list[dict[str, Any]] = []

    async def complete(role: str, system: str, user: str) -> str:
        data = json.loads(user)
        calls.append(data)
        if not data.get("context"):
            record = next(item for item in data["records"] if not item["context_only"])
            return json.dumps(
                {
                    "items": [
                        {
                            "kind": "reference",
                            "title": "周三课表待核对",
                            "importance": "medium",
                            "reason": "需要补充资料",
                            "source_refs": [record["ref"]],
                            "involvement": "self",
                            "details": "需要查找周三课表资料。",
                        }
                    ],
                    "queries": [{"question": "周三课表"}, {"question": "周三课表"}],
                }
            )
        ref = next(
            item["evidence"]
            for item in data["context"]
            if item["evidence"]["reference"] == "source:wednesday"
        )
        if revoke_context:
            from chatwaifu_runtime.knowledge.contracts import Mutation

            await r.knowledge.repository.apply_mutation(
                "local",
                Mutation.model_validate(
                    {
                        **extra,
                        "operation_id": str(uuid4()),
                        "action": "forget",
                        "expected_revision": 1,
                    }
                ),
            )
        record = next(item for item in data["records"] if not item["context_only"])
        return json.dumps(
            {
                "items": [
                    {
                        "kind": "reference",
                        "title": "周三课表资料",
                        "importance": "medium",
                        "reason": "已找到原文",
                        "source_refs": [record["ref"]],
                        "involvement": "self",
                        "details": "课程资料见原文。",
                        "context_refs": [ref],
                    }
                ],
                "queries": [],
            },
            ensure_ascii=False,
        )

    monkeypatch.setattr(r.model_configurations, "complete", complete)
    c.portal.call(r.database.execute, "UPDATE harness_observations SET available_at=0", ())
    if revoke_context:
        with pytest.raises(ValueError, match="understanding_context_changed"):
            c.portal.call(r.harness.run_once)
        assert len(calls) == 2
        assert c.portal.call(r.harness.repository.work_items, "local") == []
        return
    assert c.portal.call(r.harness.run_once)
    assert len(calls) == 2
    work = c.portal.call(r.harness.repository.work_items, "local")[0]
    assert any(ref.reference == "source:wednesday" and ref.revision == 1 for ref in work.evidence)
    assert work.task_id is None and work.bindings == []
    entries = c.get("/v1/agent/activity", params={"session_id": session}).json()["items"]
    lookup = next(item for item in entries if item["kind"] == "understanding.lookup")
    assert lookup["details"]["queries"] == 1 and lookup["details"]["model_passes"] == 2


def test_activity_survives_ordinary_updates_and_hides_only_revoked_sources(
    harness: tuple[TestClient, RuntimeContainer, str, str],
) -> None:
    from chatwaifu_protocol.harness import EvidenceRef

    c, r, session, device = harness
    assert c.portal is not None
    second = source("source:second")
    assert push(c, session, device, second).status_code == 200
    for key in ("source:seed", "source:second"):
        c.portal.call(
            r.harness.repository.record,
            "local",
            "action.event",
            key,
            "verified",
            "合成日程已核验",
            [EvidenceRef(kind="knowledge", reference=key, revision=1)],
        )

    def activity():
        return c.get("/v1/agent/activity", params={"session_id": session}).json()

    before = activity()
    assert push(c, session, device, source("source:ordinary-update")).status_code == 200
    assert activity()["context_epoch"] == before["context_epoch"]
    ids = {
        item["reference"]: item["sequence"]
        for item in before["items"]
        if item["kind"] == "action.event"
    }
    request = {"session_id": session, "sequences": list(ids.values())}
    checked = c.post("/v1/agent/activity/validate", json=request)
    assert checked.status_code == 200, checked.text
    assert set(checked.json()["valid_sequences"]) == set(ids.values())
    forgotten = source("source:seed")
    forgotten.update(action="forget", expected_revision=1)
    assert push(c, session, device, forgotten).status_code == 200
    after = activity()
    assert after["context_epoch"] != before["context_epoch"]
    entries = {item["reference"]: item for item in after["items"] if item["kind"] == "action.event"}
    assert entries["source:seed"]["redacted"] is True
    assert entries["source:seed"]["evidence"] == [] and entries["source:seed"]["details"] == {}
    assert entries["source:second"]["redacted"] is False
    validated = c.post("/v1/agent/activity/validate", json=request).json()
    assert validated["valid_sequences"] == [ids["source:second"]]
    changed = source("source:second", text="合成原文修订", revision=1)
    updated = push(
        c, session, device, changed, policy_epoch=int(after["context_epoch"].rsplit(":", 1)[1])
    )
    assert updated.status_code == 200, updated.text
    assert activity()["context_epoch"] == after["context_epoch"]
    assert c.post("/v1/agent/activity/validate", json=request).json()["valid_sequences"] == []
    revised_entries = {
        item["reference"]: item for item in activity()["items"] if item["kind"] == "action.event"
    }
    assert revised_entries["source:second"]["redacted"] is False


def test_third_party_is_reviewed_and_forgotten_evidence_cannot_be_confirmed(
    harness: tuple[TestClient, RuntimeContainer, str, str],
) -> None:
    c, r, s, d = harness
    activate(c, s, d, [{"kind": "knowledge", "reference": "source:seed", "revision": 1}])
    value = source("source:other", "小王喜欢喝茶", False)
    assert push(c, s, d, value, epoch=2).status_code == 200
    assert c.portal is not None
    c.portal.call(r.database.execute, "UPDATE harness_observations SET available_at=0", ())
    assert c.portal.call(r.harness.run_once)
    item = c.get("/v1/personal-assistant/work-items", params={"session_id": s}).json()["items"][0]
    assert item["acceptance"] == "pending"
    forgotten = {**value, "operation_id": str(uuid4()), "expected_revision": 1, "action": "forget"}
    assert push(c, s, d, forgotten, epoch=2).status_code == 200
    response = c.post(
        "/v1/personal-assistant/work-items/" + item["work_item_id"],
        json={"session_id": s, "expected_revision": item["revision"], "action": "confirm"},
    )
    assert response.status_code == 409


def test_standing_authority_is_versioned_and_cannot_enable_unreviewed_tools(
    harness: tuple[TestClient, RuntimeContainer, str, str],
) -> None:
    c, _r, s, _d = harness
    denied = c.post(
        "/v1/agent/intents",
        json={
            "session_id": s,
            "goal": "跟进课程",
            "room_ids": [ROOM],
            "allowed_skill_ids": ["agent.assist"],
        },
    )
    assert denied.status_code == 403
    response = c.post(
        "/v1/agent/intents",
        json={
            "session_id": s,
            "goal": "跟进课程",
            "room_ids": [ROOM],
            "allowed_skill_ids": ["knowledge.search"],
        },
    )
    assert response.status_code == 201, response.text
    item = response.json()
    assert item["shadow"]
    action = {"session_id": s, "expected_revision": 1, "action": "pause"}
    paused = c.post("/v1/agent/intents/" + item["intent_id"], json=action)
    assert paused.status_code == 200
    assert paused.json()["state"] == "paused"
    assert c.post("/v1/agent/intents/" + item["intent_id"], json=action).status_code == 409


def test_handoff_requires_drained_native_leases(
    harness: tuple[TestClient, RuntimeContainer, str, str],
) -> None:
    c, _, s, d = harness
    response = c.post(
        "/v1/personal-information/processing",
        json={
            "session_id": s,
            "device_id": d,
            "room_ids": [ROOM],
            "action": "activate",
            "expected_epoch": 1,
        },
    )
    assert response.status_code == 422


@pytest.mark.parametrize("binding_recheck", [False, True])
def test_live_source_uses_existing_task_kernel_and_stable_operation_id(
    harness: tuple[TestClient, RuntimeContainer, str, str],
    monkeypatch: pytest.MonkeyPatch,
    binding_recheck: bool,
) -> None:
    import asyncio
    from datetime import UTC, datetime, timedelta

    from chatwaifu_protocol.agent import DecisionRecord
    from chatwaifu_runtime.providers.contracts import (
        LlmResponseCompleted,
        LlmTextDelta,
        LlmToolCall,
        LlmToolCallRequested,
    )
    from chatwaifu_runtime.runtime_skills.execution_context import authorized_action

    c, r, s, d = harness

    account = str(UUID(int=1))
    calendar_scope = account + "/calendar"

    async def resources(session: UUID):
        return {"calendar_ids": [calendar_scope], "tasklist_ids": []}

    monkeypatch.setattr(r.harness, "resources", resources)
    activate(c, s, d, [{"kind": "knowledge", "reference": "source:seed", "revision": 1}])
    intent = c.post(
        "/v1/agent/intents",
        json={
            "session_id": s,
            "goal": "维护我的日程",
            "room_ids": [ROOM],
            "allowed_skill_ids": ["agenda.manage"],
            "calendar_ids": [calendar_scope],
            "allow_writes": True,
            "shadow": False,
        },
    )
    assert intent.status_code == 201, intent.text
    start = datetime.now(UTC) + timedelta(days=1)
    end = start + timedelta(hours=1)

    async def complete(role: str, system: str, user: str) -> str:
        message = next(value for value in json.loads(user)["records"] if not value["context_only"])
        return json.dumps(
            {
                "items": [
                    {
                        "title": "开会",
                        "kind": "event",
                        "importance": "high",
                        "reason": "明确日程",
                        "source_refs": [message["ref"]],
                        "involvement": "self",
                        "details": "明天开会",
                        "starts_at": start.isoformat(),
                        "ends_at": end.isoformat(),
                    }
                ]
            }
        )

    async def decide(persona: str, situation: Any, refs: frozenset[str]):
        assert "task" in situation["available_actions"]
        assert situation["task"]["goal"] == "维护我的日程"
        assert situation["standing_authority"]["source_in_scope"] is True
        return DecisionRecord(
            action="task", goal="创建日程", reason="主人已委托维护日程", source_refs=list(refs)
        )

    monkeypatch.setattr(r.model_configurations, "complete", complete)
    monkeypatch.setattr(r.behavior_decisions, "decide", decide)
    calls: list[UUID | None] = []

    async def manage(session: str, arguments: Any) -> dict[str, Any]:
        assert arguments["account_id"] == account
        assert arguments["collection_id"] == "calendar"
        calls.append(authorized_action.get())
        return {
            "provider": "google",
            "verified": True,
            "state": "saved",
            "item": {
                "id": "verified-event",
                "title": arguments["title"],
                "start": {"timestamp": start.isoformat()},
                "end": {"timestamp": end.isoformat()},
            },
        }

    monkeypatch.setitem(r.runtime_skills._builtin._session_handlers, "agenda_manage", manage)

    def available(skill: str, name: str) -> None:
        return None

    monkeypatch.setattr(r.capabilities, "_availability", available)

    class AgendaModel:
        kind = "scripted"
        supports_tool_calling = True
        phase = 0

        async def stream(self, request: Any):
            tool_finish = (
                any(tool.name == "verify_completion" for tool in request.tools) or self.phase < 2
            )
            if any(tool.name == "verify_completion" for tool in request.tools):
                operations = json.loads(request.user_text)["operations"]
                yield LlmToolCallRequested(
                    LlmToolCall(
                        "verify",
                        "verify_completion",
                        {
                            "complete": True,
                            "evidence_keys": [operations[-1]["step_key"]],
                            "remaining_work": "",
                        },
                    )
                )
            elif self.phase == 0:
                self.phase += 1
                yield LlmToolCallRequested(
                    LlmToolCall(
                        "activate",
                        "activate_capabilities",
                        {"capability_ids": ["agenda.manage/execute"]},
                    )
                )
            elif self.phase == 1:
                self.phase += 1
                name = next(
                    tool.name
                    for tool in request.tools
                    if "action" in tool.input_schema.get("properties", {})
                )
                yield LlmToolCallRequested(
                    LlmToolCall(
                        "create",
                        name,
                        {
                            "action": "create",
                            "kind": "calendar",
                            "title": "开会",
                            "start": start.isoformat(),
                            "end": end.isoformat(),
                        },
                    )
                )
            else:
                yield LlmTextDelta("日程已建立并核验。")
            yield LlmResponseCompleted("tool_calls" if tool_finish else "stop")

    model = AgendaModel()
    r.agent_tasks.model_factory = lambda: model
    assert push(c, s, d, source("source:live-event", "明天开会"), epoch=2).status_code == 200
    assert c.portal is not None

    async def run() -> None:
        await r.database.execute("UPDATE harness_observations SET available_at=0")
        assert await r.harness.run_once()
        rows = await r.harness.repository.work_items("local")
        assert len(rows) == 1 and rows[0].task_id
        worker = r.agent_tasks._workers.get(rows[0].task_id)
        if worker:
            async with asyncio.timeout(5):
                await worker
        pending = await r.harness.repository.work_item(rows[0].work_item_id)
        assert pending is not None and pending.state == "waiting" and pending.bindings
        if binding_recheck:
            # A later native readback of the same object supersedes its write
            # receipt. Replay both durable facts, as restart reconciliation does.
            receipt: JsonObject = {
                **pending.bindings[0],
                "state": "matched",
                "verification_source": "provider_readback",
            }
            assert await r.harness.repository.bind_work(pending, receipt)
            pending = await r.harness.repository.work_item(pending.work_item_id)
            assert pending is not None
            original = (await r.agent_tasks.repository.steps(rows[0].task_id))[-1]
            proof = cast(JsonObject, json.loads(json.dumps(original)))
            proof.update(
                step_key="fixture:binding-readback",
                skill_id="work.match",
                action_id=str(uuid4()),
                state="settled",
            )
            snapshot = cast(JsonObject, proof["snapshot"])
            result = cast(JsonObject, snapshot["result"])
            result["data"] = receipt
            await r.agent_tasks.repository.begin_step(
                rows[0].task_id, "fixture:binding-readback", proof
            )
            await r.agent_tasks.repository.finish_step(
                rows[0].task_id, "fixture:binding-readback", proof
            )
        await r.harness.reconcile()
        task = await r.agent_tasks.repository.get(rows[0].task_id)
        assert task is not None
        assert task.state.value == "succeeded", task.blocked_reason
        updated = await r.harness.repository.work_item(rows[0].work_item_id)
        assert updated is not None and updated.bindings[0]["verified"] is True
        assert updated.state == "open" and updated.revision == pending.revision
        assert len(updated.bindings) == 1
        if binding_recheck:
            assert updated.bindings[0]["state"] == "matched"
        activity_before = await r.harness.repository.activity("local")
        # Budget accounting can land after a consumer has observed success. It must
        # not invalidate the target revision held by a following change proposal.
        await r.agent_tasks.update_checkpoint(task.task_id, active_seconds=task.active_seconds + 1)
        await r.harness.reconcile()
        stable = await r.harness.repository.work_item(updated.work_item_id)
        assert stable is not None and stable.revision == updated.revision
        assert stable.bindings == updated.bindings
        assert await r.harness.repository.activity("local") == activity_before
        assert not await r.harness.run_once()

    c.portal.call(run)
    assert len(calls) == 1 and calls[0] is not None


def test_bound_edit_is_a_persistent_proposal_not_an_unverified_local_change(
    harness: tuple[TestClient, RuntimeContainer, str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    from datetime import UTC, datetime, timedelta

    from chatwaifu_protocol.harness import EvidenceRef, WorkItem

    c, r, s, _ = harness
    assert c.portal is not None

    def hold(identifier: UUID) -> None:
        pass

    monkeypatch.setattr(r.agent_tasks, "_launch", hold)
    start = datetime.now(UTC) + timedelta(days=1)
    work = WorkItem(
        work_item_id="work:bound",
        owner="local",
        room_id=ROOM,
        kind="event",
        title="会议",
        state="open",
        acceptance="human",
        starts_at=start,
        ends_at=start + timedelta(hours=1),
        evidence=[EvidenceRef(kind="knowledge", reference="source:seed", revision=1)],
        bindings=[
            {
                "provider": "google",
                "account_id": str(uuid4()),
                "collection_id": "selected",
                "item": {"id": "event", "etag": "original"},
            }
        ],
        updated_at=datetime.now(UTC),
    )
    assert c.portal.call(r.harness.repository.save_work, work, 0)
    body = {
        "session_id": s,
        "operation_id": str(uuid4()),
        "expected_revision": 1,
        "action": "edit",
        "changes": {"title": "新会议"},
    }
    response = c.post("/v1/personal-assistant/work-items/work:bound", json=body)
    assert response.status_code == 200, response.text
    proposal = response.json()
    assert proposal["target_id"] == work.work_item_id
    assert proposal["state"] == "waiting"
    canonical = c.portal.call(r.harness.repository.work_item, work.work_item_id)
    assert canonical is not None and canonical.title == "会议"
    task = c.portal.call(r.agent_tasks.repository.get, UUID(proposal["task_id"]))
    assert task is not None and task.authorization.intent_id is None
    assert (
        c.post("/v1/personal-assistant/work-items/work:bound", json=body).json()["task_id"]
        == proposal["task_id"]
    )

    async def reject_target():
        with pytest.raises(PermissionError, match="work_external_binding_required"):
            await r.harness.validate_operation(
                task,
                {
                    "kind": "calendar",
                    "action": "update",
                    "item_id": "wrong",
                    "account_id": work.bindings[0]["account_id"],
                    "collection_id": "selected",
                    "start": start.isoformat(),
                    "end": (start + timedelta(hours=1)).isoformat(),
                },
            )

    c.portal.call(reject_target)


def test_future_task_resume_keeps_its_durable_wake(
    harness: tuple[TestClient, RuntimeContainer, str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    from datetime import UTC, datetime, timedelta

    from chatwaifu_protocol.agent import AgentTaskAction, AgentTaskCreate, TaskAuthorization

    c, r, s, _ = harness
    assert c.portal is not None
    launches: list[UUID] = []
    monkeypatch.setattr(r.agent_tasks, "_launch", launches.append)

    async def exercise():
        when = datetime.now(UTC) + timedelta(hours=2)
        task = await r.agent_tasks.create(
            AgentTaskCreate(
                session_id=UUID(s),
                goal="在指定时刻检查",
                authorization=TaskAuthorization(
                    source_ref="owner",
                    allowed_skill_ids=[],
                    expires_at=when + timedelta(hours=1),
                    scheduled_for=when,
                ),
            ),
            wake_at=when,
        )
        task = await r.agent_tasks.action(
            task.task_id, UUID(s), AgentTaskAction(action="pause", expected_revision=task.revision)
        )
        task = await r.agent_tasks.action(
            task.task_id, UUID(s), AgentTaskAction(action="resume", expected_revision=task.revision)
        )
        assert task.state.value == "waiting_event"
        assert task.wake_at == when
        assert launches == []

    c.portal.call(exercise)


def test_cancelled_unknown_operation_is_read_back_without_restarting_goal(
    harness: tuple[TestClient, RuntimeContainer, str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    from datetime import UTC, datetime, timedelta

    from chatwaifu_protocol.agent import AgentTaskAction, AgentTaskCreate, TaskAuthorization

    c, r, s, _ = harness
    assert c.portal is not None
    launches: list[UUID] = []
    monkeypatch.setattr(r.agent_tasks, "_launch", launches.append)

    async def recover(task: Any, step: Any):
        return {"provider": "google", "verified": True, "item": {"id": "actual"}}

    monkeypatch.setattr(r.agent_tasks, "operation_recoverer", recover)

    async def exercise():
        task = await r.agent_tasks.create(
            AgentTaskCreate(
                session_id=UUID(s),
                goal="一次操作",
                authorization=TaskAuthorization(
                    source_ref="owner", expires_at=datetime.now(UTC) + timedelta(hours=1)
                ),
            )
        )
        task = await r.agent_tasks.action(
            task.task_id, UUID(s), AgentTaskAction(action="cancel", expected_revision=task.revision)
        )
        await r.agent_tasks.repository.begin_step(
            task.task_id,
            "lost",
            {
                "step_key": "lost",
                "state": "unknown",
                "side_effect": "write",
                "skill_id": "agenda.manage",
                "ok": False,
                "effect_possible": True,
            },
        )
        launches.clear()
        assert await r.agent_tasks.recover_uncertain(task.task_id, audit_only=True)
        latest = await r.agent_tasks.repository.get(task.task_id)
        assert latest is not None and latest.state.value == "cancelled"
        assert launches == []
        assert (await r.agent_tasks.repository.steps(task.task_id))[0]["audit_only"] is True

    c.portal.call(exercise)


def test_change_proposal_never_schedules_a_second_reminder_for_the_same_work(
    harness: tuple[TestClient, RuntimeContainer, str, str],
) -> None:
    from datetime import UTC, datetime, timedelta

    from chatwaifu_protocol.harness import EvidenceRef, WorkItem

    c, r, _, d = harness
    assert c.portal is not None

    async def exercise() -> None:
        now = datetime.now(UTC)
        base = WorkItem(
            work_item_id="work:canonical",
            owner="local",
            room_id=ROOM,
            kind="event",
            title="已有课程",
            details="已核验的同一个日历对象",
            state="open",
            revision=1,
            acceptance="human",
            primary_device_id=UUID(d),
            remind_at=now + timedelta(minutes=10),
            evidence=[EvidenceRef(kind="knowledge", reference="source:seed", revision=1)],
            updated_at=now,
        )
        change = base.model_copy(
            update={
                "work_item_id": "work:change",
                "target_id": base.work_item_id,
                "expected_revision": 1,
                "operation": "update",
                "state": "waiting",
            }
        )
        assert await r.harness.repository.save_work(base, 0)
        assert await r.harness.repository.save_work(change, 0)
        await r.harness.publish_work(base)
        await r.harness.publish_work(change)
        canonical = await r.knowledge.repository.get("local", "harness:work:canonical")
        proposal = await r.knowledge.repository.get("local", "harness:work:change")
        assert canonical is not None and canonical.remind_at is not None
        assert proposal is not None and proposal.remind_at is None
        assert proposal.origin["reminder_occurrence"] is None
        occurrences = await r.harness.repository.reminder_status("local")
        assert [o["work_item_id"] for o in occurrences if o["state"] == "scheduled"] == [
            base.work_item_id
        ]

    c.portal.call(exercise)


def test_newer_metadata_does_not_present_the_same_reminder_twice(
    harness: tuple[TestClient, RuntimeContainer, str, str],
) -> None:
    from datetime import UTC, datetime, timedelta

    from chatwaifu_protocol.harness import EvidenceRef, WorkItem
    from chatwaifu_runtime.persistence.harness_reminders import native_receipt_in

    c, r, _, d = harness
    assert c.portal is not None

    async def exercise():
        work = WorkItem(
            work_item_id="work:reminder",
            owner="local",
            room_id=ROOM,
            kind="todo",
            title="交作业",
            state="open",
            acceptance="human",
            primary_device_id=UUID(d),
            remind_at=datetime.now(UTC) + timedelta(hours=1),
            evidence=[EvidenceRef(kind="knowledge", reference="source:seed", revision=1)],
            updated_at=datetime.now(UTC),
        )
        occurrence = await r.harness.repository.reminder(work)
        assert occurrence is not None
        async with r.database.transaction() as connection:
            await native_receipt_in(
                connection,
                "local",
                uuid4(),
                {
                    "occurrence_id": occurrence["occurrence_id"],
                    "state": "presented",
                    "observed_at": datetime.now(UTC).isoformat(),
                },
            )
        unchanged = await r.harness.repository.reminder(work)
        assert unchanged is not None and unchanged["state"] == "scheduled"
        async with r.database.transaction() as connection:
            await native_receipt_in(
                connection,
                "local",
                UUID(d),
                {
                    "occurrence_id": occurrence["occurrence_id"],
                    "state": "presented",
                    "observed_at": datetime.now(UTC).isoformat(),
                },
            )
        changed = await r.harness.repository.reminder(
            work.model_copy(update={"revision": 2, "title": "提交作业"})
        )
        assert (
            changed is not None
            and changed["occurrence_id"] == occurrence["occurrence_id"]
            and changed["state"] == "presented"
        )

    c.portal.call(exercise)


def test_provider_change_updates_matter_and_relative_reminder_without_a_model(
    harness: tuple[TestClient, RuntimeContainer, str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    from datetime import UTC, datetime, timedelta

    from chatwaifu_protocol.base import JsonObject
    from chatwaifu_protocol.harness import EvidenceRef, WorkItem
    from chatwaifu_runtime.agent.calendar_observer import CalendarObserver

    c, r, _, d = harness
    assert c.portal is not None

    async def forbidden(*args: Any, **kwargs: Any):
        raise AssertionError("Deterministic reconciliation must not invoke a model")

    monkeypatch.setattr(r.model_configurations, "complete", forbidden)

    async def exercise():
        start = datetime.now(UTC) + timedelta(days=1)
        binding: JsonObject = {
            "provider": "google",
            "account_id": str(uuid4()),
            "collection_id": "selected",
            "item": {
                "id": "event",
                "etag": "one",
                "status": "confirmed",
                "recurring_event_id": None,
                "original_start": None,
            },
        }
        work = WorkItem(
            work_item_id="work:watch",
            owner="local",
            room_id=ROOM,
            kind="event",
            title="会议",
            state="open",
            acceptance="human",
            starts_at=start,
            ends_at=start + timedelta(hours=1),
            remind_at=start - timedelta(minutes=10),
            reminder_rule="relative",
            reminder_offset_seconds=600,
            primary_device_id=UUID(d),
            evidence=[EvidenceRef(kind="knowledge", reference="source:seed", revision=1)],
            bindings=[binding],
            updated_at=datetime.now(UTC),
        )
        await r.harness.repository.save_work(work, 0)
        old = await r.harness.repository.reminder(work)
        observer = CalendarObserver(r.harness, r.personal_assistant)
        item: JsonObject = {
            "id": "event",
            "etag": "two",
            "status": "confirmed",
            "title": "会议改期",
            "start": {"timestamp": (start + timedelta(hours=2)).isoformat()},
            "end": {"timestamp": (start + timedelta(hours=3)).isoformat()},
            "recurring_event_id": None,
            "original_start": None,
        }
        await observer.apply(work, str(binding["account_id"]), "selected", item)
        changed = await r.harness.repository.work_item(work.work_item_id)
        assert changed is not None and changed.starts_at == start + timedelta(hours=2)
        assert changed.remind_at == start + timedelta(hours=2, minutes=-10)
        occurrences = await r.harness.repository.reminder_status("local")
        assert (
            old is not None
            and next(o for o in occurrences if o["occurrence_id"] == old["occurrence_id"])["state"]
            == "cancelled"
        )
        assert await r.harness.run_once()

    c.portal.call(exercise)


def test_same_matter_serializes_different_actions_until_first_write_is_settled(
    harness: tuple[TestClient, RuntimeContainer, str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    from datetime import UTC, datetime, timedelta

    from chatwaifu_protocol.agent import AgentTaskCreate, TaskAuthorization
    from chatwaifu_runtime.agent.action_claims import ActionAlreadyClaimed

    c, r, s, _ = harness

    def hold(identifier: UUID) -> None:
        pass

    monkeypatch.setattr(r.agent_tasks, "_launch", hold)
    assert c.portal is not None

    async def exercise():
        request = AgentTaskCreate(
            session_id=UUID(s),
            goal="有界操作",
            authorization=TaskAuthorization(
                source_ref="owner", expires_at=datetime.now(UTC) + timedelta(hours=1)
            ),
        )
        first = (await r.agent_tasks.create(request)).task_id
        second = (await r.agent_tasks.create(request)).task_id
        left: JsonObject = {
            "step_key": "one",
            "skill_id": "agenda.manage",
            "side_effect": "write",
            "state": "started",
            "action_id": str(uuid4()),
            "verification": {"logical_work_id": "work:shared"},
        }
        right: JsonObject = {**left, "step_key": "two", "action_id": str(uuid4())}
        assert await r.agent_tasks.repository.begin_step(first, "one", left)
        with pytest.raises(ActionAlreadyClaimed):
            await r.agent_tasks.repository.begin_step(second, "two", right)
        await r.agent_tasks.repository.finish_step(
            first, "one", {**left, "state": "settled", "ok": True}
        )
        assert await r.agent_tasks.repository.begin_step(second, "two", right)

    c.portal.call(exercise)


def test_legacy_update_with_no_reliable_target_cannot_turn_into_creation(
    harness: tuple[TestClient, RuntimeContainer, str, str],
) -> None:
    c, _, s, d = harness
    proposal = {
        "work_item_id": "legacy-review:unmapped",
        "room_id": ROOM,
        "kind": "todo",
        "title": "旧修改建议",
        "details": "旧待办的新安排",
        "operation": "update",
        "legacy_review": True,
        "state": "proposed",
        "acceptance": "pending",
        "evidence": [{"kind": "knowledge", "reference": "source:seed", "revision": 1}],
        "updated_at": "2026-10-31T00:00:00Z",
    }
    response = c.post(
        "/v1/personal-information/processing",
        json={
            "session_id": s,
            "device_id": d,
            "room_ids": [ROOM],
            "action": "prepare",
            "pending_reviews": [proposal],
        },
    )
    assert response.status_code == 200, response.text
    item = next(
        w
        for w in c.get("/v1/personal-assistant/work-items", params={"session_id": s}).json()[
            "items"
        ]
        if w["work_item_id"] == proposal["work_item_id"]
    )
    assert item["target_id"].startswith("unresolved:")
    assert (
        c.post(
            "/v1/personal-assistant/work-items/legacy-review:unmapped",
            json={"session_id": s, "expected_revision": item["revision"], "action": "confirm"},
        ).status_code
        == 409
    )


def test_migrated_low_risk_owner_quote_is_reassessed_without_replaying_history(
    harness: tuple[TestClient, RuntimeContainer, str, str],
) -> None:
    c, r, s, d = harness
    proposal = {
        "work_item_id": "legacy-review:tea",
        "room_id": ROOM,
        "kind": "memory",
        "title": "饮茶偏好",
        "details": "我喜欢喝茶",
        "involvement": "self",
        "legacy_review": True,
        "state": "proposed",
        "acceptance": "pending",
        "evidence": [{"kind": "knowledge", "reference": "source:seed", "revision": 1}],
        "updated_at": "2026-10-31T00:00:00Z",
    }
    response = c.post(
        "/v1/personal-information/processing",
        json={
            "session_id": s,
            "device_id": d,
            "room_ids": [ROOM],
            "action": "prepare",
            "pending_reviews": [proposal],
        },
    )
    assert response.status_code == 200, response.text
    assert c.portal is not None
    saved = c.portal.call(r.harness.repository.work_item, "legacy-review:tea")
    assert saved is not None and saved.acceptance == "policy" and saved.task_id is None


def test_native_bound_edit_becomes_review_and_a_verified_action_task(
    harness: tuple[TestClient, RuntimeContainer, str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    from datetime import UTC, datetime

    from chatwaifu_protocol.harness import EvidenceRef, WorkItem

    c, r, s, d = harness
    assert c.portal is not None

    def hold(identifier: UUID) -> None:
        pass

    monkeypatch.setattr(r.agent_tasks, "_launch", hold)

    async def seed():
        work = WorkItem(
            work_item_id="work:external-sync",
            owner="local",
            room_id=ROOM,
            kind="todo",
            title="原待办",
            state="open",
            acceptance="human",
            evidence=[EvidenceRef(kind="knowledge", reference="source:seed", revision=1)],
            bindings=[
                {
                    "provider": "google",
                    "account_id": str(uuid4()),
                    "collection_id": "selected",
                    "item": {"id": "task", "etag": "v1"},
                }
            ],
            updated_at=datetime.now(UTC),
        )
        await r.harness.repository.save_work(work, 0)
        await r.harness.publish_work(work)
        return await r.knowledge.repository.get("local", "harness:work:external-sync")

    record = c.portal.call(seed)
    assert record is not None
    payload = record.model_dump(mode="json", exclude={"revision", "deleted", "updated_at"})
    payload["title"] = "本机修改"
    response = push(
        c,
        s,
        d,
        {"operation_id": str(uuid4()), "expected_revision": record.revision, "record": payload},
    )
    receipt = response.json()["receipts"][0]
    assert receipt["status"] == "conflict"
    original = c.portal.call(r.harness.repository.work_item, "work:external-sync")
    assert original is not None and original.title == "原待办"
    decision = {
        "session_id": s,
        "operation_id": str(uuid4()),
        "expected_revision": record.revision,
        "decision": "keep_local",
    }
    resolved = c.post("/v1/agent/review/sync/" + receipt["conflict_id"], json=decision)
    assert resolved.status_code == 200, resolved.text
    assert resolved.json()["external_pending"] is True
    original = c.portal.call(r.harness.repository.work_item, "work:external-sync")
    assert original is not None and original.title == "原待办"
    assert resolved.json()["work_item"]["task_id"] is not None
    assert (
        c.post("/v1/agent/review/sync/" + receipt["conflict_id"], json=decision).json()["accepted"]
        is True
    )


def test_changed_source_requeues_its_unprocessed_coalesced_neighbors(
    harness: tuple[TestClient, RuntimeContainer, str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    from chatwaifu_runtime.knowledge.contracts import Mutation, PushRequest, RecordDraft

    c, r, s, d = harness
    activate(c, s, d, [{"kind": "knowledge", "reference": "source:seed", "revision": 1}])
    assert c.portal is not None
    assert push(c, s, d, source("source:changing", "我喜欢喝茶"), epoch=2).status_code == 200
    assert push(c, s, d, source("source:neighbor", "我喜欢喝咖啡"), epoch=2).status_code == 200
    changed = False

    async def complete(role: str, system: str, user: str) -> str:
        nonlocal changed
        data = json.loads(user)
        if not changed:
            changed = True
            previous = await r.knowledge.repository.get("local", "source:changing")
            assert previous is not None
            draft = RecordDraft.model_validate(
                previous.model_dump(exclude={"revision", "deleted", "updated_at"})
            ).model_copy(update={"text": "我喜欢淡茶"})
            await r.knowledge.push(
                PushRequest(
                    session_id=UUID(s),
                    device_id=UUID(d),
                    schema_version="1.1",
                    processing_epoch=2,
                    ingestion_mode="live",
                    policy_epoch=0,
                    mutations=[Mutation(operation_id=uuid4(), expected_revision=1, record=draft)],
                )
            )
        return json.dumps(
            {
                "items": [
                    {
                        "title": "偏好" + str(i),
                        "kind": "memory",
                        "importance": "low",
                        "reason": "原文",
                        "source_refs": [row["ref"]],
                        "involvement": "self",
                        "details": row["text"],
                        "confidence": 0.95,
                        "sensitivity": "private",
                    }
                    for i, row in enumerate(data["records"])
                    if not row["context_only"]
                ]
            },
            ensure_ascii=False,
        )

    monkeypatch.setattr(r.model_configurations, "complete", complete)

    async def exercise():
        await r.database.execute(
            "UPDATE harness_observations SET available_at=0 WHERE state='pending'"
        )
        with pytest.raises(ValueError, match="source_batch_changed"):
            await r.harness.run_once()
        pending = await r.database.fetchall(
            "SELECT state FROM harness_observations WHERE reference='source:neighbor'"
        )
        assert pending[0][0] == "pending"
        receipts = await r.harness.repository.progress("local", 0)
        assert not any(row["reference"] == "source:neighbor" for row in receipts)
        await r.database.execute(
            "UPDATE harness_observations SET available_at=0 WHERE state='pending'"
        )
        assert await r.harness.run_once()
        pending = await r.database.fetchall(
            "SELECT state FROM harness_observations WHERE reference='source:neighbor'"
        )
        assert pending[0][0] == "done"

    c.portal.call(exercise)


def test_shared_memory_correction_and_forgetting_fence_both_sources(
    harness: tuple[TestClient, RuntimeContainer, str, str],
) -> None:
    from datetime import UTC, datetime

    from chatwaifu_protocol.memory import MemoryRecordDraft

    c, r, s, d = harness
    activate(c, s, d, [{"kind": "knowledge", "reference": "source:seed", "revision": 1}])
    assert push(c, s, d, source("source:memory-current", "我喜欢喝茶"), epoch=2).status_code == 200
    assert c.portal is not None

    async def prepare():
        await r.database.execute(
            "UPDATE harness_observations SET available_at=0 WHERE state='pending'"
        )
        await r.harness.run_once()

    c.portal.call(prepare)
    item = next(
        w
        for w in c.get("/v1/personal-assistant/work-items", params={"session_id": s}).json()[
            "items"
        ]
        if w["kind"] == "memory"
    )
    response = c.post(
        "/v1/personal-assistant/work-items/" + item["work_item_id"],
        json={
            "session_id": s,
            "expected_revision": item["revision"],
            "action": "edit",
            "changes": {"details": "我喜欢喝咖啡"},
        },
    )
    assert response.status_code == 200, response.text
    assert response.json()["acceptance"] == "human"
    assert response.json()["memory_identity"]["value"] is True
    assert response.json()["memory_identity"]["predicate"].endswith("喝咖啡")

    async def check():
        draft = MemoryRecordDraft(
            namespace="character/default/user/local",
            kind="semantic.preference",
            subject_id="user",
            predicate=item["memory_identity"]["predicate"],
            value=item["memory_identity"]["value"],
            text="我喜欢喝茶",
            observed_at=datetime.now(UTC),
            confidence=0.95,
            importance=0.5,
        )
        assert await r.external_memory_conflict(UUID(s), draft)
        assert await r.forget_external_memory(UUID(s), uuid4(), "我喜欢喝咖啡") == 1
        records = await r.knowledge.repository.exact_memories("local", "我喜欢喝咖啡")
        assert records == []

    c.portal.call(check)


def test_matching_an_uncancelled_event_does_not_verify_a_cancellation(
    harness: tuple[TestClient, RuntimeContainer, str, str],
) -> None:
    from datetime import UTC, datetime, timedelta

    from chatwaifu_protocol.agent import AgentTask, AgentTaskState, TaskAuthorization
    from chatwaifu_protocol.harness import EvidenceRef, WorkItem

    c, r, s, _ = harness
    assert c.portal is not None

    async def exercise():
        work = WorkItem(
            work_item_id="work:cancel-check",
            owner="local",
            room_id=ROOM,
            kind="event",
            title="会议",
            operation="cancel",
            state="waiting",
            evidence=[EvidenceRef(kind="knowledge", reference="source:seed", revision=1)],
            updated_at=datetime.now(UTC),
        )
        await r.harness.repository.save_work(work, 0)
        task = AgentTask(
            task_id=uuid4(),
            session_id=UUID(s),
            scope="local",
            goal="取消会议",
            work_item_id=work.work_item_id,
            completion_criteria=[],
            state=AgentTaskState.RUNNING,
            authorization=TaskAuthorization(
                source_ref="owner",
                allowed_skill_ids=["agenda.manage", "work.match"],
                allow_writes=True,
                expires_at=datetime.now(UTC) + timedelta(hours=1),
            ),
            max_tool_calls=20,
            max_active_seconds=300,
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
        )
        gate = await r.harness.validate_completion(
            task,
            [
                {
                    "work_item_id": work.work_item_id,
                    "skill_id": "work.match",
                    "ok": True,
                    "snapshot": {
                        "result": {
                            "data": {
                                "verified": True,
                                "state": "matched",
                                "item": {"id": "one", "status": "confirmed"},
                            }
                        }
                    },
                }
            ],
        )
        assert gate is not None

    c.portal.call(exercise)


def test_precise_capture_distinguishes_delayed_sync_from_backfill(
    harness: tuple[TestClient, RuntimeContainer, str, str],
) -> None:
    c, r, s, d = harness
    activate(c, s, d, [{"kind": "knowledge", "reference": "source:seed", "revision": 1}])
    delayed = source("source:offline")
    delayed["record"]["occurred_at"] = int(time.time() * 1000) - 86400000
    delayed["record"]["origin"] = {"capture_mode": "delayed"}
    history = source("source:restored")
    history["record"]["origin"] = {"capture_mode": "historical"}
    late = source("source:received-late")
    late["record"]["origin"] = {
        "capture_mode": "live",
        "captured_at": int(time.time() * 1000) - 3600000,
    }
    assert push(c, s, d, delayed, epoch=2).status_code == 200
    assert push(c, s, d, history, epoch=2).status_code == 200
    assert push(c, s, d, late, epoch=2).status_code == 200
    assert c.portal is not None

    async def read():
        rows = await r.database.fetchall(
            "SELECT reference,json_extract(payload,'$.ingestion_mode') "
            "FROM harness_observations WHERE reference "
            "IN ('source:offline','source:restored','source:received-late')"
        )
        return {str(row[0]): str(row[1]) for row in rows}

    assert c.portal.call(read) == {
        "source:offline": "delayed",
        "source:restored": "historical",
        "source:received-late": "delayed",
    }
    # Reception delay changes the observation, never the immutable input version.
    assert push(c, s, d, late, epoch=2).status_code == 200
    assert c.portal.call(read)["source:received-late"] == "delayed"


def test_enable_and_resume_recheck_only_unhandled_live_work(
    harness: tuple[TestClient, RuntimeContainer, str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    import asyncio
    from datetime import UTC, datetime

    from chatwaifu_protocol.harness import EvidenceRef, WorkItem

    c, r, s, _ = harness
    assert c.portal is not None

    async def seed():
        for mode in ("historical", "live"):
            item = WorkItem(
                work_item_id="work:scan-" + mode,
                owner="local",
                room_id=ROOM,
                kind="reference",
                title="资料",
                ingestion_mode=mode,
                state="proposed",
                evidence=[EvidenceRef(kind="knowledge", reference="source:seed", revision=1)],
                updated_at=datetime.now(UTC),
            )
            await r.harness.repository.save_work(item, 0)

    c.portal.call(seed)
    intent = c.post(
        "/v1/agent/intents",
        json={
            "session_id": s,
            "goal": "整理资料",
            "room_ids": [ROOM],
            "allowed_skill_ids": ["knowledge.search"],
        },
    ).json()
    enabled = c.post(
        "/v1/agent/intents/" + intent["intent_id"],
        json={"session_id": s, "expected_revision": intent["revision"], "action": "enable"},
    ).json()
    paused = c.post(
        "/v1/agent/intents/" + intent["intent_id"],
        json={"session_id": s, "expected_revision": enabled["revision"], "action": "pause"},
    ).json()
    resumed = c.post(
        "/v1/agent/intents/" + intent["intent_id"],
        json={"session_id": s, "expected_revision": paused["revision"], "action": "resume"},
    )
    assert resumed.status_code == 200, resumed.text
    calls: list[str] = []

    async def exercise():
        done = asyncio.Event()

        async def dispatch(
            work: Any, source: Any, session: Any, *, only_intent: UUID | None = None
        ) -> None:
            assert only_intent == UUID(intent["intent_id"])
            calls.append(work.work_item_id)
            done.set()

        monkeypatch.setattr(r.harness, "_dispatch", dispatch)
        worker = asyncio.create_task(r.harness._recheck_intents())
        try:
            await asyncio.wait_for(done.wait(), timeout=2)
            assert calls == ["work:scan-live"]
        finally:
            worker.cancel()
            await asyncio.gather(worker, return_exceptions=True)

    c.portal.call(exercise)


def test_metrics_latency_alert_recovery_and_owner_only_http(
    harness: tuple[TestClient, RuntimeContainer, str, str],
) -> None:
    from chatwaifu_protocol.harness import EvidenceRef, Observation, WorkItem
    from chatwaifu_runtime.persistence.sqlite_harness_metrics import SQLiteHarnessMetricsRepository
    from chatwaifu_runtime.providers.contracts import LlmUsage
    from chatwaifu_runtime.providers.metering import ModelTrace, ModelUsageSample

    c, r, s, d = harness
    assert c.portal is not None
    activate(c, s, d, [{"kind": "knowledge", "reference": "source:seed", "revision": 1}])
    stamp = int(time.time() * 1000)

    async def exercise() -> None:
        metrics = SQLiteHarnessMetricsRepository(r.database)
        observation = Observation(
            observation_id=uuid4(),
            owner="local",
            room_id=ROOM,
            evidence=EvidenceRef(kind="knowledge", reference="source:delayed", revision=1),
            ingestion_mode="delayed",
            occurred_at=stamp - 700000,
            received_at=stamp - 600000,
            policy_epoch=0,
        )
        async with r.database.transaction() as db:
            await db.execute(
                "INSERT INTO harness_observations VALUES(?,?,?,?,?,?,?,?,?)",
                (
                    str(observation.observation_id),
                    "local",
                    ROOM,
                    "source:delayed",
                    1,
                    "pending",
                    stamp,
                    0,
                    observation.model_dump_json(),
                ),
            )
        alerts = await metrics.alert_changes("local")
        assert len(alerts) == 1 and alerts[0][:2] == ("current_processing_delayed", True)
        # Crash before publishing/acknowledging the alert is replayable with the same key.
        assert await metrics.alert_changes("local") == alerts
        await metrics.acknowledge_alert("local", alerts[0][0], alerts[0][2])
        assert await metrics.alert_changes("local") == []
        completed = observation.model_copy(update={"state": "done", "completed_at": stamp})
        async with r.database.transaction() as db:
            await db.execute(
                "UPDATE harness_observations SET state='done',payload=? WHERE observation_id=?",
                (completed.model_dump_json(), str(observation.observation_id)),
            )
        recovered = await metrics.alert_changes("local")
        assert len(recovered) == 1 and recovered[0][:2] == ("current_processing_delayed", False)
        await metrics.acknowledge_alert("local", recovered[0][0], recovered[0][2])
        neighbor = completed.model_copy(
            update={
                "observation_id": uuid4(),
                "evidence": EvidenceRef(kind="knowledge", reference="source:neighbor", revision=1),
            }
        )
        async with r.database.transaction() as db:
            await db.execute(
                "INSERT INTO harness_observations VALUES(?,?,?,?,?,?,?,?,?)",
                (
                    str(neighbor.observation_id),
                    "local",
                    ROOM,
                    "source:neighbor",
                    1,
                    "done",
                    stamp,
                    0,
                    neighbor.model_dump_json(),
                ),
            )
        sample = ModelUsageSample(
            uuid4(),
            ModelTrace(
                "local",
                observation_id=observation.observation_id,
                observation_ids=(observation.observation_id, neighbor.observation_id),
            ),
            "memory_extraction",
            "test",
            120,
            "completed",
            LlmUsage(20, 5, 25),
            False,
            stamp,
        )
        await metrics.record_usage(sample)
        await metrics.record_usage(sample)  # Same invocation receipt cannot double the owner cost.
        from datetime import UTC, datetime

        work = WorkItem(
            work_item_id="work:cost",
            owner="local",
            room_id=ROOM,
            kind="todo",
            title="用量关联",
            state="proposed",
            evidence=[neighbor.evidence],
            updated_at=datetime.now(UTC),
        )
        assert await r.harness.repository.save_work(work, 0)

    c.portal.call(exercise)
    response = c.get("/v1/agent/metrics", params={"session_id": s})
    assert response.status_code == 200, response.text
    data = response.json()
    assert data["schema_version"] == "1.0"
    assert data["processor"]["running"] is False
    assert data["latency_seconds"]["completed_current_p95"] == 600
    assert data["latency_seconds"]["oldest_current_pending"] is None
    assert data["model_cost"]["reported_total_tokens"] == 25
    assert data["model_cost"]["invocations"] == 1
    assert data["source_coverage"]["uncollected_messages"] is None
    selected = c.get("/v1/agent/metrics", params={"session_id": s, "work_item_id": "work:cost"})
    assert selected.status_code == 200, selected.text
    assert selected.json()["model_cost"]["invocations"] == 0
    assert selected.json()["shared_source_model_cost"]["reported_total_tokens"] == 25
    assert (
        c.get("/v1/agent/metrics", params={"session_id": s, "work_item_id": "missing"}).status_code
        == 404
    )

    async def make_shared() -> str:
        participant = await r.sessions.create_participant("测试成员")
        scene = await r.sessions.create_scene("测试共享", ["local", participant.participant_id])
        return str((await r.sessions.create_session("default", scene_id=scene.scene_id)).session_id)

    shared = c.portal.call(make_shared)
    assert c.get("/v1/agent/metrics", params={"session_id": shared}).status_code == 403
