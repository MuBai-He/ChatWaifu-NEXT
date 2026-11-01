"""One owner inbox; decisions stay with the existing memory and knowledge owners."""

import hashlib
import json
from datetime import UTC, datetime
from typing import Literal, cast
from uuid import UUID, uuid4, uuid5

from chatwaifu_protocol.base import JsonObject, JsonValue
from chatwaifu_protocol.harness import WorkItemAction, WorkItemEdit
from pydantic import BaseModel, ConfigDict, Field

from chatwaifu_runtime.agent.harness import AgentHarnessService
from chatwaifu_runtime.memory.service import MemoryService


class ReviewDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")
    session_id: UUID
    decision: Literal["accept", "reject", "keep_local", "keep_server"]
    operation_id: UUID = Field(default_factory=uuid4)
    expected_revision: int = Field(default=0, ge=0)


class HarnessReviewService:
    def __init__(self, harness: AgentHarnessService, memory: MemoryService) -> None:
        self.harness, self.memory = harness, memory

    async def list(self, session: UUID) -> list[JsonObject]:
        owner = await self.harness.owner(session)
        result: list[JsonObject] = []
        for proposal in await self.memory.list_proposals(status="pending", session_id=session):
            if proposal.candidate is None:
                continue
            result.append(
                {
                    "kind": "memory",
                    "reference": str(proposal.proposal_id),
                    "title": "共同记忆核对",
                    "text": proposal.candidate.text,
                    "reason": proposal.rationale,
                    "revision": 0,
                    "evidence": cast(
                        JsonValue,
                        await self.memory.proposal_evidence(session, proposal.proposal_id),
                    ),
                }
            )
        for conflict in await self.harness.knowledge.repository.conflicts(owner):
            proposed = conflict.get("proposed")
            draft = proposed.get("record") if isinstance(proposed, dict) else None
            if not isinstance(draft, dict):
                continue
            current = await self.harness.knowledge.repository.get(owner, str(conflict["record_id"]))
            result.append(
                {
                    "kind": "sync",
                    ("current_text"): f"{current.title}\n{current.text}\n{
                        current.origin.get('start_date')
                        or (
                            datetime.fromtimestamp(current.starts_at / 1000, UTC).isoformat()
                            if current.starts_at
                            else ()
                        )
                    }"
                    if current
                    else "服务端记录已不可用",
                    "reference": conflict["conflict_id"],
                    "title": draft.get("title") or "离线修改冲突",
                    "text": draft.get("text", ""),
                    "reason": "本机修改与服务端新版本不同，请选择保留哪一版。",
                    "revision": current.revision if current else conflict["current_revision"],
                    "record_id": conflict["record_id"],
                }
            )
        return result

    async def decide(self, kind: str, reference: str, body: ReviewDecision) -> JsonObject:
        owner = await self.harness.owner(body.session_id)
        digest = hashlib.sha256(
            json.dumps(
                {
                    "kind": kind,
                    "reference": reference,
                    "decision": body.decision,
                    "expected_revision": body.expected_revision,
                },
                sort_keys=True,
            ).encode()
        ).hexdigest()
        if await self.harness.repository.command(owner, body.operation_id, digest):
            return {"accepted": True, "reference": reference}
        if kind == "memory" and body.decision in {"accept", "reject"}:
            result = await self.memory.decide_proposal(
                body.session_id, UUID(reference), cast(Literal["accept", "reject"], body.decision)
            )
            data = cast(JsonObject, result.model_dump(mode="json"))
        elif kind == "sync" and body.decision in {"keep_local", "keep_server"}:
            conflict = next(
                (
                    item
                    for item in await self.harness.knowledge.repository.conflicts(owner)
                    if item.get("conflict_id") == reference
                ),
                None,
            )
            current = (
                await self.harness.knowledge.repository.get(owner, str(conflict["record_id"]))
                if conflict
                else None
            )
            external = (
                current is not None
                and current.origin.get("external_bound") is True
                and body.decision == "keep_local"
            )
            pending: JsonObject | None = None
            if external:
                if (
                    current is None
                    or current.revision != body.expected_revision
                    or conflict is None
                ):
                    raise ValueError("review_revision_changed")
                work_id = current.origin.get("work_item_id")
                work = (
                    await self.harness.repository.work_item(work_id)
                    if isinstance(work_id, str)
                    else None
                )
                mutation = conflict.get("proposed")
                record = mutation.get("record") if isinstance(mutation, dict) else None
                if work is None or not isinstance(record, dict):
                    raise ValueError("external_work_not_available")
                changes: JsonObject = {"title": record.get("title"), "details": record.get("text")}
                for key in ("due_at", "starts_at", "ends_at", "remind_at"):
                    value = record.get(key)
                    if isinstance(value, int):
                        changes[key] = datetime.fromtimestamp(value / 1000, UTC).isoformat()
                    elif key in record:
                        changes[key] = None
                if "due_date" in record:
                    changes["due_date"] = record["due_date"]
                action = (
                    "complete"
                    if record.get("state") == "completed"
                    else "cancel"
                    if record.get("state") == "cancelled"
                    else "edit"
                )
                item = await self.harness.act_work(
                    work.work_item_id,
                    WorkItemAction(
                        session_id=body.session_id,
                        operation_id=uuid5(body.operation_id, "external-edit"),
                        expected_revision=work.revision,
                        action=action,
                        changes=WorkItemEdit.model_validate(changes),
                    ),
                )
                pending = cast(JsonObject, item.model_dump(mode="json"))
            latest = (
                await self.harness.knowledge.repository.get(owner, str(conflict["record_id"]))
                if conflict
                else None
            )
            data = await self.harness.knowledge.repository.resolve_conflict(
                owner,
                reference,
                body.decision == "keep_local" and not external,
                str(body.operation_id),
                latest.revision if external and latest else body.expected_revision,
            )
            if pending is not None:
                data.update({"external_pending": True, "work_item": pending})
            if self.harness.context_changed is not None:
                epoch = self.harness.context_epoch() if self.harness.context_epoch else 0
                self.harness.context_changed(epoch, None)
        else:
            raise ValueError("invalid_review_decision")
        await self.harness.repository.record(
            owner,
            "review",
            reference,
            "human",
            "主人已核对记忆或离线冲突。",
            details={"kind": kind, "decision": body.decision},
            dedup_key="review:" + str(body.operation_id),
        )
        await self.harness.repository.finish_command(
            owner, body.operation_id, digest, "review:" + reference
        )
        self.harness.notify()
        return data
