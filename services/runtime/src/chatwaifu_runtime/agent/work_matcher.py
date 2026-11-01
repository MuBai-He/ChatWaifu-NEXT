"""Bind a unique provider object using dates and identity, without changing that object."""

import json
from dataclasses import asdict
from datetime import datetime, time, timedelta
from typing import cast
from uuid import UUID
from zoneinfo import ZoneInfo

from chatwaifu_protocol.base import JsonObject
from pydantic import BaseModel, ConfigDict, Field

from chatwaifu_runtime.agent.harness import AgentHarnessService
from chatwaifu_runtime.personal_assistant.google_calendar import CalendarEvent
from chatwaifu_runtime.personal_assistant.integration import PersonalAssistantIntegration
from chatwaifu_runtime.personal_assistant.verification import matching_provider_etag
from chatwaifu_runtime.runtime_skills.errors import SkillExecutionError
from chatwaifu_runtime.runtime_skills.execution_context import authorized_task


class MatchArguments(BaseModel):
    model_config = ConfigDict(extra="forbid")
    work_item_id: str | None = Field(default=None, max_length=200)
    account_id: UUID
    calendar_id: str = Field(min_length=1, max_length=2048)
    event_id: str = Field(min_length=1, max_length=2048)
    etag: str = Field(min_length=1, max_length=256)


class WorkMatcher:
    def __init__(
        self, harness: AgentHarnessService, integration: PersonalAssistantIntegration
    ) -> None:
        self.harness, self.integration = harness, integration

    async def __call__(self, session_id: str, arguments: JsonObject) -> JsonObject:
        binding_saved = False
        try:
            args = MatchArguments.model_validate(arguments)
            owner = await self.harness.owner(UUID(session_id))
            task_id = authorized_task.get()
            task = await self.harness.tasks.repository.get(task_id) if task_id else None
            identifier = task.work_item_id if task else args.work_item_id
            work = await self.harness.repository.work_item(identifier) if identifier else None
            if (
                work is None
                or work.owner != owner
                or work.kind != "event"
                or not await self.harness.eligible(owner, work.evidence)
            ):
                raise ValueError("work_not_available")
            accounts = self.integration.accounts
            if accounts is None:
                raise ValueError("calendar_not_configured")
            account = str(args.account_id)
            scopes = task.authorization.calendar_ids if task else [f"{account}/{args.calendar_id}"]
            if (
                task
                and args.calendar_id not in scopes
                and f"{account}/{args.calendar_id}" not in scopes
            ):
                raise PermissionError("calendar_outside_task_grant")
            actual = await accounts.inspect_event(
                session_id, account, args.calendar_id, args.event_id
            )
            if (
                actual.id != args.event_id
                or matching_provider_etag(actual.etag, args.etag) is None
                or actual.status == "cancelled"
                or actual.recurrence
            ):
                raise ValueError("calendar_target_changed")
            target = (
                await self.harness.repository.work_item(work.target_id) if work.target_id else None
            )
            if target is not None and target.revision != work.expected_revision:
                raise ValueError("work_target_revision_changed")
            bindings = (target or work).bindings
            known = [
                b for b in bindings if b.get("provider") == "google" and b.get("verified") is True
            ]
            bound = any(
                b.get("account_id") == account
                and b.get("collection_id") == args.calendar_id
                and isinstance(item := b.get("item"), dict)
                and item.get("id") == args.event_id
                for b in known
            )
            if task and known and not bound:
                raise ValueError("calendar_binding_mismatch")
            if task and not bound:
                expected = target or work
                title = expected.title
                start = expected.starts_at if target else work.previous_starts_at or work.starts_at
                end = expected.ends_at if target else work.previous_ends_at or work.ends_at
                start_day, end_day = expected.start_date, expected.end_date
                if start_day is not None and end_day is not None:
                    start = datetime.combine(start_day, time.min, ZoneInfo(work.timezone))
                    end = datetime.combine(end_day, time.min, ZoneInfo(work.timezone))
                if start is None or end is None:
                    raise ValueError("original_event_time_required")

                def matches(event: CalendarEvent) -> bool:
                    when = event.original_start or event.start
                    if start_day is not None and end_day is not None:
                        return event.title == title and (
                            (
                                event.start is not None
                                and event.end is not None
                                and event.start.day == start_day
                                and event.end.day == end_day
                            )
                            or (
                                event.original_start is not None
                                and event.original_start.day == start_day
                            )
                        )
                    return event.title == title and (
                        (
                            event.start is not None
                            and event.start.timestamp == start
                            and event.end is not None
                            and event.end.timestamp == end
                        )
                        or (
                            when is not None
                            and when.timestamp == start
                            and event.original_start is not None
                        )
                    )

                if not matches(actual):
                    raise ValueError("calendar_identity_mismatch")
                low = min(start, work.starts_at or start) - timedelta(minutes=1)
                high = max(end, work.ends_at or end) + timedelta(minutes=1)
                if high - low > timedelta(days=31):
                    raise ValueError("calendar_match_window_exceeded")
                candidates: set[tuple[str, str, str]] = set()
                for scope in scopes:
                    if "/" not in scope:
                        if scope != args.calendar_id:
                            continue
                        source_account, calendar = account, scope
                    else:
                        source_account, calendar = scope.split("/", 1)
                    for event in await accounts.query(
                        session_id, source_account, calendar, low, high
                    ):
                        if matches(event):
                            candidates.add((source_account, calendar, event.id))
                if candidates != {(account, args.calendar_id, args.event_id)}:
                    raise ValueError("calendar_target_ambiguous")
            if task:
                await self.harness.validate_task(task)
            receipt = cast(
                JsonObject,
                json.loads(
                    json.dumps(
                        {
                            "provider": "google",
                            "verified": True,
                            "state": "matched",
                            "account_id": account,
                            "collection_id": args.calendar_id,
                            "item": asdict(actual),
                            "verification_source": "provider_readback",
                        },
                        default=str,
                    )
                ),
            )
            if not await self.harness.repository.bind_work(work, receipt):
                raise ValueError("work_revision_conflict")
            binding_saved = True
            await self.harness.repository.record(
                owner,
                "binding",
                work.work_item_id,
                "verified",
                "已核对并关联唯一日程，尚未执行修改。",
                work.evidence,
            )
            return receipt
        except (ValueError, PermissionError) as error:
            target_changed = str(error) == "work_target_revision_changed"
            raise SkillExecutionError(
                "work_match_state_unknown" if binding_saved else "work_match_needs_review",
                "目标事项修订已变化，旧提案不能继续。请重新核对当前事项，等待重评或向主人澄清。"
                if target_changed
                else "无法唯一核对该日程。请检查来源、原定时间和当前版本，再查询或向主人核对。",
                details={"reason": "work_target_revision_changed"} if target_changed else {},
            ) from None
