"""Real storage failure boundaries: restart, missed alarms, device fencing and DST."""

from collections.abc import AsyncIterator
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from chatwaifu_runtime.config.settings import Settings, StorageConfig
from chatwaifu_runtime.persistence.database import Database
from chatwaifu_runtime.persistence.sqlite_assistant_tasks import SQLiteTaskRepository
from chatwaifu_runtime.personal_assistant.repository import AssistantAccessError
from chatwaifu_runtime.personal_assistant.tasks import AppleOperation, TaskInput, next_due


@pytest.fixture
async def tasks(tmp_path: Path) -> AsyncIterator[tuple[Database, SQLiteTaskRepository]]:
    db = Database(tmp_path / "tasks.db", StorageConfig())
    await db.open()
    try:
        yield db, SQLiteTaskRepository(db)
    finally:
        await db.close()


def task(device: str, due: float = 100, repeat: str = "none") -> TaskInput:
    return TaskInput.model_validate(
        {
            "request_id": str(uuid4()),
            "device_id": device,
            "title": "test",
            "due_at": datetime.fromtimestamp(due, UTC),
            "kind": "alarm",
            "repeat": repeat,
        }
    )


async def test_restart_idempotence_and_missed_alarm(
    tasks: tuple[Database, SQLiteTaskRepository],
) -> None:
    db, repo = tasks
    device = await repo.pair("desktop")
    value = task(device["device_id"])
    await repo.create_task(value, 0)
    await repo.create_task(value, 0)
    await db.close()
    await db.open()
    await repo.tick(500)
    await repo.tick(500)
    rows = await db.fetchall("SELECT state FROM assistant_deliveries")
    assert [r[0] for r in rows] == ["missed"]
    assert not (await repo.poll(device["device_id"], device["secret"], [], 500))["deliveries"]


async def test_snooze_and_cancel_fence_late_ack(
    tasks: tuple[Database, SQLiteTaskRepository],
) -> None:
    _, repo = tasks
    device = await repo.pair("desktop")
    value = task(device["device_id"])
    await repo.create_task(value, 0)
    await repo.tick(100)
    delivery = (await repo.poll(device["device_id"], device["secret"], [], 100))["deliveries"][0]
    await repo.acknowledge(
        device["device_id"], device["secret"], delivery["delivery_id"], "snooze", {}, 101
    )
    assert not (await repo.poll(device["device_id"], device["secret"], [], 102))["deliveries"]
    later = (await repo.poll(device["device_id"], device["secret"], [], 402))["deliveries"][0]
    await repo.change_task(str(value.request_id), "cancel", 403)
    with pytest.raises(ValueError, match="delivery_not_active"):
        await repo.acknowledge(
            device["device_id"], device["secret"], later["delivery_id"], "stop", {}, 404
        )
    assert not (await repo.poll(device["device_id"], device["secret"], [], 404))["deliveries"]


async def test_no_cross_device_delivery_or_revoked_acks(
    tasks: tuple[Database, SQLiteTaskRepository],
) -> None:
    _, repo = tasks
    first, second = await repo.pair("first"), await repo.pair("second")
    await repo.create_task(task(first["device_id"]), 0)
    await repo.tick(100)
    assert not (await repo.poll(second["device_id"], second["secret"], [], 100))["deliveries"]
    with pytest.raises(AssistantAccessError):
        await repo.poll(first["device_id"], second["secret"], [], 100)
    await repo.revoke(first["device_id"])
    with pytest.raises(AssistantAccessError):
        await repo.poll(first["device_id"], first["secret"], [], 100)


async def test_unknown_write_not_reissued_and_source_revocation_fences_results(
    tasks: tuple[Database, SQLiteTaskRepository],
) -> None:
    db, repo = tasks
    device = await repo.pair("mac")
    sources = [{"id": "list", "resource": "reminder", "title": "Tasks", "writable": True}]
    await repo.poll(device["device_id"], device["secret"], sources, 0)
    op = AppleOperation(
        request_id=uuid4(),
        device_id=UUID(device["device_id"]),
        resource="reminder",
        action="create",
        calendar_id="list",
        title="test",
    )
    await repo.enqueue(op, 0)
    await repo.enqueue(op, 0)
    assert (
        len((await repo.poll(device["device_id"], device["secret"], sources, 1))["operations"]) == 1
    )
    await db.close()
    await db.open()
    assert not (await repo.poll(device["device_id"], device["secret"], sources, 2))["operations"]
    await repo.poll(device["device_id"], device["secret"], [], 3)
    await repo.poll(device["device_id"], device["secret"], sources, 4)
    with pytest.raises(ValueError, match="operation_not_active"):
        await repo.acknowledge(
            device["device_id"], device["secret"], str(op.request_id), "result", {"item": "late"}, 5
        )


async def test_task_id_cannot_be_reused_for_changed_payload(
    tasks: tuple[Database, SQLiteTaskRepository],
) -> None:
    _, repo = tasks
    d = await repo.pair("desktop")
    value = task(d["device_id"])
    await repo.create_task(value, 0)
    with pytest.raises(ValueError, match="request_id_conflict"):
        await repo.create_task(value.model_copy(update={"title": "changed"}), 0)


@pytest.mark.parametrize(
    ("due", "after", "repeat", "expected"),
    [
        ("2026-03-07T02:30:00-05:00", "2026-03-07T08:00:00Z", "daily", "2026-03-08T07:30:00+00:00"),
        ("2026-10-31T01:30:00-04:00", "2026-10-31T06:00:00Z", "daily", "2026-11-01T05:30:00+00:00"),
        (
            "2026-09-25T08:00:00-04:00",
            "2026-09-25T12:00:00Z",
            "weekdays",
            "2026-09-28T12:00:00+00:00",
        ),
    ],
)
def test_wall_clock_recurrence(due: str, after: str, repeat: str, expected: str) -> None:
    following = next_due(
        datetime.fromisoformat(due), "America/New_York", repeat, datetime.fromisoformat(after)
    )
    assert following is not None and following.isoformat() == expected


async def test_owner_scope_and_transport_before_pairing(
    tasks: tuple[Database, SQLiteTaskRepository],
) -> None:
    from types import SimpleNamespace

    import httpx
    from chatwaifu_runtime.api.personal_assistant_routes import router
    from chatwaifu_runtime.persistence.sqlite_personal_assistant import SQLiteAssistantRepository
    from chatwaifu_runtime.personal_assistant.tasks import TaskService
    from fastapi import FastAPI

    db, repo = tasks
    owner, visitor = uuid4(), uuid4()
    for session, scope in ((owner, "local"), (visitor, "guest")):
        await db.execute(
            "INSERT INTO "
            "sessions(session_id,character_id,state,conversation_state,"
            "created_at,updated_at,user_scope) "
            "VALUES(?, 'test','idle','idle','now','now',?)",
            (str(session), scope),
        )
    app = FastAPI()
    app.state.container = SimpleNamespace(
        settings=Settings(),
        personal_assistant=SimpleNamespace(tasks=TaskService(SQLiteAssistantRepository(db), repo)),
    )
    app.include_router(router)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="https://server.example"
    ) as client:
        assert (
            await client.post(
                "/v1/personal-assistant/devices", json={"session_id": str(visitor), "name": "guest"}
            )
        ).status_code == 403
        assert (
            await client.get(f"/v1/personal-assistant/organizer?session_id={visitor}")
        ).status_code == 403
        body = {"session_id": str(owner), "name": "mac"}
        assert (
            await client.post(
                "/v1/personal-assistant/devices", json=body, headers={"X-Forwarded-Proto": "https"}
            )
        ).status_code == 403
        assert (
            await client.post("http://server.example/v1/personal-assistant/devices", json=body)
        ).status_code == 403
        result = await client.post("/v1/personal-assistant/devices", json=body)
        assert result.status_code == 200
        snapshot = (await client.get(f"/v1/personal-assistant/organizer?session_id={owner}")).json()
        assert "secret" not in str(snapshot)


def test_mutation_skills_retain_confirmation_gate() -> None:
    from chatwaifu_runtime.runtime_skills.registry import SkillRegistry

    registry = SkillRegistry(Path(__file__).resolve().parents[3] / "skills/builtin")
    registry.reload([])
    for name in ("schedule.create", "schedule.change", "apple.manage"):
        entry = registry.get(name)
        assert entry is not None
        capability = entry.definition.capabilities[0]
        assert capability.confirmation_required and capability.side_effect.value == "write"
        assert capability.required_permissions


async def test_source_revision_and_deselection_clear_cached_results(
    tasks: tuple[Database, SQLiteTaskRepository],
) -> None:
    _, repo = tasks
    device = await repo.pair("mac")
    sources = [{"id": "list", "resource": "reminder", "title": "Tasks", "writable": True}]
    await repo.poll(device["device_id"], device["secret"], sources, 0, source_revision=1)
    op = AppleOperation(
        request_id=uuid4(),
        device_id=UUID(device["device_id"]),
        resource="reminder",
        action="list",
        calendar_id="list",
    )
    await repo.enqueue(op, 0)
    await repo.poll(device["device_id"], device["secret"], sources, 1, source_revision=1)
    await repo.acknowledge(
        device["device_id"],
        device["secret"],
        str(op.request_id),
        "result",
        {"items": ["private"]},
        2,
    )
    await repo.poll(device["device_id"], device["secret"], [], 3, deliver=False, source_revision=2)
    await repo.poll(device["device_id"], device["secret"], sources, 4, source_revision=1)
    assert (await repo.devices())[0]["sources"] == []
    assert (await repo.operations())[0]["result"] == {}


async def test_snooze_retry_is_idempotent_and_cancelled_task_cannot_resume(
    tasks: tuple[Database, SQLiteTaskRepository],
) -> None:
    db, repo = tasks
    device = await repo.pair("desktop")
    value = task(device["device_id"])
    await repo.create_task(value, 0)
    await repo.tick(100)
    delivery = (await repo.poll(device["device_id"], device["secret"], [], 100))["deliveries"][0]
    for _ in range(2):
        await repo.acknowledge(
            device["device_id"], device["secret"], delivery["delivery_id"], "snooze", {}, 101
        )
    assert len(await db.fetchall("SELECT * FROM assistant_deliveries")) == 2
    await repo.change_task(str(value.request_id), "cancel", 102)
    with pytest.raises(ValueError, match="only_active"):
        await repo.change_task(str(value.request_id), "pause", 103)


def test_weekday_first_occurrence_moves_to_monday() -> None:
    value = task(str(uuid4()), datetime(2026, 9, 26, 0, tzinfo=UTC).timestamp(), "weekdays")
    assert value.due_at == datetime(2026, 9, 28, 0, tzinfo=UTC)


async def test_task_edit_rejects_stale_revision(
    tasks: tuple[Database, SQLiteTaskRepository],
) -> None:
    _, repo = tasks
    device = await repo.pair("desktop")
    value = task(device["device_id"])
    await repo.create_task(value, 0)
    updated = value.model_copy(update={"title": "new title"})
    await repo.revise_task(updated, 0, 1)
    await repo.revise_task(updated, 0, 1)  # lost response retry, same intent
    with pytest.raises(ValueError, match="task_changed"):
        await repo.revise_task(value.model_copy(update={"title": "stale title"}), 0, 1)
    assert (await repo.tasks())[0]["title"] == "new title"
