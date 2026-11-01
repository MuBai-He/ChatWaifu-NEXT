"""Owner instructions and verified interactive effects join the same current matters."""

import json
from datetime import UTC, date, datetime, timedelta
from typing import Literal, cast
from uuid import UUID, uuid4, uuid5

from chatwaifu_protocol.agent import AgentTaskCreate, TaskAuthorization
from chatwaifu_protocol.base import JsonObject
from chatwaifu_protocol.harness import EvidenceRef, WaitCondition, WorkItem
from pydantic import AwareDatetime, BaseModel, ConfigDict, Field

from chatwaifu_runtime.agent.harness import AgentHarnessService
from chatwaifu_runtime.knowledge.contracts import Mutation, RecordDraft
from chatwaifu_runtime.runtime_skills.errors import SkillExecutionError
from chatwaifu_runtime.runtime_skills.execution_context import (
    authorized_action,
    authorized_generation,
    authorized_task,
)


class LocalArguments(BaseModel):
    model_config = ConfigDict(extra="forbid")
    title: str | None = Field(default=None, min_length=1, max_length=200)
    kind: Literal["todo", "event"] = "todo"
    details: str = Field(default="", max_length=1200)
    due_date: date | None = None
    due_at: AwareDatetime | None = None
    starts_at: AwareDatetime | None = None
    ends_at: AwareDatetime | None = None
    remind_at: AwareDatetime | None = None


class LocalWorkSkills:
    def __init__(self, harness: AgentHarnessService) -> None:
        self.harness = harness

    async def _evidence(self, session: UUID, identifier: UUID, data: JsonObject) -> EvidenceRef:
        owner = await self.harness.owner(session)
        context = authorized_generation.get()
        record_id = "runtime-effect:" + str(identifier)
        previous = await self.harness.knowledge.repository.get(owner, record_id)
        if previous is not None:
            return EvidenceRef(kind="knowledge", reference=record_id, revision=previous.revision)
        draft = RecordDraft(
            record_id=record_id,
            kind="source",
            room_id="owner:instructions",
            title="主人交办与工具核验",
            text=json.dumps(data, ensure_ascii=False),
            occurred_at=int(datetime.now(UTC).timestamp() * 1000),
            sender_name="宁宁工具核验",
            origin={
                "source_type": "verified_runtime_effect",
                "session_id": str(session),
                "turn_id": str(context.turn_id) if context and context.turn_id else None,
                "generation_id": str(context.generation_id)
                if context and context.generation_id
                else None,
                "operation_id": str(identifier),
                "_trusted_adapter": True,
            },
        )
        await self.harness.knowledge.repository.apply_mutation(
            owner,
            Mutation(operation_id=uuid5(identifier, "evidence"), expected_revision=0, record=draft),
        )
        return EvidenceRef(kind="knowledge", reference=record_id, revision=1)

    async def manage(self, session_id: str, arguments: JsonObject) -> JsonObject:
        try:
            args = LocalArguments.model_validate(arguments)
            session = UUID(session_id)
            owner = await self.harness.owner(session)
            task_id = authorized_task.get()
            task = await self.harness.tasks.repository.get(task_id) if task_id else None
            intent = None
            if task and task.authorization.intent_id:
                intent = await self.harness.repository.intent(task.authorization.intent_id)
                if intent and intent.schedule and intent.schedule.kind == "remind":
                    if args.title is not None and args.title != intent.schedule.title:
                        raise ValueError("scheduled_title_changed")
                    if (
                        args.remind_at is not None
                        and args.remind_at != task.authorization.scheduled_for
                    ):
                        raise ValueError("scheduled_time_changed")
                    args = args.model_copy(
                        update={
                            "title": intent.schedule.title,
                            "remind_at": task.authorization.scheduled_for,
                        }
                    )
            current = (
                await self.harness.repository.work_item(task.work_item_id)
                if task and task.work_item_id
                else None
            )
            if (
                task
                and task.authorization.intent_id
                and current is None
                and not (intent and intent.schedule and intent.schedule.kind == "remind")
            ):
                raise ValueError("current_work_required")
            if current and current.kind not in {"todo", "event"}:
                raise ValueError("local_work_kind_not_supported")
            if current:
                if args.title is not None and args.title != current.title:
                    raise ValueError("work_title_changed")
                if args.remind_at is not None and args.remind_at != current.remind_at:
                    raise ValueError("reminder_time_not_grounded")
                work = current
            else:
                if args.title is None:
                    raise ValueError("title_required")
                action = authorized_action.get() or uuid4()
                evidence = await self._evidence(
                    session, action, cast(JsonObject, args.model_dump(mode="json"))
                )
                work = WorkItem(
                    work_item_id="work:" + str(uuid5(action, "work")),
                    owner=owner,
                    room_id="owner:instructions",
                    source_room_ids=["owner:instructions"],
                    ingestion_mode="live",
                    kind=args.kind,
                    title=args.title,
                    details=args.details,
                    state="proposed",
                    acceptance="pending",
                    evidence=[evidence],
                    due_date=args.due_date,
                    due_at=args.due_at,
                    starts_at=args.starts_at,
                    ends_at=args.ends_at,
                    remind_at=args.remind_at,
                    primary_device_id=await self.harness.repository.primary_device(
                        owner, "owner:instructions"
                    ),
                    involvement="self",
                    task_id=task_id,
                    updated_at=datetime.now(UTC),
                )
                if not await self.harness.repository.save_work(work, 0):
                    previous = await self.harness.repository.work_item(work.work_item_id)
                    if previous is None:
                        raise ValueError("work_missing")
                    work = previous
                if task and task.work_item_id is None:
                    task = await self.harness.tasks.update_checkpoint(
                        task.task_id, work_item_id=work.work_item_id
                    )
            changed = work.model_copy(
                update={
                    "revision": work.revision + 1,
                    "state": "waiting" if work.remind_at else "open",
                    "acceptance": "policy" if task else "human",
                    "task_id": task.task_id if task else work.task_id,
                    "waiting_reason": "等待主提醒设备接收计划。" if work.remind_at else None,
                }
            )
            if not await self.harness.repository.save_work(changed, work.revision):
                raise ValueError("work_revision_conflict")
            await self.harness.publish_work(changed, producer=task.task_id if task else None)
            occurrence = await self.harness.repository.reminder(changed)
            if task is None and occurrence is not None:
                action = authorized_action.get() or uuid4()
                task = await self.harness.tasks.create(
                    AgentTaskCreate(
                        session_id=session,
                        goal="将主人指定的提醒计划送达主设备。",
                        authorization=TaskAuthorization(
                            allowed_skill_ids=["work.manage"],
                            source_ref="owner-instruction:" + str(action),
                            allow_writes=True,
                            expires_at=datetime.now(UTC) + timedelta(hours=24),
                        ),
                        max_tool_calls=20,
                        max_active_seconds=300,
                    ),
                    task_id=uuid5(action, "delivery"),
                    work_item_id=changed.work_item_id,
                    initial_wait=WaitCondition(
                        kind="device", reference=str(occurrence["occurrence_id"])
                    ),
                )
                saved = changed.model_copy(
                    update={"task_id": task.task_id, "revision": changed.revision + 1}
                )
                await self.harness.repository.save_work(saved, changed.revision)
                changed = saved
                key = "imported-rule:" + str(action)
                proof: JsonObject = {
                    "step_key": key,
                    "skill_id": "work.manage",
                    "capability": "execute",
                    "side_effect": "write",
                    "state": "settled",
                    "ok": True,
                    "work_item_id": changed.work_item_id,
                    "runtime_skill_run_id": str(action),
                    "snapshot": {
                        "result": {
                            "data": {
                                "verified": True,
                                "provider": "runtime",
                                "work_item_id": changed.work_item_id,
                            }
                        }
                    },
                }
                await self.harness.tasks.repository.begin_step(task.task_id, key, proof)
                await self.harness.tasks.repository.finish_step(task.task_id, key, proof)
            if task and occurrence is not None and task_id is not None:
                await self.harness.tasks.wait_for_device(
                    task.task_id, str(occurrence["occurrence_id"]), "等待主提醒设备接收计划。"
                )
            await self.harness.repository.record(
                owner,
                "work",
                changed.work_item_id,
                changed.state,
                "事项已保存，提醒接收状态单独核对。",
                changed.evidence,
            )
            return {
                "work_item_id": changed.work_item_id,
                "provider": "runtime",
                "verified": True,
                "state": "waiting_device" if occurrence else "saved",
                "occurrence": occurrence,
            }
        except (ValueError, PermissionError):
            raise SkillExecutionError(
                "local_work_needs_review",
                "事项或提醒依据不足。请核对标题、明确时间和当前版本后继续。",
            ) from None

    async def observe_agenda(
        self, session_id: str, arguments: JsonObject, result: JsonObject
    ) -> None:
        if result.get("verified") is not True:
            return
        task_id = authorized_task.get()
        task = await self.harness.tasks.repository.get(task_id) if task_id else None
        if task and task.work_item_id:
            return
        action = authorized_action.get()
        if action is None:
            return
        owner = await self.harness.owner(UUID(session_id))
        item = result.get("item")
        data = cast(JsonObject, item) if isinstance(item, dict) else {}
        item_id = data.get("id") or result.get("item_id")
        existing = await self.harness.repository.bound_item(
            owner, str(result.get("account_id")), str(result.get("collection_id")), str(item_id)
        )
        evidence = await self._evidence(UUID(session_id), action, result)

        def moment(key: str) -> datetime | None:
            value = data.get(key)
            raw = value.get("timestamp") if isinstance(value, dict) else None
            return datetime.fromisoformat(raw) if isinstance(raw, str) else None

        due = data.get("due")
        work = WorkItem(
            work_item_id=existing.work_item_id
            if existing
            else "work:" + str(uuid5(action, "work")),
            owner=owner,
            room_id=existing.room_id if existing else "owner:instructions",
            source_room_ids=existing.source_room_ids if existing else ["owner:instructions"],
            legacy_record_id=existing.legacy_record_id if existing else None,
            ingestion_mode="live",
            kind="event" if arguments.get("kind") == "calendar" else "todo",
            title=str(
                data.get("title")
                or (existing.title if existing else arguments.get("title") or "个人事项")
            ),
            details=str(data.get("description") or data.get("notes") or ""),
            state="cancelled"
            if result.get("state") == "deleted"
            else "completed"
            if data.get("status") == "completed"
            else "open",
            acceptance="policy" if task else "human",
            evidence=[evidence],
            revision=existing.revision + 1 if existing else 1,
            start_date=date.fromisoformat(str(cast(JsonObject, data["start"]).get("day")))
            if isinstance(data.get("start"), dict) and cast(JsonObject, data["start"]).get("day")
            else None,
            end_date=date.fromisoformat(str(cast(JsonObject, data["end"]).get("day")))
            if isinstance(data.get("end"), dict) and cast(JsonObject, data["end"]).get("day")
            else None,
            starts_at=moment("start"),
            ends_at=moment("end"),
            due_date=date.fromisoformat(due) if isinstance(due, str) else None,
            primary_device_id=existing.primary_device_id
            if existing
            else await self.harness.repository.primary_device(owner, "owner:instructions"),
            remind_at=existing.remind_at if existing else None,
            reminder_rule=existing.reminder_rule if existing else "absolute",
            reminder_offset_seconds=existing.reminder_offset_seconds if existing else None,
            involvement="self",
            bindings=[result],
            updated_at=datetime.now(UTC),
        )
        if not await self.harness.repository.save_work(work, existing.revision if existing else 0):
            return
        await self.harness.publish_work(work, producer=task.task_id if task else None)
        if task:
            await self.harness.tasks.update_checkpoint(task.task_id, work_item_id=work.work_item_id)
        result["work_item_id"] = work.work_item_id
        await self.harness.repository.record(
            owner,
            "action." + work.kind,
            work.work_item_id,
            "verified",
            "已回读核验交办的事项。",
            work.evidence,
        )
