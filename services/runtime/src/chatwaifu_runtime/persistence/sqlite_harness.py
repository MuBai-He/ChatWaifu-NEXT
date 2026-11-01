"""Transactional source outbox, execution handoff and CAS projections in SQLite."""

from __future__ import annotations

import hashlib
import json
import time
from collections.abc import Callable, Sequence
from datetime import UTC, datetime
from typing import Literal, cast
from uuid import NAMESPACE_URL, UUID, uuid5

import aiosqlite
from chatwaifu_protocol.agent import AgentTask
from chatwaifu_protocol.base import JsonObject
from chatwaifu_protocol.harness import (
    ActivityEntry,
    EvidenceRef,
    Observation,
    StandingIntent,
    WorkItem,
)

from chatwaifu_runtime.knowledge.contracts import Record
from chatwaifu_runtime.knowledge.errors import KnowledgeConflict
from chatwaifu_runtime.persistence.database import Database
from chatwaifu_runtime.persistence.harness_reminders import native_receipt_in, project_reminder_in


def now_ms() -> int:
    return int(time.time() * 1000)


async def fetch_rows(
    c: aiosqlite.Connection, query: str, args: Sequence[object] = ()
) -> list[aiosqlite.Row]:
    return list(await c.execute_fetchall(query, args))


def instant() -> datetime:
    return datetime.now(UTC)


async def observe_in(
    c: aiosqlite.Connection,
    owner: str,
    record: Record,
    mode: str,
    epoch: int,
    *,
    precise_capture: bool = False,
) -> None:
    """Called within source ingestion's transaction; never dispatch before commit."""
    if record.kind != "source":
        return
    rows = await fetch_rows(
        c,
        "SELECT state,watermark FROM harness_processing WHERE owner=? AND room_id=?",
        (owner, record.room_id),
    )
    if not rows or rows[0][0] != "runtime":
        return
    await c.execute(
        (
            "UPDATE harness_observations SET state='superseded' WHERE owner=? "
            "AND reference=? AND revision<? AND state IN "
            "('pending','running','failed')"
        ),
        (owner, record.record_id, record.revision),
    )
    if record.deleted:
        return
    actual = (
        "historical"
        if mode == "historical" or (not precise_capture and record.occurred_at <= int(rows[0][1]))
        else "delayed"
        if mode == "delayed" or record.occurred_at < now_ms() - 300000
        else "live"
    )
    observation = Observation(
        observation_id=uuid5(NAMESPACE_URL, f"{owner}:{record.record_id}:v{record.revision}"),
        owner=owner,
        room_id=record.room_id,
        evidence=EvidenceRef(
            kind="knowledge", reference=record.record_id, revision=record.revision
        ),
        ingestion_mode=actual,
        occurred_at=record.occurred_at,
        received_at=now_ms(),
        policy_epoch=epoch,
    )
    # History is a durable metadata journal alongside the source archive, not
    # an in-memory runnable queue. Its backlog must never roll back a live push.
    # Claims, leases and model admission bound execution independently.
    due = now_ms() + (3000 if actual != "historical" else 0)
    await c.execute(
        (
            "INSERT OR IGNORE INTO harness_observations "
            "(observation_id,owner,room_id,reference,revision,state,available_"
            "at,payload) VALUES(?,?,?,?,?,'pending',?,?)"
        ),
        (
            str(observation.observation_id),
            owner,
            record.room_id,
            record.record_id,
            record.revision,
            due,
            observation.model_dump_json(),
        ),
    )
    if actual != "historical":
        pending = await fetch_rows(
            c,
            (
                "SELECT min(json_extract(payload,'$.received_at')) FROM "
                "harness_observations WHERE owner=? AND room_id=? AND st"
                "ate='pending' AND json_extract(payload,'$.ingestion_mod"
                "e')!='historical'"
            ),
            (owner, record.room_id),
        )
        first_received = int(pending[0][0]) if pending and pending[0][0] else now_ms()
        await c.execute(
            (
                "UPDATE harness_observations SET available_at=? WHERE ow"
                "ner=? AND room_id=? AND state='pending' AND json_extrac"
                "t(payload,'$.ingestion_mode')!='historical'"
            ),
            (min(first_received + 15000, now_ms() + 3000), owner, record.room_id),
        )


async def project_work_in(
    c: aiosqlite.Connection,
    owner: str,
    record: Record,
    *,
    trusted: bool = False,
    device: UUID | None = None,
) -> WorkItem | None:
    if record.kind not in {"todo", "event", "memory", "reference"} or not record.confirmed:
        return
    identifier = str(record.origin.get("work_item_id") or "legacy:" + record.record_id)
    if len(identifier) > 200:
        identifier = "legacy-hash:" + hashlib.sha256(record.record_id.encode()).hexdigest()
    rows = await fetch_rows(
        c,
        "SELECT payload FROM harness_work_items WHERE work_item_id=? AND owner=?",
        (identifier, owner),
    )
    current = WorkItem.model_validate_json(rows[0][0]) if rows else None
    if trusted or (current is None and record.kind in {"memory", "reference"}):
        return
    if current:
        projection = await fetch_rows(
            c,
            (
                "SELECT record_id,record_revision,work_revision FROM har"
                "ness_projections WHERE owner=? AND work_item_id=?"
            ),
            (owner, identifier),
        )
        if projection and (
            projection[0][0] != record.record_id
            or projection[0][1] != record.revision - 1
            or projection[0][2] != current.revision
        ):
            raise KnowledgeConflict("work_projection_changed")
    if (
        current
        and record.origin.get("source_type") == "agent_harness"
        and record.origin.get("work_revision") != current.revision
    ):
        raise KnowledgeConflict("work_revision_conflict")

    def dt(value: int | None) -> datetime | None:
        return datetime.fromtimestamp(value / 1000, UTC) if value is not None else None

    evidence = [
        EvidenceRef(kind="knowledge", reference=r.record_id, revision=r.revision)
        for r in record.source_refs
    ]
    if not evidence:
        evidence = [
            EvidenceRef(kind="knowledge", reference=record.record_id, revision=record.revision)
        ]
    primary = current.primary_device_id if current else device
    if device is not None and record.origin.get("requested_primary_device") == str(device):
        primary = device
    receipt = record.origin.get("device_reminder")
    if device is not None and isinstance(receipt, dict):
        await native_receipt_in(c, owner, device, cast(JsonObject, receipt))
    if (
        current
        and current.kind in {"memory", "decision", "reference"}
        and (current.details != record.text or current.title != record.title)
    ):
        await suppress_in(c, owner, current.details, "corrected")
    work = WorkItem(
        work_item_id=identifier,
        legacy_record_id=record.record_id if not current else current.legacy_record_id,
        owner=owner,
        room_id=record.room_id,
        kind=current.kind if current else cast(Literal["todo", "event"], record.kind),
        source_room_ids=current.source_room_ids if current else [record.room_id],
        ingestion_mode=current.ingestion_mode if current else "historical",
        title=record.title,
        details=record.text,
        state="cancelled"
        if record.deleted
        else record.state
        if record.state != "active"
        else "open",
        revision=current.revision + 1 if current else 1,
        acceptance="human",
        evidence=evidence,
        primary_device_id=primary,
        due_date=record.due_date,
        reminder_rule=current.reminder_rule if current else "absolute",
        reminder_offset_seconds=current.reminder_offset_seconds if current else None,
        start_date=current.start_date if current else None,
        end_date=current.end_date if current else None,
        due_at=dt(record.due_at),
        starts_at=dt(record.starts_at),
        ends_at=dt(record.ends_at),
        remind_at=dt(record.remind_at),
        timezone=record.timezone,
        involvement=cast(Literal["self", "other", "unclear"], record.origin.get("involvement"))
        if record.origin.get("involvement") in {"self", "other", "unclear"}
        else current.involvement
        if current
        else "unclear",
        task_id=current.task_id if current else None,
        bindings=current.bindings if current else [],
        updated_at=instant(),
    )
    await c.execute(
        (
            "INSERT INTO harness_work_items VALUES(?,?,?,?) ON CONFL"
            "ICT(work_item_id) DO UPDATE SET revision=excluded.revis"
            "ion,payload=excluded.payload"
        ),
        (identifier, owner, work.revision, work.model_dump_json()),
    )
    await project_reminder_in(c, work)
    await c.execute(
        (
            "INSERT INTO harness_projections VALUES(?,?,?,?,?) ON CO"
            "NFLICT(owner,work_item_id) DO UPDATE SET record_revisio"
            "n=excluded.record_revision,work_revision=excluded.work_"
            "revision"
        ),
        (owner, identifier, record.record_id, record.revision, work.revision),
    )
    return work


def suppression_digest(text: str) -> str:
    return hashlib.sha256(" ".join(text.casefold().split()).encode()).hexdigest()


async def suppress_in(c: aiosqlite.Connection, owner: str, text: str, cause: str) -> None:
    if text.strip():
        await c.execute(
            "INSERT OR REPLACE INTO harness_suppressions VALUES(?,?,?)",
            (owner, suppression_digest(text), cause),
        )


async def forget_source_in(c: aiosqlite.Connection, owner: str, reference: str) -> None:
    # Scrub derived contents as part of the canonical source-forgetting transaction.
    rows = await fetch_rows(
        c,
        (
            "SELECT w.payload FROM harness_work_items w,json_each(w."
            "payload,'$.evidence') e WHERE w.owner=? AND json_extrac"
            "t(e.value,'$.reference')=?"
        ),
        (owner, reference),
    )
    previous = await fetch_rows(
        c,
        (
            "SELECT payload FROM knowledge_versions WHERE owner=? AN"
            "D record_id=? AND json_extract(payload,'$.deleted')=0 O"
            "RDER BY revision DESC LIMIT 1"
        ),
        (owner, reference),
    )
    if previous:
        original = Record.model_validate_json(previous[0][0])
        await suppress_in(c, owner, original.text, "forgotten")
        linked = str(original.origin.get("work_item_id") or "legacy:" + reference)
        rows += await fetch_rows(
            c,
            "SELECT payload FROM harness_work_items WHERE owner=? AND work_item_id=?",
            (owner, linked),
        )
    identifiers: set[str] = set()
    for row in rows:
        item = WorkItem.model_validate_json(row[0])
        identifiers.add(item.work_item_id)
        await suppress_in(c, owner, item.details, "forgotten")
        if item.memory_identity.get("predicate"):
            await suppress_in(
                c, owner, "identity:" + str(item.memory_identity["predicate"]), "forgotten"
            )
        redacted = item.model_copy(
            update={
                "title": "来源已遗忘",
                "details": "",
                "bindings": [],
                "state": "cancelled",
                "revision": item.revision + 1,
            }
        )
        await c.execute(
            "UPDATE harness_work_items SET revision=?,payload=? WHERE work_item_id=?",
            (redacted.revision, redacted.model_dump_json(), item.work_item_id),
        )
        await project_reminder_in(c, redacted)
    activity = await fetch_rows(
        c, "SELECT sequence,evidence,reference FROM harness_activity WHERE owner=?", (owner,)
    )
    for row in activity:
        if row[2] in identifiers or any(
            e.get("reference") == reference for e in json.loads(row[1])
        ):
            await c.execute(
                (
                    "UPDATE harness_activity SET summary='相关来源已遗忘',details='"
                    "{}' WHERE sequence=?"
                ),
                (row[0],),
            )
    tasks = await fetch_rows(
        c, "SELECT task_id,payload_json FROM agent_tasks WHERE scope=?", (owner,)
    )
    for row in tasks:
        payload = json.loads(row[1])
        refs = payload.get("authorization", {}).get("evidence_refs", [])
        if payload.get("work_item_id") not in identifiers and not any(
            isinstance(value, dict) and cast(JsonObject, value).get("reference") == reference
            for value in refs
        ):
            continue
        payload.update(
            goal="相关来源已遗忘",
            continuation=None,
            result_text=None,
            blocked_reason="source_forgotten",
            revision=payload["revision"] + 1,
        )
        if payload["state"] not in {"succeeded", "failed", "cancelled"}:
            payload["state"] = "cancelled"
        await c.execute(
            "UPDATE agent_tasks SET revision=?,state=?,payload_json=? WHERE task_id=?",
            (payload["revision"], payload["state"], json.dumps(payload), row[0]),
        )
        steps = await fetch_rows(
            c, "SELECT step_key,payload_json FROM agent_task_steps WHERE task_id=?", (row[0],)
        )
        for step in steps:
            value = json.loads(step[1])
            value.pop("snapshot", None)
            value["redacted"] = True
            await c.execute(
                "UPDATE agent_task_steps SET payload_json=? WHERE task_id=? AND step_key=?",
                (json.dumps(value), row[0], step[0]),
            )


class SQLiteHarnessRepository:
    def __init__(self, db: Database) -> None:
        self.db = db
        self.on_record: Callable[[], None] | None = None

    async def import_confirmed(self) -> None:
        async with self.db.transaction() as c:
            rows = await fetch_rows(
                c,
                (
                    "SELECT r.owner,r.payload FROM knowledge_records r WHERE"
                    " r.deleted=0 AND r.kind IN ('todo','event') AND json_ex"
                    "tract(r.payload,'$.confirmed')=1 AND NOT EXISTS(SELECT "
                    "1 FROM harness_projections p WHERE p.owner=r.owner AND "
                    "p.record_id=r.record_id)"
                ),
            )
            for row in rows:
                record = Record.model_validate_json(row[1])
                existing = await fetch_rows(
                    c,
                    "SELECT 1 FROM harness_work_items WHERE owner=? AND work_item_id=?",
                    (
                        row[0],
                        str(record.origin.get("work_item_id") or "legacy:" + record.record_id),
                    ),
                )
                if not existing:
                    await project_work_in(c, str(row[0]), record)

    async def projection_current(self, work: WorkItem) -> bool:
        return bool(
            await self.db.fetchall(
                (
                    "SELECT 1 FROM harness_projections p JOIN knowledge_reco"
                    "rds r ON r.owner=p.owner AND r.record_id=p.record_id AN"
                    "D r.revision=p.record_revision WHERE p.owner=? AND p.wo"
                    "rk_item_id=? AND p.work_revision=?"
                ),
                (work.owner, work.work_item_id, work.revision),
            )
        )

    async def mark_projection(self, work: WorkItem, record_id: str, record_revision: int) -> None:
        async with self.db.transaction() as c:
            await c.execute(
                (
                    "INSERT INTO harness_projections SELECT ?,?,?,?,? WHERE "
                    "EXISTS(SELECT 1 FROM harness_work_items WHERE owner=? A"
                    "ND work_item_id=? AND revision=?) ON CONFLICT(owner,wor"
                    "k_item_id) DO UPDATE SET record_id=excluded.record_id,r"
                    "ecord_revision=excluded.record_revision,work_revision=e"
                    "xcluded.work_revision"
                ),
                (
                    work.owner,
                    work.work_item_id,
                    record_id,
                    record_revision,
                    work.revision,
                    work.owner,
                    work.work_item_id,
                    work.revision,
                ),
            )

    async def pending_projections(self, owner: str) -> list[WorkItem]:
        rows = await self.db.fetchall(
            (
                "SELECT w.payload FROM harness_work_items w LEFT JOIN ha"
                "rness_projections p ON p.owner=w.owner AND p.work_item_"
                "id=w.work_item_id WHERE w.owner=? AND json_extract(w.pa"
                "yload,'$.acceptance')!='pending' AND json_extract(w.pay"
                "load,'$.state') NOT IN ('rejected','cancelled','propose"
                "d') AND (p.work_revision IS NULL OR p.work_revision<w.r"
                "evision) ORDER BY w.rowid LIMIT 20"
            ),
            (owner,),
        )
        return [WorkItem.model_validate_json(row[0]) for row in rows]

    async def request_recheck(self, intent: StandingIntent) -> None:
        await self.db.execute(
            (
                "INSERT INTO harness_intent_reche"
                "cks VALUES(?,?,0,(SELECT coalesc"
                "e(max(rowid),0) FROM harness_wor"
                "k_items)) ON CONFLICT(intent_id)"
                " DO UPDATE SET revision=excluded"
                ".revision,after_row=0,through_ro"
                "w=excluded.through_row"
            ),
            (str(intent.intent_id), intent.revision),
        )

    async def next_recheck(self) -> tuple[UUID, int, int, WorkItem | None] | None:
        rows = await self.db.fetchall(
            "SELECT intent_id,revision,after_"
            "row,through_row FROM harness_int"
            "ent_rechecks ORDER BY rowid LIMI"
            "T 1"
        )
        if not rows:
            return None
        identifier, revision, after, through = rows[0]
        work = await self.db.fetchall(
            (
                "SELECT rowid,payload FROM harnes"
                "s_work_items WHERE rowid>? AND r"
                "owid<=? ORDER BY rowid LIMIT 1"
            ),
            (after, through),
        )
        return (
            UUID(identifier),
            int(revision),
            int(work[0][0]) if work else int(through),
            WorkItem.model_validate_json(work[0][1]) if work else None,
        )

    async def advance_recheck(self, intent: UUID, revision: int, row: int) -> None:
        async with self.db.transaction() as c:
            await c.execute(
                "UPDATE harness_intent_rechecks SET after_row=? WHERE intent_id=? AND revision=?",
                (row, str(intent), revision),
            )
            await c.execute(
                (
                    "DELETE FROM harness_intent_reche"
                    "cks WHERE intent_id=? AND revisi"
                    "on=? AND after_row>=through_row"
                ),
                (str(intent), revision),
            )

    async def dispatch_known(self, intent: UUID, work: str) -> bool:
        return bool(
            await self.db.fetchall(
                (
                    "SELECT 1 FROM harness_dispatch d"
                    " JOIN agent_tasks t ON t.task_id"
                    "=d.task_id WHERE d.intent_id=? A"
                    "ND d.work_item_id=? LIMIT 1"
                ),
                (str(intent), work),
            )
        )

    async def legacy_work(self, owner: str, record_id: str) -> WorkItem | None:
        rows = await self.db.fetchall(
            (
                "SELECT payload FROM harness_work"
                "_items WHERE owner=? AND json_ex"
                "tract(payload,'$.legacy_record_i"
                "d')=? AND json_extract(payload,'"
                "$.target_id') IS NULL LIMIT 1"
            ),
            (owner, record_id),
        )
        return WorkItem.model_validate_json(rows[0][0]) if rows else None

    async def bound_item(
        self, owner: str, account: str, collection: str, identifier: str
    ) -> WorkItem | None:
        rows = await self.db.fetchall(
            (
                "SELECT w.payload FROM harness_work_items w,json_each(w."
                "payload,'$.bindings') b WHERE w.owner=? AND json_extrac"
                "t(b.value,'$.account_id')=? AND json_extract(b.value,'$"
                ".collection_id')=? AND coalesce(json_extract(b.value,'$"
                ".item.id'),json_extract(b.value,'$.item_id'))=? AND jso"
                "n_extract(w.payload,'$.target_id') IS NULL ORDER BY w.r"
                "owid DESC LIMIT 1"
            ),
            (owner, account, collection, identifier),
        )
        return WorkItem.model_validate_json(rows[0][0]) if rows else None

    async def bind_work(
        self, work: WorkItem, receipt: JsonObject, apply_change: bool = False
    ) -> bool:
        async with self.db.transaction() as c:
            current = await fetch_rows(
                c,
                "SELECT revision FROM harness_work_items WHERE work_item_id=? AND owner=?",
                (work.work_item_id, work.owner),
            )
            if not current or current[0][0] != work.revision:
                return False
            target_rows = (
                await fetch_rows(
                    c,
                    "SELECT payload FROM harness_work_items WHERE work_item_id=? AND owner=?",
                    (work.target_id, work.owner),
                )
                if work.target_id
                else []
            )
            if work.target_id and (
                not target_rows
                or WorkItem.model_validate_json(target_rows[0][0]).revision
                != work.expected_revision
            ):
                return False
            if target_rows:
                target = WorkItem.model_validate_json(target_rows[0][0])
                updates: dict[str, object] = {
                    "revision": target.revision + 1,
                    "bindings": [receipt],
                }
                if apply_change:
                    updates.update(
                        {
                            "title": work.title,
                            "details": work.details,
                            "evidence": work.evidence,
                            "due_date": work.due_date,
                            "due_at": work.due_at,
                            "start_date": work.start_date,
                            "end_date": work.end_date,
                            "starts_at": work.starts_at,
                            "ends_at": work.ends_at,
                            "remind_at": work.remind_at,
                            "reminder_rule": work.reminder_rule,
                            "reminder_offset_seconds": work.reminder_offset_seconds,
                            "state": "cancelled"
                            if work.operation == "cancel"
                            else "completed"
                            if work.operation == "complete"
                            else "waiting",
                            "acceptance": "policy",
                            "task_id": work.task_id,
                            "source_room_ids": list(
                                dict.fromkeys(
                                    [target.room_id, *target.source_room_ids, work.room_id]
                                )
                            ),
                        }
                    )
                if apply_change and work.operation in {"complete", "cancel"}:
                    for key in (
                        "title",
                        "details",
                        "due_date",
                        "due_at",
                        "starts_at",
                        "ends_at",
                        "start_date",
                        "end_date",
                    ):
                        updates[key] = getattr(target, key)
                    updates["remind_at"] = None
                target = target.model_copy(update=updates)
                await c.execute(
                    "UPDATE harness_work_items SET revision=?,payload=? WHERE work_item_id=?",
                    (target.revision, target.model_dump_json(), target.work_item_id),
                )
                work = work.model_copy(
                    update={
                        "expected_revision": target.revision,
                        "target_applied_revision": target.revision if apply_change else None,
                    }
                )
            updated = work.model_copy(update={"revision": work.revision + 1, "bindings": [receipt]})
            await c.execute(
                "UPDATE harness_work_items SET revision=?,payload=? WHERE work_item_id=?",
                (updated.revision, updated.model_dump_json(), updated.work_item_id),
            )
            return True

    async def primary_device(self, owner: str, room: str) -> UUID | None:
        rows = await self.db.fetchall(
            "SELECT device_id FROM harness_processing WHERE owner=? AND room_id=?", (owner, room)
        )
        if rows:
            return UUID(rows[0][0])
        rows = await self.db.fetchall(
            (
                "SELECT DISTINCT device_id FROM harness_processing WHERE"
                " owner=? AND state='runtime' LIMIT 2"
            ),
            (owner,),
        )
        return UUID(rows[0][0]) if len(rows) == 1 else None

    async def device_allowed(self, owner: str, device: UUID) -> bool:
        return bool(
            await self.db.fetchall(
                "SELECT 1 FROM knowledge_devices WHERE owner=? AND device_id=?",
                (owner, str(device)),
            )
        )

    async def reminder(self, work: WorkItem) -> JsonObject | None:
        async with self.db.transaction() as c:
            return await project_reminder_in(c, work)

    async def reminder_status(self, owner: str) -> list[JsonObject]:
        rows = await self.db.fetchall(
            (
                "SELECT payload FROM harness_reminders WHERE owner=? ORD"
                "ER BY scheduled_at DESC LIMIT 100"
            ),
            (owner,),
        )
        return [cast(JsonObject, json.loads(row[0])) for row in rows]

    async def provider_watermark(
        self, owner: str, scope: str, initialize: bool = False
    ) -> int | None:
        if initialize:
            await self.db.execute(
                "INSERT OR IGNORE INTO harness_provider_sources VALUES(?,?,?)",
                (owner, scope, now_ms()),
            )
        rows = await self.db.fetchall(
            "SELECT initialized_at FROM harness_provider_sources WHERE owner=? AND scope=?",
            (owner, scope),
        )
        return int(rows[0][0]) if rows else None

    async def bound_work(self, owner: str, after: int) -> list[tuple[int, WorkItem]]:
        rows = await self.db.fetchall(
            (
                "SELECT rowid,payload FROM harness_work_items WHERE owne"
                "r=? AND rowid>? AND json_extract(payload,'$.target_id')"
                " IS NULL AND json_array_length(payload,'$.bindings')>0 "
                "AND json_extract(payload,'$.state') NOT IN ('completed'"
                ",'cancelled','rejected') ORDER BY rowid LIMIT 100"
            ),
            (owner, after),
        )
        return [(int(row[0]), WorkItem.model_validate_json(row[1])) for row in rows]

    async def work_mutating(self, work: WorkItem) -> bool:
        rows = await self.db.fetchall(
            (
                "SELECT 1 FROM agent_tasks t JOIN harness_work_items p O"
                "N p.work_item_id=json_extract(t.payload_json,'$.work_it"
                "em_id') WHERE t.scope=? AND t.state IN ('queued','runni"
                "ng') AND (p.work_item_id=? OR json_extract(p.payload,'$"
                ".target_id')=?) LIMIT 1"
            ),
            (work.owner, work.work_item_id, work.work_item_id),
        )
        return bool(rows)

    async def capture_snapshot(
        self, work: WorkItem, facts: JsonObject, historical: bool = False
    ) -> EvidenceRef:
        from chatwaifu_runtime.knowledge.contracts import Mutation, RecordDraft
        from chatwaifu_runtime.persistence.sqlite_knowledge import SQLiteKnowledgeRepository

        identifier = (
            "provider:"
            + hashlib.sha256(
                json.dumps(
                    {key: facts.get(key) for key in ("account_id", "collection_id")}
                    | {"item": cast(JsonObject, facts.get("item", {})).get("id")},
                    sort_keys=True,
                ).encode()
            ).hexdigest()
        )
        async with self.db.transaction() as c:
            rows = await fetch_rows(
                c,
                "SELECT revision FROM knowledge_records WHERE owner=? AND record_id=?",
                (work.owner, identifier),
            )
            revision = int(rows[0][0]) if rows else 0
            draft = RecordDraft(
                record_id=identifier,
                kind="source",
                room_id="calendar:"
                + hashlib.sha256(
                    str(facts.get("account_id")).encode() + str(facts.get("collection_id")).encode()
                ).hexdigest(),
                title="外部事项回读",
                text=json.dumps(facts, ensure_ascii=False),
                occurred_at=now_ms(),
                sender_name="日历与待办回读",
                origin={
                    "source_type": "provider_change",
                    "_trusted_adapter": True,
                    "work_item_id": work.work_item_id,
                    "work_revision": work.revision,
                },
            )
            operation = uuid5(NAMESPACE_URL, f"{identifier}:v{revision + 1}")
            result = await SQLiteKnowledgeRepository(self.db).apply_mutation_in(
                c,
                work.owner,
                Mutation(operation_id=operation, expected_revision=revision, record=draft),
                trusted=True,
            )
            if result.get("status") != "accepted":
                raise KnowledgeConflict("provider_snapshot_changed")
            observation = Observation(
                observation_id=uuid5(NAMESPACE_URL, f"{identifier}:v{revision + 1}"),
                owner=work.owner,
                room_id=draft.room_id,
                evidence=EvidenceRef(kind="knowledge", reference=identifier, revision=revision + 1),
                ingestion_mode="historical" if historical else "live",
                occurred_at=draft.occurred_at,
                received_at=now_ms(),
                policy_epoch=0,
            )
            await c.execute(
                (
                    "INSERT OR IGNORE INTO harness_observations(observation_"
                    "id,owner,room_id,reference,revision,state,available_at,"
                    "payload) VALUES(?,?,?,?,?,'pending',?,?)"
                ),
                (
                    str(observation.observation_id),
                    work.owner,
                    draft.room_id,
                    identifier,
                    revision + 1,
                    now_ms(),
                    observation.model_dump_json(),
                ),
            )
        return observation.evidence

    async def pending_commands(self, owner: str) -> list[WorkItem]:
        rows = await self.db.fetchall(
            (
                "SELECT w.payload FROM harness_work_items w LEFT JOIN ag"
                "ent_tasks t ON t.task_id=json_extract(w.payload,'$.task"
                "_id') WHERE w.owner=? AND json_extract(w.payload,'$.roo"
                "m_id')='owner:instructions' AND json_extract(w.payload,"
                "'$.target_id') IS NOT NULL AND (t.task_id IS NULL OR js"
                "on_extract(t.payload_json,'$.wait_condition.reference')"
                "='owner-work-ready') AND json_extract(w.payload,'$.stat"
                "e')='waiting' LIMIT 100"
            ),
            (owner,),
        )
        return [WorkItem.model_validate_json(row[0]) for row in rows]

    async def dependent_tasks(self, owner: str) -> list[AgentTask]:
        rows = await self.db.fetchall(
            (
                "SELECT payload_json FROM agent_tasks WHERE scope=? AND "
                "state='waiting_event' AND json_extract(payload_json,'$."
                "wait_condition.kind')='task' LIMIT 32"
            ),
            (owner,),
        )
        return [AgentTask.model_validate_json(row[0]) for row in rows]

    async def last_scheduled(self, intent: UUID) -> tuple[datetime, UUID] | None:
        rows = await self.db.fetchall(
            (
                "SELECT scheduled_at,task_id FROM harness_intent_wakes W"
                "HERE intent_id=? ORDER BY scheduled_at DESC LIMIT 1"
            ),
            (str(intent),),
        )
        return (datetime.fromisoformat(rows[0][0]), UUID(rows[0][1])) if rows else None

    async def schedule_reserved(self, intent: UUID, when: datetime, task: UUID) -> None:
        await self.db.execute(
            "INSERT OR IGNORE INTO harness_intent_wakes VALUES(?,?,?)",
            (str(intent), when.astimezone(UTC).isoformat(), str(task)),
        )

    async def devices(self, owner: str) -> list[JsonObject]:
        rows = await self.db.fetchall(
            (
                "SELECT device_id,label,last_seen FROM knowledge_devices"
                " WHERE owner=? ORDER BY last_seen DESC LIMIT 100"
            ),
            (owner,),
        )
        return [dict(row) for row in rows]

    async def active_task(
        self, owner: str, intent: UUID, work: str, target: str | None
    ) -> AgentTask | None:
        rows = await self.db.fetchall(
            (
                "SELECT payload_json FROM agent_tasks WHERE scope=? AND "
                "state NOT IN ('succeeded','failed','cancelled') AND jso"
                "n_extract(payload_json,'$.authorization.intent_id')=? A"
                "ND json_extract(payload_json,'$.work_item_id') IN (?,?)"
                " LIMIT 1"
            ),
            (owner, str(intent), work, target),
        )
        return AgentTask.model_validate_json(rows[0][0]) if rows else None

    async def active_work(self, owner: str) -> list[WorkItem]:
        rows = await self.db.fetchall(
            (
                "SELECT w.payload FROM harness_work_items w JOIN agent_t"
                "asks t ON t.task_id=json_extract(w.payload,'$.task_id')"
                " WHERE w.owner=? AND json_extract(t.payload_json,'$.wor"
                "k_item_id')=w.work_item_id AND json_extract(w.payload,'"
                "$.state') NOT IN ('rejected','cancelled') AND (json_ext"
                "ract(w.payload,'$.reconciled_task_revision') IS NULL OR"
                " json_extract(w.payload,'$.reconciled_task_revision')<t"
                ".revision) ORDER BY w.rowid LIMIT 100"
            ),
            (owner,),
        )
        return [WorkItem.model_validate_json(row[0]) for row in rows]

    async def command(self, owner: str, operation: UUID, digest: str) -> str | None:
        rows = await self.db.fetchall(
            "SELECT request_hash,reference FROM harness_commands WHERE owner=? AND operation_id=?",
            (owner, str(operation)),
        )
        if not rows:
            return None
        if rows[0][0] != digest:
            raise ValueError("operation_id_payload_changed")
        return str(rows[0][1])

    async def finish_command(
        self, owner: str, operation: UUID, digest: str, reference: str
    ) -> None:
        await self.db.execute(
            "INSERT OR IGNORE INTO harness_commands VALUES(?,?,?,?)",
            (str(operation), owner, digest, reference),
        )

    async def suppressed(self, owner: str, text: str) -> bool:
        return bool(
            await self.db.fetchall(
                "SELECT 1 FROM harness_suppressions WHERE owner=? AND digest=?",
                (owner, suppression_digest(text)),
            )
        )

    async def suppress(self, owner: str, text: str, cause: str) -> None:
        async with self.db.transaction() as c:
            await suppress_in(c, owner, text, cause)

    async def intents(self, owner: str) -> list[StandingIntent]:
        rows = await self.db.fetchall(
            ("SELECT payload FROM harness_intents WHERE owner=? ORDER BY intent_id LIMIT 100"),
            (owner,),
        )
        return [StandingIntent.model_validate_json(r[0]) for r in rows]

    async def intent(self, identifier: UUID) -> StandingIntent | None:
        rows = await self.db.fetchall(
            "SELECT payload FROM harness_intents WHERE intent_id=?", (str(identifier),)
        )
        return StandingIntent.model_validate_json(rows[0][0]) if rows else None

    async def save_intent(self, item: StandingIntent, expected: int) -> bool:
        async with self.db.transaction() as c:
            if expected == 0:
                result = await c.execute(
                    "INSERT OR IGNORE INTO harness_intents VALUES(?,?,?,?)",
                    (str(item.intent_id), item.owner, item.revision, item.model_dump_json()),
                )
            else:
                result = await c.execute(
                    (
                        "UPDATE harness_intents SET revision=?,payload=? WHERE "
                        "intent_id=? AND revision=?"
                    ),
                    (item.revision, item.model_dump_json(), str(item.intent_id), expected),
                )
            return result.rowcount == 1

    async def processing(self, owner: str) -> list[dict[str, object]]:
        rows = await self.db.fetchall(
            "SELECT * FROM harness_processing WHERE owner=? ORDER BY room_id", (owner,)
        )
        return [dict(r) for r in rows]

    async def handoff(
        self,
        owner: str,
        device: UUID,
        rooms: list[str],
        expected: int,
        activate: bool,
        watermarks: dict[str, int],
        completed_sources: list[EvidenceRef],
    ) -> int:
        async with self.db.transaction() as c:
            for ref in completed_sources:
                if ref.kind != "knowledge":
                    raise ValueError("invalid_completed_source")
                await c.execute(
                    "INSERT OR IGNORE INTO harness_processed_sources VALUES(?,?,?)",
                    (owner, ref.reference, ref.revision),
                )
            next_epoch = expected
            for room in rooms:
                rows = await fetch_rows(
                    c,
                    (
                        "SELECT epoch,state,device_id,watermark FROM harness_processing "
                        "WHERE owner=? AND room_id=?"
                    ),
                    (owner, room),
                )
                epoch = int(rows[0][0]) if rows else 0
                if rows and rows[0][1] == "runtime" and epoch == expected:
                    next_epoch = epoch
                    continue
                if epoch != expected or (rows and str(rows[0][2]) != str(device)):
                    raise ValueError("processing_epoch_conflict")
                if activate and (not rows or rows[0][1] != "preparing"):
                    raise ValueError("processing_not_prepared")
                next_epoch = epoch + 1 if activate or not rows else epoch
                await c.execute(
                    (
                        "INSERT INTO harness_processing VALUES(?,?,?,?,?,?) ON "
                        "CONFLICT(owner,room_id) DO UPDATE SET "
                        "epoch=excluded.epoch,state=excluded.state,watermark=excluded.wate"
                        "rmark"
                    ),
                    (
                        owner,
                        room,
                        str(device),
                        next_epoch,
                        "runtime" if activate else "preparing",
                        int(rows[0][3]) if rows else watermarks.get(room, now_ms()),
                    ),
                )
                if activate:
                    inherited = await fetch_rows(
                        c,
                        (
                            "SELECT payload FROM harness_work_items WHERE owner=? AN"
                            "D json_extract(payload,'$.room_id')=? AND json_extract("
                            "payload,'$.primary_device_id') IS NULL AND json_extract"
                            "(payload,'$.remind_at') IS NOT NULL"
                        ),
                        (owner, room),
                    )
                    for row in inherited:
                        work = WorkItem.model_validate_json(row[0])
                        changed = work.model_copy(
                            update={"primary_device_id": device, "revision": work.revision + 1}
                        )
                        await c.execute(
                            (
                                "UPDATE harness_work_items SET revision=?,payload=? WHER"
                                "E work_item_id=?"
                            ),
                            (changed.revision, changed.model_dump_json(), changed.work_item_id),
                        )
                        await project_reminder_in(c, changed)
                if activate:
                    records = await fetch_rows(
                        c,
                        (
                            "SELECT payload FROM knowledge_records WHERE owner=? AND "
                            "room_id=? AND kind='source' AND deleted=0"
                        ),
                        (owner, room),
                    )
                    state = await fetch_rows(
                        c, "SELECT policy_epoch FROM knowledge_owners WHERE owner=?", (owner,)
                    )
                    for record in records:
                        parsed = Record.model_validate_json(record[0])
                        done = await fetch_rows(
                            c,
                            (
                                "SELECT 1 FROM harness_processed_sources WHERE owner=? AND "
                                "reference=? AND revision=?"
                            ),
                            (owner, parsed.record_id, parsed.revision),
                        )
                        if done:
                            pending = await fetch_rows(
                                c,
                                (
                                    "SELECT 1 FROM harness_work_items w,json_each(w.payload,"
                                    "'$.evidence') e WHERE w.owner=? AND json_extract(w.payl"
                                    "oad,'$.legacy_review')=1 AND json_extract(w.payload,'$."
                                    "state')='proposed' AND json_extract(e.value,'$.referenc"
                                    "e')=? LIMIT 1"
                                ),
                                (owner, parsed.record_id),
                            )
                            if not pending:
                                continue
                        await observe_in(
                            c,
                            owner,
                            Record.model_validate_json(record[0]),
                            "live",
                            int(state[0][0]),
                        )
            return next_epoch

    async def acknowledge_activity(
        self, owner: str, sequence: int, device: UUID, state: str
    ) -> None:
        async with self.db.transaction() as c:
            rows = await fetch_rows(
                c,
                (
                    "SELECT 1 FROM knowledge_devices d,harness_activity a WH"
                    "ERE d.owner=? AND d.device_id=? AND a.owner=d.owner AND"
                    " a.sequence=?"
                ),
                (owner, str(device), sequence),
            )
            if not rows:
                raise PermissionError("activity_device_not_visible")
            await c.execute(
                (
                    "INSERT INTO harness_activity_receipts VALUES(?,?,?,?) O"
                    "N CONFLICT(sequence,device_id,state) DO UPDATE SET upda"
                    "ted_at=excluded.updated_at"
                ),
                (sequence, str(device), state, instant().isoformat()),
            )

    async def progress(self, owner: str, after: int) -> list[dict[str, object]]:
        rows = await self.db.fetchall(
            (
                "SELECT sequence,reference,revision FROM harness_process"
                "ing_receipts WHERE owner=? AND sequence>? ORDER BY sequ"
                "ence LIMIT 100"
            ),
            (owner, after),
        )
        return [dict(row) for row in rows]

    async def cohort(self, item: Observation) -> list[Observation]:
        result = [item]
        async with self.db.transaction() as c:
            rows = await fetch_rows(
                c,
                (
                    "SELECT o.payload,r.payload FROM harness_observations o "
                    "JOIN knowledge_records r ON r.owner=o.owner AND r.recor"
                    "d_id=o.reference AND r.revision=o.revision WHERE o.owne"
                    "r=? AND o.room_id=? AND o.state='pending' AND o.availab"
                    "le_at<=? AND json_extract(o.payload,'$.ingestion_mode')"
                    "=? ORDER BY json_extract(o.payload,'$.occurred_at'),o.o"
                    "bservation_id LIMIT 29"
                ),
                (item.owner, item.room_id, now_ms(), item.ingestion_mode),
            )
            first = await fetch_rows(
                c,
                "SELECT payload FROM knowledge_records WHERE owner=? AND record_id=?",
                (item.owner, item.evidence.reference),
            )
            size = (
                len(Record.model_validate_json(first[0][0]).text.encode("utf-16-le")) // 2
                if first
                else 0
            )
            if first and len(Record.model_validate_json(first[0][0]).text) > 3000:
                return result
            for row in rows:
                source = Record.model_validate_json(row[1])
                cost = len(source.text.encode("utf-16-le")) // 2
                if len(source.text) > 3000:
                    continue
                if size + cost > 12000:
                    break
                other = Observation.model_validate_json(row[0]).model_copy(
                    update={"state": "running"}
                )
                await c.execute(
                    (
                        "UPDATE harness_observations SET state='running',lease_u"
                        "ntil=?,payload=? WHERE observation_id=? AND state='pend"
                        "ing'"
                    ),
                    (now_ms() + 360000, other.model_dump_json(), str(other.observation_id)),
                )
                result.append(other)
                size += cost
        return result

    async def next_delay(self) -> float:
        rows = await self.db.fetchall(
            "SELECT min(o.available_at) FROM harness_observations o "
            "WHERE o.state='pending' AND NOT EXISTS(SELECT 1 FROM ha"
            "rness_observations r WHERE r.owner=o.owner AND r.room_i"
            "d=o.room_id AND r.state='running') AND (json_extract(o."
            "payload,'$.ingestion_mode')!='historical' OR NOT EXISTS"
            "(SELECT 1 FROM harness_observations h WHERE h.state='ru"
            "nning' AND json_extract(h.payload,'$.ingestion_mode')='"
            "historical'))"
        )
        value = rows[0][0] if rows else None
        return max(0.05, min(300, (int(value) - now_ms()) / 1000)) if value is not None else 300

    async def claim(self) -> Observation | None:
        now = now_ms()
        async with self.db.transaction() as c:
            await c.execute(
                (
                    "UPDATE harness_observations SET state='pending' WHERE "
                    "state='running' AND lease_until<=?"
                ),
                (now,),
            )
            rows = await fetch_rows(
                c,
                (
                    "SELECT observation_id,payload FROM harness_observations o WHERE "
                    "state='pending' AND availabl"
                    "e_at<=? AND (json_extract(pa"
                    "yload,'$.ingestion_mode')!='"
                    "historical' OR NOT EXISTS(SE"
                    "LECT 1 FROM harness_observat"
                    "ions h WHERE h.state='runnin"
                    "g' AND json_extract(h.payloa"
                    "d,'$.ingestion_mode')='histo"
                    "rical')) AND NOT EXISTS(SELE"
                    "CT 1 FROM "
                    "harness_observations r WHERE r.room_id=o.room_id AND "
                    "r.owner=o.owner AND r.state='running') ORDER BY CASE "
                    "json_extract(payload,'$.ingestion_mode') WHEN 'historical' THEN "
                    "1 ELSE 0 END,available_at,observation_id LIMIT 1"
                ),
                (now,),
            )
            if not rows:
                return None
            item = Observation.model_validate_json(rows[0][1]).model_copy(
                update={"state": "running"}
            )
            await c.execute(
                (
                    "UPDATE harness_observations SET "
                    "state='running',lease_until=?,payload=? WHERE observation_id=?"
                ),
                (now + 360000, item.model_dump_json(), rows[0][0]),
            )
            return item

    async def settle(self, item: Observation, failed: bool = False) -> None:
        attempts = item.attempts + int(failed)
        state = "failed" if attempts >= 5 else "pending" if failed else "done"
        updated = item.model_copy(update={"state": state, "attempts": attempts})
        async with self.db.transaction() as c:
            if not failed:
                await c.execute(
                    (
                        "INSERT OR IGNORE INTO harness_processing_receipts(owner"
                        ",reference,revision) VALUES(?,?,?)"
                    ),
                    (item.owner, item.evidence.reference, item.evidence.revision),
                )
            await c.execute(
                (
                    "UPDATE harness_observations SET "
                    "state=?,available_at=?,lease_until=0,payload=? WHERE "
                    "observation_id=? AND state='running'"
                ),
                (state, now_ms() + 60000, updated.model_dump_json(), str(item.observation_id)),
            )

    async def work_items(
        self, owner: str, limit: int = 100, offset: int = 0, room: str | None = None
    ) -> list[WorkItem]:
        rows = await self.db.fetchall(
            (
                "SELECT payload FROM harness_work_items WHERE owner=? AN"
                "D (? IS NULL OR json_extract(payload,'$.room_id')=? OR "
                "EXISTS(SELECT 1 FROM json_each(payload,'$.source_room_i"
                "ds') r WHERE r.value=?)) ORDER BY rowid DESC LIMIT ? OF"
                "FSET ?"
            ),
            (owner, room, room, room, limit, offset),
        )
        return [WorkItem.model_validate_json(r[0]) for r in rows]

    async def work_item(self, identifier: str) -> WorkItem | None:
        rows = await self.db.fetchall(
            "SELECT payload FROM harness_work_items WHERE work_item_id=?", (identifier,)
        )
        return WorkItem.model_validate_json(rows[0][0]) if rows else None

    async def save_work(self, item: WorkItem, expected: int) -> bool:
        async with self.db.transaction() as c:
            if expected == 0:
                result = await c.execute(
                    "INSERT OR IGNORE INTO harness_work_items VALUES(?,?,?,?)",
                    (item.work_item_id, item.owner, item.revision, item.model_dump_json()),
                )
            else:
                result = await c.execute(
                    (
                        "UPDATE harness_work_items SET revision=?,payload=? WHERE "
                        "work_item_id=? AND revision=?"
                    ),
                    (item.revision, item.model_dump_json(), item.work_item_id, expected),
                )
            return result.rowcount == 1

    async def reserve_dispatch(self, intent: UUID, work: WorkItem) -> UUID:
        key = uuid5(intent, work.work_item_id)
        async with self.db.transaction() as c:
            await c.execute(
                "INSERT OR IGNORE INTO harness_dispatch VALUES(?,?,?,?)",
                (str(intent), work.work_item_id, work.revision, str(key)),
            )
        return key

    async def activity(
        self, owner: str, after: int = 0, before: int | None = None
    ) -> list[ActivityEntry]:
        if after > 0:
            rows = await self.db.fetchall(
                (
                    "SELECT * FROM harness_activity WHERE owner=? AND sequen"
                    "ce>? ORDER BY sequence LIMIT 100"
                ),
                (owner, after),
            )
        else:
            rows = list(
                reversed(
                    await self.db.fetchall(
                        (
                            "SELECT * FROM harness_activity WHERE owner=? AND sequen"
                            "ce<? ORDER BY sequence DESC LIMIT 100"
                        ),
                        (owner, before or 9223372036854775807),
                    )
                )
            )
        entries: list[ActivityEntry] = []
        for row in rows:
            receipts = await self.db.fetchall(
                "SELECT device_id,state,updated_at FROM harness_activity_receipts WHERE sequence=?",
                (row["sequence"],),
            )
            delivery: dict[str, str] = {}
            ranks = {"blocked": 0, "unknown": 1, "accepted": 2, "presented": 3, "opened": 4}
            for receipt in receipts:
                if ranks.get(receipt[1], 0) >= ranks.get(delivery.get(receipt[0], "blocked"), 0):
                    delivery[receipt[0]] = receipt[1]
            entries.append(
                ActivityEntry(
                    sequence=row["sequence"],
                    kind=row["kind"],
                    reference=row["reference"],
                    state=row["state"],
                    summary=row["summary"],
                    created_at=row["created_at"],
                    evidence=json.loads(row["evidence"]),
                    delivery=delivery,
                    delivery_history=[
                        {"device_id": receipt[0], "state": receipt[1], "observed_at": receipt[2]}
                        for receipt in receipts
                    ],
                    details=json.loads(row["details"]),
                )
            )
        return entries

    async def record(
        self,
        owner: str,
        kind: str,
        reference: str,
        state: str,
        summary: str,
        evidence: list[EvidenceRef] | None = None,
        *,
        dedup_key: str | None = None,
        details: JsonObject | None = None,
    ) -> None:
        async with self.db.transaction() as c:
            cursor = await c.execute(
                (
                    "INSERT OR IGNORE INTO harness_activity(owner,kind,refer"
                    "ence,state,summary,created_at,evidence,dedup_key,detail"
                    "s) VALUES(?,?,?,?,?,?,?,?,?)"
                ),
                (
                    owner,
                    kind,
                    reference,
                    state,
                    summary[:1200],
                    instant().isoformat(),
                    json.dumps([e.model_dump(mode="json") for e in evidence or []]),
                    dedup_key,
                    json.dumps(details or {}, ensure_ascii=False),
                ),
            )
            changed = cursor.rowcount == 1
        if changed and self.on_record:
            self.on_record()
