"""Trusted session boundary for the personal calendar builtin."""

from datetime import UTC, datetime
from pathlib import Path
from typing import cast
from unittest.mock import AsyncMock
from zoneinfo import ZoneInfoNotFoundError

import pytest
from chatwaifu_protocol.agent import AgentTask
from chatwaifu_protocol.base import JsonObject
from chatwaifu_runtime.config.settings import Settings
from chatwaifu_runtime.personal_assistant.action_recovery import AgendaActionRecovery
from chatwaifu_runtime.personal_assistant.integration import PersonalAssistantIntegration
from chatwaifu_runtime.personal_assistant.repository import AssistantRepository
from chatwaifu_runtime.personal_assistant.skill import (
    CalendarQuery,
    CalendarReadSkill,
    _calendar_window,  # pyright: ignore[reportPrivateUsage]
)
from chatwaifu_runtime.runtime_skills.adapters import BuiltinAdapter
from chatwaifu_runtime.runtime_skills.errors import SkillExecutionError
from chatwaifu_runtime.runtime_skills.registry import SkillRegistry


@pytest.mark.parametrize("kind", ["calendar", "reminder"])
async def test_version_preflight_rejection_is_known_no_effect_and_requires_a_new_read(
    kind: str,
) -> None:
    from types import SimpleNamespace
    from uuid import uuid4

    from chatwaifu_runtime.personal_assistant.agenda_skill import AgendaManageSkill
    from chatwaifu_runtime.personal_assistant.repository import AssistantAccessError

    code = "calendar_version_changed" if kind == "calendar" else "task_version_changed"
    accounts = SimpleNamespace(
        update_event=AsyncMock(side_effect=AssistantAccessError(code)),
        update_task=AsyncMock(side_effect=AssistantAccessError(code)),
    )
    integration = SimpleNamespace(
        agenda=object(),
        accounts=accounts,
        task_calendar_scope=None,
        task_list_scope=None,
        effect_observer=None,
    )
    skill = AgendaManageSkill(cast(PersonalAssistantIntegration, integration))
    with pytest.raises(SkillExecutionError) as rejected:
        await skill(
            str(uuid4()),
            {
                "kind": kind,
                "action": "update",
                "title": "test",
                "account_id": str(uuid4()),
                "collection_id": "selected",
                "item_id": "same",
                "etag": "stale",
            },
        )
    assert rejected.value.structured.code == "version_conflict"
    assert rejected.value.structured.details["execution_state"] == "not_started"
    assert rejected.value.structured.retryable


async def test_existing_calendar_binding_survives_title_change_but_cannot_be_replaced() -> None:
    from dataclasses import replace
    from datetime import timedelta
    from types import SimpleNamespace
    from uuid import uuid4

    from chatwaifu_protocol.harness import EvidenceRef, WorkItem
    from chatwaifu_runtime.agent.harness import AgentHarnessService
    from chatwaifu_runtime.agent.work_matcher import WorkMatcher
    from chatwaifu_runtime.personal_assistant.google_calendar import CalendarEvent, EventTime
    from chatwaifu_runtime.runtime_skills.execution_context import authorized_task

    account, task_id = str(uuid4()), uuid4()
    now = datetime.now(UTC)
    event = CalendarEvent(
        "bound",
        "confirmed",
        "用户已改名",
        EventTime(None, now, "UTC"),
        EventTime(None, now + timedelta(hours=1), "UTC"),
        "v2",
        (),
        None,
        None,
    )
    work = WorkItem(
        work_item_id="work:bound",
        owner="local",
        room_id="a" * 64,
        kind="event",
        title="旧标题",
        state="waiting",
        evidence=[EvidenceRef(kind="knowledge", reference="fixture:source", revision=1)],
        updated_at=now,
        bindings=[
            {
                "provider": "google",
                "verified": True,
                "account_id": account,
                "collection_id": "calendar",
                "item": {"id": "bound"},
            }
        ],
    )
    repository = SimpleNamespace(
        work_item=AsyncMock(return_value=work),
        bind_work=AsyncMock(return_value=True),
        record=AsyncMock(),
    )
    task = SimpleNamespace(
        work_item_id=work.work_item_id,
        authorization=SimpleNamespace(calendar_ids=[account + "/calendar"]),
    )
    harness = SimpleNamespace(
        owner=AsyncMock(return_value="local"),
        eligible=AsyncMock(return_value=True),
        repository=repository,
        tasks=SimpleNamespace(repository=SimpleNamespace(get=AsyncMock(return_value=task))),
        validate_task=AsyncMock(),
    )
    accounts = SimpleNamespace(inspect_event=AsyncMock(return_value=event), query=AsyncMock())
    matcher = WorkMatcher(
        cast(AgentHarnessService, harness),
        cast(PersonalAssistantIntegration, SimpleNamespace(accounts=accounts)),
    )
    arguments: JsonObject = {
        "account_id": account,
        "calendar_id": "calendar",
        "event_id": "bound",
        "etag": "v2",
    }
    token = authorized_task.set(task_id)
    try:
        result = await matcher(str(uuid4()), arguments)
        assert result["verified"] is True
        accounts.query.assert_not_called()
        repository.bind_work.reset_mock()
        proposal = work.model_copy(
            update={
                "work_item_id": "work:proposal",
                "target_id": work.work_item_id,
                "expected_revision": work.revision,
            }
        )
        task.work_item_id = proposal.work_item_id

        def lookup(identifier: str) -> WorkItem:
            return (
                proposal
                if identifier == proposal.work_item_id
                else work.model_copy(update={"revision": work.revision + 1})
            )

        repository.work_item.side_effect = lookup
        with pytest.raises(SkillExecutionError) as conflict:
            await matcher(str(uuid4()), arguments)
        assert conflict.value.structured.details == {"reason": "work_target_revision_changed"}
        repository.bind_work.assert_not_called()
        repository.work_item.side_effect = None
        task.work_item_id = work.work_item_id
        accounts.inspect_event.return_value = replace(event, id="other", title="旧标题")
        with pytest.raises(SkillExecutionError) as denied:
            await matcher(str(uuid4()), {**arguments, "event_id": "other"})
        assert denied.value.structured.code == "work_match_needs_review"
        repository.bind_work.assert_not_called()
        accounts.inspect_event.return_value = event
        repository.record.side_effect = ValueError("post_binding_audit_failed")
        with pytest.raises(SkillExecutionError) as uncertain:
            await matcher(str(uuid4()), arguments)
        assert uncertain.value.structured.code == "work_match_state_unknown"
    finally:
        authorized_task.reset(token)


@pytest.mark.parametrize(
    "reason", ["calendar_outside_task_grant", "calendar_version_changed", "task_version_changed"]
)
async def test_native_scope_denial_is_known_not_started_without_provider_access(
    reason: str,
) -> None:
    integration = PersonalAssistantIntegration(Settings(), cast(AssistantRepository, AsyncMock()))
    recovery = AgendaActionRecovery(integration)
    step: JsonObject = {
        "skill_id": "agenda.manage",
        "snapshot": {"error": {"code": "agenda_access_denied", "message": reason}},
    }
    receipt = await recovery.recover(cast(AgentTask, AsyncMock()), step)
    assert receipt is not None and receipt["execution_state"] == "not_started"
    assert receipt["verified"] is False
    step["snapshot"] = {"error": {"code": "transport_error", "message": "unknown"}}
    assert await recovery.recover(cast(AgentTask, AsyncMock()), step) is None


async def test_builtin_session_is_not_taken_from_model_arguments() -> None:
    adapter = BuiltinAdapter()
    handler = AsyncMock(return_value={"ok": True})
    adapter.register_session("calendar", handler)
    with pytest.raises(SkillExecutionError, match="trusted Runtime session"):
        await adapter.invoke("calendar", {"session_id": "forged"})
    await adapter.invoke("calendar", {}, "trusted")
    handler.assert_awaited_once_with("trusted", {})


async def test_unconfigured_calendar_is_a_normalized_failure() -> None:
    integration = PersonalAssistantIntegration(Settings(), cast(AssistantRepository, AsyncMock()))
    with pytest.raises(SkillExecutionError) as error:
        await CalendarReadSkill(integration)("trusted", {})
    assert error.value.structured.code == "calendar_not_configured"


def test_calendar_manifest_declares_permission_and_read_only() -> None:
    root = Path(__file__).resolve().parents[3] / "skills/builtin"
    registry = SkillRegistry(root)
    registry.reload([])
    entry = registry.get("calendar.read")
    assert entry is not None
    cap = entry.definition.capabilities[0]
    assert cap.required_permissions == ["calendar.read"]
    assert cap.side_effect.value == "read"
    assert cap.input_schema["additionalProperties"] is False
    properties = cap.input_schema["properties"]
    assert isinstance(properties, dict)
    assert "start" not in properties
    assert "period" in properties


def test_account_owned_agenda_manifests_keep_read_and_write_permissions_separate() -> None:
    root = Path(__file__).resolve().parents[3] / "skills/builtin"
    registry = SkillRegistry(root)
    registry.reload([])
    write = registry.get("agenda.manage")
    read = registry.get("google-tasks.read")
    assert write is not None and read is not None
    assert write.definition.capabilities[0].side_effect.value == "write"
    assert write.definition.capabilities[0].confirmation_required
    assert write.definition.capabilities[0].required_permissions == ["agenda.manage"]
    assert read.definition.capabilities[0].side_effect.value == "read"
    assert not read.definition.capabilities[0].confirmation_required
    assert read.definition.capabilities[0].required_permissions == ["google-tasks.read"]


def test_calendar_period_uses_selected_calendar_day_across_utc_midnight() -> None:
    # At 06:08 in Shanghai, a 06:00 event is still ongoing; querying the
    # UTC date's midnight would incorrectly omit it.
    now = datetime(2026, 9, 26, 22, 8, tzinfo=UTC)
    start, end, zone = _calendar_window(CalendarQuery.model_validate({}), "Asia/Shanghai", now)
    assert zone == "Asia/Shanghai"
    assert start.isoformat() == "2026-09-27T00:00:00+08:00"
    assert end.isoformat() == "2026-10-04T00:00:00+08:00"
    assert start <= datetime(2026, 9, 26, 22, 0, tzinfo=UTC) < end

    tomorrow, next_day, _ = _calendar_window(
        CalendarQuery.model_validate({"period": "tomorrow"}), "Asia/Shanghai", now
    )
    assert tomorrow.isoformat() == "2026-09-28T00:00:00+08:00"
    assert next_day.isoformat() == "2026-09-29T00:00:00+08:00"
    with pytest.raises(ZoneInfoNotFoundError):
        _calendar_window(CalendarQuery.model_validate({}), None, now)


async def test_calendar_denies_nonowner_without_querying_provider() -> None:
    from chatwaifu_runtime.personal_assistant.accounts import GoogleAccountService
    from chatwaifu_runtime.personal_assistant.repository import AssistantAccessError

    service = AsyncMock(spec=GoogleAccountService)
    service.status.side_effect = AssistantAccessError("personal_account_requires_owner")
    integration = PersonalAssistantIntegration(Settings(), cast(AssistantRepository, AsyncMock()))
    integration.accounts = service
    with pytest.raises(SkillExecutionError) as error:
        await CalendarReadSkill(integration)(
            "shared-session",
            {
                "start": "2026-09-22T00:00:00+08:00",
                "end": "2026-09-23T00:00:00+08:00",
            },
        )
    assert error.value.structured.code == "calendar_access_denied"
    service.query.assert_not_called()
