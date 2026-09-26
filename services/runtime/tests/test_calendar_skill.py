"""Trusted session boundary for the personal calendar builtin."""

from datetime import UTC, datetime
from pathlib import Path
from typing import cast
from unittest.mock import AsyncMock
from zoneinfo import ZoneInfoNotFoundError

import pytest
from chatwaifu_runtime.config.settings import Settings
from chatwaifu_runtime.personal_assistant.integration import PersonalAssistantIntegration
from chatwaifu_runtime.personal_assistant.repository import AssistantRepository
from chatwaifu_runtime.personal_assistant.skill import (
    CalendarQuery,
    CalendarReadSkill,
    _calendar_window,
)
from chatwaifu_runtime.runtime_skills.adapters import BuiltinAdapter
from chatwaifu_runtime.runtime_skills.errors import SkillExecutionError
from chatwaifu_runtime.runtime_skills.registry import SkillRegistry


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
    assert "start" not in cap.input_schema["properties"]
    assert "period" in cap.input_schema["properties"]


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
