"""Deterministic provider reconciliation, independent of model/observation workers."""

import asyncio
import json
from dataclasses import asdict
from datetime import UTC, date, datetime, timedelta
from typing import cast
from uuid import NAMESPACE_URL, UUID, uuid5

from chatwaifu_protocol.base import JsonObject
from chatwaifu_protocol.harness import EvidenceRef, WorkItem

from chatwaifu_runtime.agent.harness import AgentHarnessService
from chatwaifu_runtime.personal_assistant.calendar_push import CalendarPushService
from chatwaifu_runtime.personal_assistant.google_calendar import CalendarEvent, GoogleCalendarError
from chatwaifu_runtime.personal_assistant.integration import PersonalAssistantIntegration


class CalendarObserver:
    def __init__(
        self,
        harness: AgentHarnessService,
        integration: PersonalAssistantIntegration,
        push: CalendarPushService | None = None,
    ) -> None:
        self.harness, self.integration, self.push = harness, integration, push
        self._limit = asyncio.Semaphore(4)

    async def reconcile(self) -> None:
        if self.integration.accounts is None:
            return
        session = await self.harness.session()
        targets: set[tuple[str, str]] = set()
        for intent in await self.harness.repository.intents("local"):
            if (
                intent.state != "active"
                or intent.shadow
                or not (intent.observe_calendar or not intent.room_ids)
            ):
                continue
            for scope in intent.calendar_ids:
                if "/" in scope:
                    account, collection = scope.split("/", 1)
                    targets.add((account, collection))
        cursor = 0
        while True:
            bound = await self.harness.repository.bound_work("local", cursor)
            if not bound:
                break
            for _row, work in bound:
                if work.kind != "event":
                    continue
                for binding in work.bindings:
                    account, calendar = binding.get("account_id"), binding.get("collection_id")
                    if (
                        binding.get("provider") == "google"
                        and isinstance(account, str)
                        and isinstance(calendar, str)
                    ):
                        targets.add((account, calendar))
            cursor = bound[-1][0]
        hints = await self.push.dirty() if self.push else []
        if self.push:
            self.push.observe(str(session), targets)
        for account, calendar in targets:
            try:
                await self.integration.accounts.sync(str(session), account, calendar)
                events = await self.integration.accounts.query(
                    str(session),
                    account,
                    calendar,
                    datetime.now(UTC),
                    datetime.now(UTC) + timedelta(days=30),
                )
                await self.synced(str(session), account, calendar, events)
                await self.harness.repository.provider_watermark(
                    "local", f"{account}/{calendar}", True
                )
                if self.push:
                    for hint in hints:
                        if (hint.account_id, hint.calendar_id) == (account, calendar):
                            await self.push.repository.consume(hint.channel_id, hint.dirty_revision)
            except asyncio.CancelledError:
                raise
            except Exception:
                continue
        cursor = 0
        while True:
            rows = await self.harness.repository.bound_work("local", cursor)
            if not rows:
                return
            await asyncio.gather(*(self._one(work) for _, work in rows))
            cursor = rows[-1][0]

    async def _one(self, work: WorkItem) -> None:
        accounts = self.integration.accounts
        if accounts is None:
            return
        if not await self.harness.eligible(work.owner, work.evidence):
            return
        session = await self.harness.session()
        for binding in work.bindings:
            if binding.get("provider") != "google":
                continue
            item = binding.get("item")
            if not isinstance(item, dict) or not item.get("id"):
                continue
            previous = cast(JsonObject, item)
            account, collection = binding.get("account_id"), binding.get("collection_id")
            if not isinstance(account, str) or not isinstance(collection, str):
                continue
            async with self._limit:
                try:
                    if work.kind == "event":
                        actual = await accounts.inspect_event(
                            str(session), account, collection, str(previous["id"])
                        )
                        await self.apply(
                            work,
                            account,
                            collection,
                            cast(JsonObject, json.loads(json.dumps(asdict(actual), default=str))),
                        )
                    elif work.kind == "todo":
                        actual_task = await accounts.inspect_task(
                            str(session), account, collection, str(previous["id"])
                        )
                        await self.apply(
                            work,
                            account,
                            collection,
                            cast(
                                JsonObject, json.loads(json.dumps(asdict(actual_task), default=str))
                            ),
                        )
                except GoogleCalendarError as error:
                    if error.code == "item_not_found":
                        await self.apply(
                            work, account, collection, {"id": previous["id"], "status": "cancelled"}
                        )
                except asyncio.CancelledError:
                    raise
                except Exception:
                    # Configuration/offline failures do not turn into guesses or writes.
                    continue

    async def synced(
        self, session_id: str, account: str, collection: str, events: tuple[CalendarEvent, ...]
    ) -> None:
        owner = await self.harness.owner(UUID(session_id))
        for event in events:
            work = await self.harness.repository.bound_item(owner, account, collection, event.id)
            if work is not None:
                await self.apply(
                    work,
                    account,
                    collection,
                    cast(JsonObject, json.loads(json.dumps(asdict(event), default=str))),
                )
            elif (
                not event.recurrence
                and event.status != "cancelled"
                and event.start
                and event.end
                and (
                    (event.end.timestamp is not None and event.end.timestamp > datetime.now(UTC))
                    or (event.end.day is not None and event.end.day >= datetime.now(UTC).date())
                )
            ):
                await self._import(owner, account, collection, event)

    async def _import(
        self, owner: str, account: str, collection: str, event: CalendarEvent
    ) -> None:
        if event.start is None or event.end is None:
            return
        identifier = "work:" + str(
            uuid5(NAMESPACE_URL, f"google:{owner}:{account}:{collection}:{event.id}")
        )
        if await self.harness.repository.work_item(identifier) is not None:
            return
        receipt = cast(
            JsonObject,
            json.loads(
                json.dumps(
                    {
                        "provider": "google",
                        "verified": True,
                        "account_id": account,
                        "collection_id": collection,
                        "item": asdict(event),
                        "verification_source": "provider_readback",
                    },
                    default=str,
                )
            ),
        )
        # First discovery is backfill; it never launches retrospective actions.
        draft = WorkItem(
            work_item_id=identifier,
            owner=owner,
            room_id="calendar:import",
            kind="event",
            title=event.title or "日历活动",
            details="选定日历中的活动;未推断出席或个人承诺。",
            state="open",
            acceptance="policy",
            evidence=[EvidenceRef(kind="knowledge", reference="pending", revision=1)],
            start_date=event.start.day,
            end_date=event.end.day,
            starts_at=event.start.timestamp,
            ends_at=event.end.timestamp,
            bindings=[receipt],
            involvement="self",
            updated_at=datetime.now(UTC),
        )
        watermark = await self.harness.repository.provider_watermark(
            owner, f"{account}/{collection}"
        )
        historical = (
            watermark is None
            or event.updated_at is None
            or int(event.updated_at.timestamp() * 1000) < watermark
        )
        evidence = await self.harness.repository.capture_snapshot(
            draft, receipt, historical=historical
        )
        draft = draft.model_copy(
            update={
                "evidence": [evidence],
                "ingestion_mode": "historical" if historical else "live",
            }
        )
        if await self.harness.repository.save_work(draft, 0):
            await self.harness.publish_work(draft)

    async def apply(self, work: WorkItem, account: str, collection: str, item: JsonObject) -> None:
        current = await self.harness.repository.work_item(work.work_item_id)
        if current is None or current.revision != work.revision:
            return
        if await self.harness.repository.work_mutating(work):
            return
        previous = next(
            (
                binding
                for binding in work.bindings
                if binding.get("account_id") == account
                and binding.get("collection_id") == collection
                and isinstance(binding.get("item"), dict)
                and cast(JsonObject, binding["item"]).get("id") == item.get("id")
            ),
            None,
        )
        if previous is None:
            return
        before = cast(JsonObject, previous["item"])
        if before.get("etag") == item.get("etag") and before.get("status") == item.get("status"):
            return
        if before.get("recurring_event_id") != item.get("recurring_event_id") or before.get(
            "original_start"
        ) != item.get("original_start"):
            if item.get("status") != "cancelled":
                return
        receipt: JsonObject = {
            "provider": "google",
            "account_id": account,
            "collection_id": collection,
            "item": item,
            "verified": True,
            "verification_source": "provider_readback",
        }

        def moment(key: str) -> datetime | None:
            raw = item.get(key)
            value = raw.get("timestamp") if isinstance(raw, dict) else None
            return datetime.fromisoformat(value) if isinstance(value, str) else None

        start, end = moment("start"), moment("end")
        updated = item.get("updated_at")
        changed_at = (
            datetime.fromisoformat(updated) if isinstance(updated, str) else datetime.now(UTC)
        )
        source = await self.harness.repository.capture_snapshot(work, receipt)
        reminder = work.remind_at
        if work.reminder_rule == "relative" and work.reminder_offset_seconds is not None:
            reminder = start - timedelta(seconds=work.reminder_offset_seconds) if start else None
        state = (
            "cancelled"
            if item.get("status") == "cancelled"
            else "completed"
            if item.get("status") == "completed"
            else "blocked"
        )
        raw_due = item.get("due")
        due = date.fromisoformat(raw_due) if isinstance(raw_due, str) else None
        changed = work.model_copy(
            update={
                "revision": work.revision + 1,
                "title": item.get("title") or work.title,
                "start_date": date.fromisoformat(
                    str(cast(JsonObject, item.get("start", {})).get("day"))
                )
                if isinstance(item.get("start"), dict)
                and cast(JsonObject, item["start"]).get("day")
                else None,
                "end_date": date.fromisoformat(
                    str(cast(JsonObject, item.get("end", {})).get("day"))
                )
                if isinstance(item.get("end"), dict) and cast(JsonObject, item["end"]).get("day")
                else None,
                "starts_at": start if work.kind == "event" else work.starts_at,
                "ends_at": end if work.kind == "event" else work.ends_at,
                "due_date": due if work.kind == "todo" else work.due_date,
                "due_at": None if work.kind == "todo" and due != work.due_date else work.due_at,
                "remind_at": None if state in {"completed", "cancelled"} else reminder,
                "state": state,
                "waiting_reason": "外部对象已变化，已同步当前状态;自动修改需依据新资料重新核对。"
                if state == "blocked"
                else None,
                "bindings": [receipt],
                "evidence": [source],
                "task_id": None,
                "ingestion_mode": "live",
                "provider_updated_at": changed_at,
                "updated_at": datetime.now(UTC),
            }
        )
        if await self.harness.repository.save_work(changed, work.revision):
            await self.harness.publish_work(changed)
            await self.harness.repository.record(
                work.owner,
                "action.event" if work.kind == "event" else "action.todo",
                work.work_item_id,
                "verified",
                "外部事项的变化已核对，当前事项和提醒已同步。",
                [source],
                dedup_key=f"provider:{account}:{collection}:{item.get('id')}:{item.get('etag')}:{state}",
            )
            self.harness.notify()
