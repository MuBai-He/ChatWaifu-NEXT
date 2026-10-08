"""Session-bound read-only calendar tool; uses the same selected-calendar service as UI."""

import json
from dataclasses import asdict
from datetime import UTC, date, datetime, time, timedelta
from typing import Literal, cast
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from chatwaifu_protocol.base import JsonObject
from pydantic import AwareDatetime, BaseModel, ConfigDict, ValidationError, model_validator

from chatwaifu_runtime.personal_assistant.google_calendar import GoogleCalendarError
from chatwaifu_runtime.personal_assistant.integration import PersonalAssistantIntegration
from chatwaifu_runtime.personal_assistant.repository import AssistantAccessError
from chatwaifu_runtime.runtime_skills.errors import SkillExecutionError


class CalendarQuery(BaseModel):
    model_config = ConfigDict(extra="forbid")
    period: (
        Literal["today", "tomorrow", "yesterday", "this_week", "next_week", "next_7_days"] | None
    ) = None
    start_date: date | None = None
    end_date: date | None = None
    # Retain the original typed window for existing internal callers. The
    # model-facing schema uses dates/periods so it cannot guess a UTC day.
    start: AwareDatetime | None = None
    end: AwareDatetime | None = None

    @model_validator(mode="after")
    def bounded(self):
        explicit = self.start is not None or self.end is not None
        dates = self.start_date is not None or self.end_date is not None
        if sum((explicit, dates, self.period is not None)) > 1:
            raise ValueError("choose one calendar window")
        if explicit and (
            self.start is None
            or self.end is None
            or not timedelta(0) < self.end - self.start <= timedelta(days=31)
        ):
            raise ValueError("window must be within 31 days")
        if dates and (
            self.start_date is None
            or self.end_date is None
            or not timedelta(0) <= self.end_date - self.start_date < timedelta(days=31)
        ):
            raise ValueError("date range must be within 31 days")
        return self


def _calendar_window(
    query: CalendarQuery, timezone: str | None, now: datetime
) -> tuple[datetime, datetime, str]:
    if query.start is not None and query.end is not None:
        return query.start, query.end, timezone or "UTC"
    if not timezone:
        raise ZoneInfoNotFoundError("calendar timezone unavailable")
    zone = ZoneInfo(timezone)
    today = now.astimezone(zone).date()
    period = query.period or "next_7_days"
    if query.start_date is not None and query.end_date is not None:
        first, last = query.start_date, query.end_date + timedelta(days=1)
    elif period == "today":
        first, last = today, today + timedelta(days=1)
    elif period == "tomorrow":
        first, last = today + timedelta(days=1), today + timedelta(days=2)
    elif period == "yesterday":
        first, last = today - timedelta(days=1), today
    elif period in {"this_week", "next_week"}:
        first = today - timedelta(days=today.weekday())
        if period == "next_week":
            first += timedelta(days=7)
        last = first + timedelta(days=7)
    else:
        first, last = today, today + timedelta(days=7)
    start = datetime.combine(first, time.min, tzinfo=zone)
    end = datetime.combine(last, time.min, tzinfo=zone)
    if not timedelta(0) < end - start <= timedelta(days=31):
        raise ValueError("window must be within 31 days")
    return start, end, str(zone)


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
                "invalid_query_window", "请选择日期范围，最多 31 天。"
            ) from None
        try:
            accounts = await service.status(session_id)
            sources: list[tuple[str, str, str, str | None]] = []
            for account in accounts:
                if account.status != "connected":
                    continue
                for item in await service.calendars(session_id, account.account_id):
                    if item.selected:
                        sources.append(
                            (
                                account.account_id,
                                item.calendar.id,
                                item.calendar.title,
                                item.calendar.timezone,
                            )
                        )
            allowed = (
                await self._integration.task_calendar_scope()
                if self._integration.task_calendar_scope
                else None
            )
            if allowed is not None:
                sources = [s for s in sources if s[1] in allowed or f"{s[0]}/{s[1]}" in allowed]
            if not sources:
                raise SkillExecutionError(
                    "calendar_not_selected", "请先在个人助理设置中选择允许查询的日历。"
                )
            if len(sources) > 8:
                raise SkillExecutionError(
                    "calendar_query_limit", "一次最多查询 8 个日历，请缩小所选日历范围。"
                )
            results: list[dict[str, object]] = []
            windows: list[dict[str, str]] = []
            now = datetime.now(UTC)
            for account_id, calendar_id, title, timezone in sources:
                try:
                    start, end, used_timezone = _calendar_window(query, timezone, now)
                except ZoneInfoNotFoundError:
                    raise SkillExecutionError(
                        "calendar_timezone_unavailable", "日历时区不可用，请检查服务器时区数据。"
                    ) from None
                except ValueError:
                    raise SkillExecutionError(
                        "invalid_query_window", "日期范围超过 31 天，请缩短后再试。"
                    ) from None
                events = await service.query(session_id, account_id, calendar_id, start, end)
                results.extend(
                    {
                        "account_id": account_id,
                        "collection_id": calendar_id,
                        "calendar": title,
                        "event": asdict(event),
                    }
                    for event in events
                )
                windows.append(
                    {
                        "calendar": title,
                        "timezone": used_timezone,
                        "start": start.isoformat(),
                        "end": end.isoformat(),
                    }
                )
                if len(results) > 200:
                    raise SkillExecutionError(
                        "calendar_query_limit", "日程过多，请缩短查询时间范围。"
                    )
            # Recheck all selections after the last upstream read; don't leak a
            # previous calendar's result after its concurrent deselection/revoke.
            for account_id, calendar_id, _, _ in sources:
                current = await service.calendars(session_id, account_id)
                if not any(item.selected and item.calendar.id == calendar_id for item in current):
                    raise AssistantAccessError("calendar_not_selected")
            return cast(
                JsonObject,
                json.loads(
                    json.dumps(
                        {"source": "google_live", "windows": windows, "events": results},
                        default=str,
                    )
                ),
            )
        except AssistantAccessError:
            raise SkillExecutionError(
                "calendar_access_denied", "仅主人私聊可查询已授权且选中的个人日历。"
            ) from None
        except GoogleCalendarError:
            raise SkillExecutionError(
                "calendar_unavailable", "Google 日历暂时无法读取，请稍后重试或检查授权。"
            ) from None
