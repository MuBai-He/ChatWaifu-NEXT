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
from chatwaifu_runtime.personal_assistant.repository import AssistantAccessError, WriteDestination
from chatwaifu_runtime.runtime_skills.errors import SkillExecutionError
from chatwaifu_runtime.runtime_skills.execution_context import authorized_action


class AgendaMutation(BaseModel):
    model_config = ConfigDict(extra="forbid")
    action: Literal["create", "update", "complete", "delete"]
    kind: Literal["calendar", "reminder"]
    title: str | None = Field(default=None, min_length=1, max_length=200)
    start_date: date | None = None
    end_date: date | None = None
    start: AwareDatetime | None = None
    end: AwareDatetime | None = None
    due_date: date | None = None
    clear_due: bool = False
    due_at: AwareDatetime | None = None
    notes: str | None = Field(default=None, max_length=8192)
    location: str | None = Field(default=None, max_length=500)
    account_id: UUID | None = None
    collection_id: str | None = Field(default=None, min_length=1, max_length=2048)
    item_id: str | None = Field(default=None, min_length=1, max_length=2048)
    etag: str | None = Field(default=None, min_length=1, max_length=256)

    @model_validator(mode="after")
    def valid_shape(self):
        timed = self.start is not None or self.end is not None
        dated = self.start_date is not None or self.end_date is not None
        if timed and dated:
            raise ValueError("mixed_event_window")
        if timed and (
            self.start is None
            or self.end is None
            or not timedelta(0) < self.end - self.start <= timedelta(days=31)
        ):
            raise ValueError("event_window_invalid")
        if dated and (
            self.start_date is None
            or self.end_date is None
            or not timedelta(0) < self.end_date - self.start_date <= timedelta(days=31)
        ):
            raise ValueError("event_date_window_invalid")
        if self.kind != "calendar" and (timed or dated):
            raise ValueError("task_cannot_have_event_window")
        if self.clear_due and (
            self.kind != "reminder" or self.action != "update" or self.due_date is not None
        ):
            raise ValueError("invalid_due_clear")
        if self.action == "create":
            if (self.account_id is None) != (self.collection_id is None):
                raise ValueError("explicit_destination_incomplete")
            if not self.title:
                raise ValueError("title_required")
            if self.kind == "calendar" and not (timed or dated):
                raise ValueError("event_window_required")
        elif not self.account_id or not self.collection_id or not self.item_id or not self.etag:
            raise ValueError("google_item_reference_and_etag_required")
        if self.action == "complete" and self.kind != "reminder":
            raise ValueError("only_reminders_can_be_completed")
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
            if mutation.kind == "reminder" and self.integration.task_list_scope:
                allowed = await self.integration.task_list_scope()
            if allowed is not None:
                if mutation.action == "create":
                    destination = next(
                        (
                            d
                            for d in await agenda.destinations(session_id)
                            if d.kind == mutation.kind
                        ),
                        None,
                    )
                    collection = mutation.collection_id or (
                        destination.collection_id if destination else None
                    )
                    account = mutation.account_id or (
                        destination.account_id if destination else None
                    )
                else:
                    collection, account = mutation.collection_id, mutation.account_id
                if collection not in allowed and f"{account}/{collection}" not in allowed:
                    raise AssistantAccessError("calendar_outside_task_grant")
            if mutation.action == "create":
                assert mutation.title is not None
                result = await agenda.create(
                    session_id,
                    mutation.kind,
                    authorized_action.get() or uuid4(),
                    mutation.title,
                    start_date=mutation.start_date,
                    end_date=mutation.end_date,
                    start=mutation.start,
                    end=mutation.end,
                    due_date=mutation.due_date,
                    due_at=mutation.due_at,
                    notes=mutation.notes,
                    location=mutation.location,
                    destination=WriteDestination(
                        mutation.kind,
                        "google",
                        str(mutation.account_id),
                        mutation.collection_id,
                        None,
                    )
                    if mutation.account_id and mutation.collection_id
                    else None,
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
                        result = {
                            "state": "deleted",
                            "provider": "google",
                            "verified": True,
                            "item_id": mutation.item_id,
                            "account_id": account_id,
                            "collection_id": mutation.collection_id,
                        }
                    else:
                        changes: dict[str, object] = {}
                        if mutation.location is not None:
                            changes["location"] = mutation.location
                        if mutation.notes is not None:
                            changes["description"] = mutation.notes
                        if mutation.title is not None:
                            changes["summary"] = mutation.title
                        if mutation.start is not None and mutation.end is not None:
                            changes["start"] = {"dateTime": mutation.start.isoformat()}
                            changes["end"] = {"dateTime": mutation.end.isoformat()}
                        if mutation.start_date is not None and mutation.end_date is not None:
                            changes["start"] = {"date": mutation.start_date.isoformat()}
                            changes["end"] = {"date": mutation.end_date.isoformat()}
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
                        result = {
                            "state": "saved",
                            "provider": "google",
                            "verified": True,
                            "account_id": account_id,
                            "collection_id": mutation.collection_id,
                            "item": asdict(item),
                        }
                elif mutation.action == "delete":
                    await service.delete_task(
                        session_id,
                        account_id,
                        mutation.collection_id,
                        mutation.item_id,
                        mutation.etag,
                    )
                    result = {
                        "state": "deleted",
                        "provider": "google",
                        "verified": True,
                        "item_id": mutation.item_id,
                        "account_id": account_id,
                        "collection_id": mutation.collection_id,
                    }
                else:
                    task_changes: dict[str, object] = {}
                    if mutation.title is not None:
                        task_changes["title"] = mutation.title
                    if mutation.due_date is not None:
                        task_changes["due"] = f"{mutation.due_date.isoformat()}T00:00:00.000Z"
                    if mutation.clear_due:
                        task_changes["due"] = None
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
                    result = {
                        "state": "saved",
                        "provider": "google",
                        "verified": True,
                        "account_id": account_id,
                        "collection_id": mutation.collection_id,
                        "item": asdict(item),
                    }
            normalized = cast(JsonObject, json.loads(json.dumps(result, default=str)))
            if self.integration.effect_observer is not None:
                try:
                    await self.integration.effect_observer(session_id, arguments, normalized)
                except Exception:
                    normalized["harness_recording_pending"] = True
            return normalized
        except AssistantAccessError as error:
            if str(error) == "calendar_outside_task_grant":
                raise SkillExecutionError("task_scope_denied", str(error)) from None
            if str(error) in {"calendar_version_changed", "task_version_changed"}:
                raise SkillExecutionError(
                    "version_conflict",
                    "对象版本已变化，本次操作尚未写入。请重新读取最新对象与版本，"
                    "核对人工修改是否与来源冲突后再决定，不要直接重放旧请求。",
                    retryable=True,
                    details={"execution_state": "not_started"},
                ) from None
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
            allowed = (
                await self.integration.task_list_scope()
                if self.integration.task_list_scope
                else None
            )
            for account in accounts:
                if account.status != "connected" or not account.tasks_write:
                    continue
                for tasklist in await service.tasklists(session_id, account.account_id):
                    if not tasklist.selected or (
                        allowed is not None
                        and tasklist.tasklist.id not in allowed
                        and f"{account.account_id}/{tasklist.tasklist.id}" not in allowed
                    ):
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
