"""Trusted-session agenda mutation tool; normal Runtime Skill confirmation applies."""

from __future__ import annotations

import json
from dataclasses import asdict
from datetime import date, timedelta
from typing import Literal, cast
from uuid import UUID, uuid4

from chatwaifu_protocol.base import JsonObject
from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, ValidationError, model_validator

from chatwaifu_runtime.personal_assistant.google_calendar import GoogleCalendarError
from chatwaifu_runtime.personal_assistant.integration import PersonalAssistantIntegration
from chatwaifu_runtime.personal_assistant.repository import AssistantAccessError
from chatwaifu_runtime.runtime_skills.errors import SkillExecutionError


class AgendaMutation(BaseModel):
    model_config = ConfigDict(extra="forbid")
    action: Literal["create", "update", "complete", "delete"]
    kind: Literal["calendar", "reminder"]
    title: str | None = Field(default=None, min_length=1, max_length=200)
    start: AwareDatetime | None = None
    end: AwareDatetime | None = None
    due_date: date | None = None
    due_at: AwareDatetime | None = None
    notes: str | None = Field(default=None, max_length=8192)
    account_id: UUID | None = None
    collection_id: str | None = Field(default=None, min_length=1, max_length=2048)
    item_id: str | None = Field(default=None, min_length=1, max_length=2048)
    etag: str | None = Field(default=None, min_length=1, max_length=256)

    @model_validator(mode="after")
    def valid_shape(self):
        if self.action == "create":
            if not self.title:
                raise ValueError("title_required")
            if self.kind == "calendar" and (
                self.start is None
                or self.end is None
                or self.end <= self.start
                or self.end - self.start > timedelta(days=31)
            ):
                raise ValueError("event_window_required")
            if self.kind == "reminder" and self.start is not None:
                raise ValueError("task_cannot_have_event_window")
        elif not self.account_id or not self.collection_id or not self.item_id or not self.etag:
            raise ValueError("google_item_reference_and_etag_required")
        if self.action == "complete" and self.kind != "reminder":
            raise ValueError("only_reminders_can_be_completed")
        if (
            self.action == "update"
            and self.kind == "calendar"
            and (
                (self.start is None) != (self.end is None)
                or (
                    self.start is not None
                    and self.end is not None
                    and (self.end <= self.start or self.end - self.start > timedelta(days=31))
                )
            )
        ):
            raise ValueError("event_window_invalid")
        return self


class AgendaManageSkill:
    def __init__(self, integration: PersonalAssistantIntegration) -> None:
        self.integration = integration

    async def __call__(self, session_id: str, arguments: JsonObject) -> JsonObject:
        agenda = self.integration.agenda
        if agenda is None:
            raise SkillExecutionError("assistant_disabled", "个人助理尚未启用。")
        try:
            mutation = AgendaMutation.model_validate(arguments)
        except ValidationError:
            raise SkillExecutionError(
                "invalid_agenda_arguments",
                "请提供明确的事项类型、操作、标题和时间。修改时需使用查询得到的来源标识与版本。",
            ) from None
        try:
            allowed = (
                await self.integration.task_calendar_scope()
                if self.integration.task_calendar_scope
                else None
            )
            if allowed is not None and mutation.kind == "calendar":
                if mutation.action == "create":
                    destination = next(
                        (d for d in await agenda.destinations(session_id) if d.kind == "calendar"),
                        None,
                    )
                    collection = destination.collection_id if destination else None
                    account = destination.account_id if destination else None
                else:
                    collection, account = mutation.collection_id, mutation.account_id
                if collection not in allowed and f"{account}/{collection}" not in allowed:
                    raise AssistantAccessError("calendar_outside_task_grant")
            if mutation.action == "create":
                assert mutation.title is not None
                result = await agenda.create(
                    session_id,
                    mutation.kind,
                    uuid4(),
                    mutation.title,
                    start=mutation.start,
                    end=mutation.end,
                    due_date=mutation.due_date,
                    due_at=mutation.due_at,
                    notes=mutation.notes,
                )
            else:
                service = self.integration.accounts
                if service is None:
                    raise AssistantAccessError("google_account_unavailable")
                assert (
                    mutation.account_id
                    and mutation.collection_id
                    and mutation.item_id
                    and mutation.etag
                )
                account_id = str(mutation.account_id)
                if mutation.kind == "calendar":
                    if mutation.action == "delete":
                        await service.delete_event(
                            session_id,
                            account_id,
                            mutation.collection_id,
                            mutation.item_id,
                            mutation.etag,
                        )
                        result = {"state": "deleted", "provider": "google"}
                    else:
                        changes: dict[str, object] = {}
                        if mutation.title is not None:
                            changes["summary"] = mutation.title
                        if mutation.start is not None and mutation.end is not None:
                            changes["start"] = {"dateTime": mutation.start.isoformat()}
                            changes["end"] = {"dateTime": mutation.end.isoformat()}
                        if not changes:
                            raise AssistantAccessError("event_change_required")
                        item = await service.update_event(
                            session_id,
                            account_id,
                            mutation.collection_id,
                            mutation.item_id,
                            mutation.etag,
                            changes,
                        )
                        result = {"state": "saved", "provider": "google", "item": asdict(item)}
                elif mutation.action == "delete":
                    await service.delete_task(
                        session_id,
                        account_id,
                        mutation.collection_id,
                        mutation.item_id,
                        mutation.etag,
                    )
                    result = {"state": "deleted", "provider": "google"}
                else:
                    task_changes: dict[str, object] = {}
                    if mutation.title is not None:
                        task_changes["title"] = mutation.title
                    if mutation.due_date is not None:
                        task_changes["due"] = f"{mutation.due_date.isoformat()}T00:00:00.000Z"
                    if mutation.notes is not None:
                        task_changes["notes"] = mutation.notes
                    if mutation.action == "complete":
                        task_changes["status"] = "completed"
                    if not task_changes:
                        raise AssistantAccessError("task_change_required")
                    item = await service.update_task(
                        session_id,
                        account_id,
                        mutation.collection_id,
                        mutation.item_id,
                        mutation.etag,
                        task_changes,
                    )
                    result = {"state": "saved", "provider": "google", "item": asdict(item)}
            return cast(JsonObject, json.loads(json.dumps(result, default=str)))
        except AssistantAccessError as error:
            raise SkillExecutionError("agenda_access_denied", str(error)) from None
        except GoogleCalendarError as error:
            message = (
                "写入结果不确定，请先在原账户核对，勿重复创建。"
                if error.retryable
                else "来源拒绝了操作，请刷新事项或检查授权。"
            )
            raise SkillExecutionError(error.code, message) from None


class GoogleTasksReadSkill:
    def __init__(self, integration: PersonalAssistantIntegration) -> None:
        self.integration = integration

    async def __call__(self, session_id: str, arguments: JsonObject) -> JsonObject:
        if arguments:
            raise SkillExecutionError("invalid_query", "此工具无需参数。")
        service = self.integration.accounts
        if service is None:
            raise SkillExecutionError("google_tasks_unavailable", "Google 账号尚未配置。")
        try:
            accounts = await service.status(session_id)
            results: list[dict[str, object]] = []
            sources = 0
            for account in accounts:
                if account.status != "connected" or not account.tasks_write:
                    continue
                for tasklist in await service.tasklists(session_id, account.account_id):
                    if not tasklist.selected:
                        continue
                    sources += 1
                    if sources > 8:
                        raise SkillExecutionError(
                            "task_query_limit", "待办列表超过 8 个，请缩小范围。"
                        )
                    tasks = await service.tasks(
                        session_id, account.account_id, tasklist.tasklist.id
                    )
                    results.extend(
                        {
                            "account_id": account.account_id,
                            "collection_id": tasklist.tasklist.id,
                            "list_title": tasklist.tasklist.title,
                            "task": asdict(task),
                        }
                        for task in tasks
                    )
                    if len(results) > 200:
                        raise SkillExecutionError(
                            "task_query_limit", "待办超过 200 条，请缩小范围。"
                        )
            return cast(
                JsonObject,
                json.loads(json.dumps({"source": "google_live", "tasks": results}, default=str)),
            )
        except AssistantAccessError:
            raise SkillExecutionError(
                "task_access_denied", "仅主人私聊可读取 Google 待办。"
            ) from None
        except GoogleCalendarError:
            raise SkillExecutionError(
                "google_tasks_unavailable", "Google 待办暂时不可读取。"
            ) from None
