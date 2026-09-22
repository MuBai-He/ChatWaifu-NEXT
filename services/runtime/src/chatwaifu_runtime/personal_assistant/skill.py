"""Session-bound read-only calendar tool; uses the same selected-calendar service as UI."""

import json
from dataclasses import asdict
from datetime import timedelta
from typing import cast

from chatwaifu_protocol.base import JsonObject
from pydantic import AwareDatetime, BaseModel, ConfigDict, ValidationError, model_validator

from chatwaifu_runtime.personal_assistant.google_calendar import GoogleCalendarError
from chatwaifu_runtime.personal_assistant.integration import PersonalAssistantIntegration
from chatwaifu_runtime.personal_assistant.repository import AssistantAccessError
from chatwaifu_runtime.runtime_skills.errors import SkillExecutionError


class CalendarQuery(BaseModel):
    model_config = ConfigDict(extra="forbid")
    start: AwareDatetime
    end: AwareDatetime

    @model_validator(mode="after")
    def bounded(self):
        if not timedelta(0) < self.end - self.start <= timedelta(days=31):
            raise ValueError("window must be within 31 days")
        return self


class CalendarReadSkill:
    def __init__(self, integration: PersonalAssistantIntegration) -> None:
        self._integration = integration

    async def __call__(self, session_id: str, arguments: JsonObject) -> JsonObject:
        service = self._integration.accounts
        if service is None:
            raise SkillExecutionError(
                "calendar_not_configured", "请先在个人助理设置中连接 Google 日历。"
            )
        try:
            query = CalendarQuery.model_validate(arguments)
        except ValidationError:
            raise SkillExecutionError(
                "invalid_query_window", "请提供带时区的起止时间，范围不超过 31 天。"
            ) from None
        try:
            accounts = await service.status(session_id)
            sources: list[tuple[str, str, str]] = []
            for account in accounts:
                if account.status != "connected":
                    continue
                for item in await service.calendars(session_id, account.account_id):
                    if item.selected:
                        sources.append((account.account_id, item.calendar.id, item.calendar.title))
            if not sources:
                raise SkillExecutionError(
                    "calendar_not_selected", "请先在个人助理设置中选择允许查询的日历。"
                )
            if len(sources) > 8:
                raise SkillExecutionError(
                    "calendar_query_limit", "一次最多查询 8 个日历，请缩小所选日历范围。"
                )
            results: list[dict[str, object]] = []
            for account_id, calendar_id, title in sources:
                events = await service.query(
                    session_id, account_id, calendar_id, query.start, query.end
                )
                results.extend({"calendar": title, "event": asdict(event)} for event in events)
                if len(results) > 200:
                    raise SkillExecutionError(
                        "calendar_query_limit", "日程过多，请缩短查询时间范围。"
                    )
            # Recheck all selections after the last upstream read; don't leak a
            # previous calendar's result after its concurrent deselection/revoke.
            for account_id, calendar_id, _ in sources:
                current = await service.calendars(session_id, account_id)
                if not any(item.selected and item.calendar.id == calendar_id for item in current):
                    raise AssistantAccessError("calendar_not_selected")
            return cast(
                JsonObject,
                json.loads(json.dumps({"source": "google_live", "events": results}, default=str)),
            )
        except AssistantAccessError:
            raise SkillExecutionError(
                "calendar_access_denied", "仅主人私聊可查询已授权且选中的个人日历。"
            ) from None
        except GoogleCalendarError:
            raise SkillExecutionError(
                "calendar_unavailable", "Google 日历暂时无法读取，请稍后重试或检查授权。"
            ) from None
