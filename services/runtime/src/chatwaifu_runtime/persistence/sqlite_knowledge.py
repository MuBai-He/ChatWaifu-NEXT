"Canonical knowledge revisions, CAS receipts and indexing leases in SQLite."

from __future__ import annotations

import hashlib
import json
import time
from collections.abc import Sequence
from typing import cast
from uuid import NAMESPACE_URL, UUID, uuid4, uuid5

import aiosqlite
from chatwaifu_protocol.base import JsonObject, JsonValue
from chatwaifu_protocol.memory import MemoryRecord

from chatwaifu_runtime.knowledge.contracts import (
    Citation,
    Mutation,
    PolicyMutation,
    PushRequest,
    Record,
    RecordDraft,
)
from chatwaifu_runtime.knowledge.errors import KnowledgeAccessError, KnowledgeConflict
from chatwaifu_runtime.persistence.database import Database


def now_ms() -> int:
    return int(time.time() * 1000)


def encode(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def document(value: str) -> JsonObject:
    return cast(JsonObject, json.loads(value))


async def one(
    c: aiosqlite.Connection, sql: str, args: Sequence[object] = ()
) -> aiosqlite.Row | None:
    rows = list(await c.execute_fetchall(sql, args))
    return rows[0] if rows else None


class SQLiteKnowledgeRepository:
    def __init__(self, database: Database) -> None:
        self.db = database

    async def private_owner(self, session_id: str) -> str:
        row = await self.db.fetchone(
            (
                "SELECT user_scope,scene_kind,scene_id,audience_json FROM "
                "sessions WHERE session_id=?"
            ),
            (session_id,),
        )
        if (
            row is None
            or row[0] != "local"
            or row[1] != "private"
            or row[2] is not None
            or json.loads(str(row[3])) != ["local"]
        ):
            raise KnowledgeAccessError("private_owner_required")
        return "local"

    async def ensure_owner_in(self, c: aiosqlite.Connection, owner: str) -> aiosqlite.Row:
        await c.execute("INSERT OR IGNORE INTO knowledge_owners(owner) VALUES(?)", (owner,))
        row = await one(c, "SELECT * FROM knowledge_owners WHERE owner=?", (owner,))
        assert row is not None
        return row

    async def epoch(self, owner: str) -> int:
        row = await self.db.fetchone(
            "SELECT policy_epoch FROM knowledge_owners WHERE owner=?", (owner,)
        )
        return int(row[0]) if row else 0

    async def room_excluded_in(self, c: aiosqlite.Connection, owner: str, room: str) -> bool:
        row = await one(
            c, "SELECT excluded FROM knowledge_policies WHERE owner=? AND room_id=?", (owner, room)
        )
        return bool(row and row[0])

    async def _change(
        self, c: aiosqlite.Connection, owner: str, kind: str, key: str, payload: object
    ) -> None:
        await c.execute(
            (
                "INSERT INTO "
                "knowledge_changes(owner,kind,object_id,payload,created_at) "
                "VALUES(?,?,?,?,?)"
            ),
            (owner, kind, key, encode(payload), now_ms()),
        )

    async def _receipt(
        self, c: aiosqlite.Connection, owner: str, operation: str, digest: str
    ) -> JsonObject | None:
        row = await one(
            c,
            (
                "SELECT request_hash,receipt FROM knowledge_receipts WHERE "
                "owner=? AND operation_id=?"
            ),
            (owner, operation),
        )
        if row is None:
            return None
        if row[0] != digest:
            raise KnowledgeConflict("operation_id_reused")
        return document(str(row[1]))

    async def _save_receipt(
        self, c: aiosqlite.Connection, owner: str, operation: str, digest: str, receipt: JsonObject
    ) -> None:
        await c.execute(
            "INSERT INTO knowledge_receipts VALUES(?,?,?,?)",
            (owner, operation, digest, encode(receipt)),
        )

    async def push(self, owner: str, body: PushRequest) -> JsonObject:
        receipts: list[JsonObject] = []
        async with self.db.transaction() as c:
            state = await self.ensure_owner_in(c, owner)
            device = await one(
                c, "SELECT owner FROM knowledge_devices WHERE device_id=?", (str(body.device_id),)
            )
            if device and device[0] != owner:
                raise KnowledgeAccessError("device_owner_mismatch")
            await c.execute(
                (
                    "INSERT INTO knowledge_devices(device_id,owner,last_seen) "
                    "VALUES(?,?,?) ON CONFLICT(device_id) DO UPDATE SET "
                    "last_seen=excluded.last_seen"
                ),
                (str(body.device_id), owner, now_ms()),
            )
            # Clients must reconcile changed exclusions before transmitting source content.
            if int(state["policy_epoch"]) != body.policy_epoch:
                for op in (*body.policies, *body.mutations):
                    digest = hashlib.sha256(encode(op.model_dump(mode="json")).encode()).hexdigest()
                    if await self._receipt(c, owner, str(op.operation_id), digest) is None:
                        raise KnowledgeConflict("policy_changed_pull_required")
            for policy in body.policies:
                receipts.append(await self._policy(c, owner, policy))
            for mutation in body.mutations:
                if (
                    mutation.record.record_id.startswith("formal:")
                    or mutation.record.origin.get("source_type") == "formal_memory"
                ):
                    raise KnowledgeAccessError("formal_memory_origin_reserved")
                existing = await one(
                    c,
                    "SELECT payload FROM knowledge_records WHERE owner=? AND record_id=?",
                    (owner, mutation.record.record_id),
                )
                if (
                    existing
                    and Record.model_validate_json(str(existing[0])).origin.get("source_type")
                    == "formal_memory"
                ):
                    raise KnowledgeConflict("formal_memory_use_memory_controls")
                receipts.append(await self._mutate(c, owner, mutation))
            state = await self.ensure_owner_in(c, owner)
            cursor = await one(
                c, "SELECT coalesce(max(sequence),0) FROM knowledge_changes WHERE owner=?", (owner,)
            )
            return {
                "schema_version": "1.0",
                "receipts": cast(list[JsonValue], receipts),
                "policy_epoch": int(state["policy_epoch"]),
                "server_cursor": int(cursor[0]) if cursor else 0,
            }

    async def _policy(
        self, c: aiosqlite.Connection, owner: str, policy: PolicyMutation
    ) -> JsonObject:
        operation = str(policy.operation_id)
        digest = hashlib.sha256(encode(policy.model_dump(mode="json")).encode()).hexdigest()
        cached = await self._receipt(c, owner, operation, digest)
        if cached is not None:
            return cached
        old = await one(
            c,
            "SELECT * FROM knowledge_policies WHERE owner=? AND room_id=?",
            (owner, policy.room_id),
        )
        revision = int(old["revision"]) if old else 0
        if revision != policy.expected_revision:
            result: JsonObject = {
                "operation_id": operation,
                "status": "conflict",
                "room_id": policy.room_id,
                "revision": revision,
                "excluded": bool(old and old["excluded"]),
            }
        else:
            revision += 1
            await c.execute(
                (
                    "INSERT INTO knowledge_policies VALUES(?,?,?,?,?) ON "
                    "CONFLICT(owner,room_id) DO UPDATE SET "
                    "revision=excluded.revision,excluded=excluded.excluded,label=excluded.label"
                ),
                (owner, policy.room_id, revision, int(policy.excluded), policy.label),
            )
            await c.execute(
                (
                    "UPDATE knowledge_owners SET "
                    "policy_epoch=policy_epoch+1,graph_revision=graph_revision+1 "
                    "WHERE owner=?"
                ),
                (owner,),
            )
            result = {
                "operation_id": operation,
                "status": "accepted",
                "room_id": policy.room_id,
                "revision": revision,
                "excluded": policy.excluded,
                "label": policy.label,
            }
            await self._change(c, owner, "policy", policy.room_id, result)
            rows = await c.execute_fetchall(
                (
                    "SELECT record_id,revision,json_extract(payload,'$.occurred_at') "
                    "FROM knowledge_records WHERE owner=? AND deleted=0 AND "
                    "(room_id=? OR EXISTS(SELECT 1 FROM "
                    "json_each(payload,'$.source_refs') j JOIN knowledge_records "
                    "parent ON parent.owner=knowledge_records.owner AND "
                    "parent.record_id=json_extract(j.value,'$.record_id') WHERE "
                    "parent.room_id=?))"
                ),
                (owner, policy.room_id, policy.room_id),
            )
            for row in rows:
                await self._job(
                    c,
                    owner,
                    str(row[0]),
                    int(row[1]),
                    "remove" if policy.excluded else "index",
                    not policy.excluded and int(row[2]) < now_ms() - 30 * 86400000,
                )
        await self._save_receipt(c, owner, operation, digest, result)
        return result

    async def _job(
        self,
        c: aiosqlite.Connection,
        owner: str,
        record_id: str,
        revision: int,
        action: str,
        historical: bool,
    ) -> None:
        state = await self.ensure_owner_in(c, owner)
        await c.execute(
            (
                "UPDATE knowledge_jobs SET state='superseded' WHERE owner=? AND "
                "record_id=? AND state IN ('pending','leased','failed') AND "
                "action='index'"
            ),
            (owner, record_id),
        )
        await c.execute(
            (
                "INSERT INTO "
                "knowledge_jobs(job_id,owner,record_id,revision,policy_epoch,action,historical,created_at)"
                " VALUES(?,?,?,?,?,?,?,?)"
            ),
            (
                str(uuid4()),
                owner,
                record_id,
                revision,
                int(state["policy_epoch"]),
                action,
                int(historical),
                now_ms(),
            ),
        )

    async def _mutate(self, c: aiosqlite.Connection, owner: str, mutation: Mutation) -> JsonObject:
        operation = str(mutation.operation_id)
        digest = hashlib.sha256(encode(mutation.model_dump(mode="json")).encode()).hexdigest()
        cached = await self._receipt(c, owner, operation, digest)
        if cached is not None:
            return cached
        draft = mutation.record
        row = await one(
            c,
            "SELECT * FROM knowledge_records WHERE owner=? AND record_id=?",
            (owner, draft.record_id),
        )
        current = Record.model_validate_json(str(row["payload"])) if row else None
        revision = current.revision if current else 0
        result: JsonObject = {"operation_id": operation, "record_id": draft.record_id}
        if revision != mutation.expected_revision or (
            current and current.deleted and mutation.action != "forget"
        ):
            conflict_id = str(uuid4())
            await c.execute(
                (
                    "INSERT INTO "
                    "knowledge_conflicts(conflict_id,owner,record_id,proposed,current_revision,created_at)"
                    " VALUES(?,?,?,?,?,?)"
                ),
                (
                    conflict_id,
                    owner,
                    draft.record_id,
                    mutation.model_dump_json(),
                    revision,
                    now_ms(),
                ),
            )
            result.update(
                {
                    "status": "conflict",
                    "conflict_id": conflict_id,
                    "revision": revision,
                    "current": current.model_dump(mode="json") if current else None,
                }
            )
        elif current and (current.kind != draft.kind or current.room_id != draft.room_id):
            result.update({"status": "blocked", "reason": "identity_cannot_change"})
        elif (
            mutation.action == "upsert"
            and await self.room_excluded_in(c, owner, draft.room_id)
            and (draft.kind == "source" or not draft.confirmed)
        ):
            result.update({"status": "blocked", "reason": "conversation_excluded"})
        else:
            for citation in draft.source_refs if mutation.action == "upsert" else []:
                source = await one(
                    c,
                    (
                        "SELECT revision,deleted,kind FROM knowledge_records WHERE "
                        "owner=? AND record_id=?"
                    ),
                    (owner, citation.record_id),
                )
                if (
                    source is None
                    or source[1]
                    or source[2] != "source"
                    or int(source[0]) != citation.revision
                ):
                    raise KnowledgeConflict("source_revision_changed")
            record = Record(
                **draft.model_dump(),
                revision=revision + 1,
                deleted=mutation.action == "forget",
                updated_at=now_ms(),
            )
            if record.deleted:
                record = record.model_copy(
                    update={
                        "text": "",
                        "title": "已遗忘",
                        "origin": {},
                        "source_refs": [],
                        "sender_id": None,
                        "sender_name": "",
                    }
                )
                await self._scrub_forgotten(c, owner, record)
            payload = record.model_dump_json()
            await c.execute(
                (
                    "INSERT INTO knowledge_records VALUES(?,?,?,?,?,?,?,?,?) ON "
                    "CONFLICT(owner,record_id) DO UPDATE SET "
                    "revision=excluded.revision,deleted=excluded.deleted,confirmed=excluded.confirmed,payload=excluded.payload,updated_at=excluded.updated_at"
                ),
                (
                    owner,
                    record.record_id,
                    record.kind,
                    record.room_id,
                    record.revision,
                    int(record.deleted),
                    int(record.confirmed),
                    payload,
                    record.updated_at,
                ),
            )
            await c.execute(
                "INSERT INTO knowledge_versions VALUES(?,?,?,?,?)",
                (owner, record.record_id, record.revision, payload, record.updated_at),
            )
            await c.execute(
                "DELETE FROM knowledge_fts WHERE owner=? AND record_id=?", (owner, record.record_id)
            )
            if not record.deleted:
                await c.execute(
                    "INSERT INTO knowledge_fts VALUES(?,?,?)",
                    (owner, record.record_id, self.lexical(record.title + " " + record.text)),
                )
            if (
                record.confirmed
                and record.kind in ("memory", "todo", "event")
                and not record.deleted
            ):
                node_id = (
                    "object:"
                    + hashlib.sha256((owner + "\0" + record.record_id).encode()).hexdigest()
                )
                await c.execute(
                    (
                        "INSERT INTO knowledge_nodes(owner,node_id,kind,label,room_id) "
                        "VALUES(?,?,?,?,?) ON CONFLICT(owner,node_id) DO UPDATE SET "
                        "label=excluded.label"
                    ),
                    (owner, node_id, record.kind, record.title, record.room_id),
                )
                await c.execute(
                    "INSERT OR IGNORE INTO knowledge_node_sources VALUES(?,?,?,?)",
                    (owner, node_id, record.record_id, record.revision),
                )
            await self._change(c, owner, "record", record.record_id, record.model_dump(mode="json"))
            await self._job(
                c,
                owner,
                record.record_id,
                record.revision,
                "remove" if record.deleted else "index",
                record.occurred_at < now_ms() - 30 * 86400000,
            )
            if record.deleted:
                await c.execute(
                    "UPDATE knowledge_owners SET policy_epoch=policy_epoch+1 WHERE owner=?",
                    (owner,),
                )
                dependents = await c.execute_fetchall(
                    (
                        "SELECT record_id,revision FROM knowledge_records WHERE owner=? "
                        "AND deleted=0 AND EXISTS(SELECT 1 FROM "
                        "json_each(payload,'$.source_refs') j WHERE "
                        "json_extract(j.value,'$.record_id')=?)"
                    ),
                    (owner, record.record_id),
                )
                for dependent in dependents:
                    await self._job(c, owner, str(dependent[0]), int(dependent[1]), "remove", False)
            await c.execute(
                ("UPDATE knowledge_owners SET graph_revision=graph_revision+1 WHERE owner=?"),
                (owner,),
            )
            result.update(
                {
                    "status": "accepted",
                    "revision": record.revision,
                    "record": record.model_dump(mode="json"),
                }
            )
        await self._save_receipt(c, owner, operation, digest, result)
        return result

    async def _scrub_forgotten(self, c: aiosqlite.Connection, owner: str, record: Record) -> None:
        versions = await c.execute_fetchall(
            ("SELECT revision,payload FROM knowledge_versions WHERE owner=? AND record_id=?"),
            (owner, record.record_id),
        )
        for version in versions:
            old = Record.model_validate_json(str(version[1]))
            tombstone = record.model_copy(
                update={"revision": int(version[0]), "updated_at": old.updated_at}
            )
            await c.execute(
                (
                    "UPDATE knowledge_versions SET payload=? WHERE owner=? AND "
                    "record_id=? AND revision=?"
                ),
                (tombstone.model_dump_json(), owner, record.record_id, int(version[0])),
            )
        await c.execute(
            (
                "UPDATE knowledge_changes SET payload=? WHERE owner=? AND "
                "kind='record' AND object_id=?"
            ),
            (record.model_dump_json(), owner, record.record_id),
        )
        await c.execute(
            (
                "UPDATE knowledge_changes SET "
                "payload=json_set(payload,'$.record',json(?)) WHERE owner=? AND "
                "kind='conflict_resolved' AND "
                "json_extract(payload,'$.record.record_id')=?"
            ),
            (record.model_dump_json(), owner, record.record_id),
        )
        orphaned = await c.execute_fetchall(
            (
                "SELECT DISTINCT n.node_id FROM knowledge_node_sources n WHERE "
                "n.owner=? AND n.record_id=? AND NOT EXISTS(SELECT 1 FROM "
                "knowledge_node_sources other JOIN knowledge_records r ON "
                "r.owner=other.owner AND r.record_id=other.record_id WHERE "
                "other.owner=n.owner AND other.node_id=n.node_id AND "
                "other.record_id<>? AND r.deleted=0)"
            ),
            (owner, record.record_id, record.record_id),
        )
        for orphan in orphaned:
            await c.execute(
                "DELETE FROM knowledge_reviews WHERE owner=? AND object_id=?", (owner, orphan[0])
            )
            await c.execute(
                "DELETE FROM knowledge_nodes WHERE owner=? AND node_id=?", (owner, orphan[0])
            )
        await c.execute(
            "DELETE FROM knowledge_node_sources WHERE owner=? AND record_id=?",
            (owner, record.record_id),
        )
        receipts = await c.execute_fetchall(
            (
                "SELECT operation_id,receipt FROM knowledge_receipts WHERE "
                "owner=? AND json_extract(receipt,'$.record_id')=?"
            ),
            (owner, record.record_id),
        )
        for row in receipts:
            receipt = document(str(row[1]))
            for key in ("record", "current"):
                if key in receipt:
                    receipt[key] = record.model_dump(mode="json")
            await c.execute(
                ("UPDATE knowledge_receipts SET receipt=? WHERE owner=? AND operation_id=?"),
                (encode(receipt), owner, row[0]),
            )
        await c.execute(
            "DELETE FROM knowledge_conflicts WHERE owner=? AND record_id=?",
            (owner, record.record_id),
        )
        await c.execute(
            (
                "UPDATE knowledge_relations SET "
                "status='forgotten',description='',revision=revision+1 WHERE "
                "owner=? AND EXISTS (SELECT 1 FROM json_each(source_refs) j WHERE"
                " json_extract(j.value,'$.record_id')=?)"
            ),
            (owner, record.record_id),
        )
        await c.execute(
            (
                "DELETE FROM knowledge_reviews WHERE owner=? AND object_id IN "
                "(SELECT relation_id FROM knowledge_relations WHERE owner=? AND "
                "status='forgotten')"
            ),
            (owner, owner),
        )

    @staticmethod
    def lexical(text: str) -> str:
        import re

        words = re.findall(r"[A-Za-z0-9_]+|[\u3400-\u9fff]+", text.lower())
        return " ".join(
            token
            for word in words
            for token in (
                [word] + [word[i : i + 2] for i in range(len(word) - 1)]
                if re.search(r"[\u3400-\u9fff]", word)
                else [word]
            )
        )

    async def pull(self, owner: str, device: str, after: int, limit: int = 100) -> JsonObject:
        async with self.db.transaction() as c:
            state = await self.ensure_owner_in(c, owner)
            registered = await one(
                c, "SELECT owner FROM knowledge_devices WHERE device_id=?", (device,)
            )
            if registered and registered[0] != owner:
                raise KnowledgeAccessError("device_owner_mismatch")
            rows = list(
                await c.execute_fetchall(
                    (
                        "SELECT * FROM knowledge_changes WHERE owner=? AND sequence>? "
                        "ORDER BY sequence LIMIT ?"
                    ),
                    (owner, after, limit + 1),
                )
            )
            changes: list[JsonObject] = []
            for row in rows[:limit]:
                payload = document(str(row["payload"]))
                changes.append(
                    {
                        "sequence": int(row["sequence"]),
                        "kind": str(row["kind"]),
                        "id": str(row["object_id"]),
                        "value": payload,
                    }
                )
            policies = await c.execute_fetchall(
                ("SELECT room_id,revision,excluded,label FROM knowledge_policies WHERE owner=?"),
                (owner,),
            )
            return {
                "schema_version": "1.0",
                "changes": cast(list[JsonValue], changes),
                "cursor": int(rows[min(len(rows), limit) - 1]["sequence"]) if rows else after,
                "more": len(rows) > limit,
                "policy_epoch": int(state["policy_epoch"]),
                "policies": [
                    {
                        "room_id": str(p[0]),
                        "revision": int(p[1]),
                        "excluded": bool(p[2]),
                        "label": str(p[3]),
                    }
                    for p in policies
                ],
            }

    async def acknowledge(
        self, owner: str, device: str, cursor: int, applied: list[Citation] | None = None
    ) -> None:
        async with self.db.transaction() as c:
            registered = await one(
                c, "SELECT owner FROM knowledge_devices WHERE device_id=?", (device,)
            )
            if registered and registered[0] != owner:
                raise KnowledgeAccessError("device_owner_mismatch")
            highest = await one(
                c, "SELECT coalesce(max(sequence),0) FROM knowledge_changes WHERE owner=?", (owner,)
            )
            if highest is None or cursor > int(highest[0]):
                raise KnowledgeConflict("invalid_ack_cursor")
            await c.execute(
                (
                    "INSERT INTO knowledge_devices VALUES(?,?,?,?) ON "
                    "CONFLICT(device_id) DO UPDATE SET "
                    "acknowledged=max(acknowledged,excluded.acknowledged),last_seen=excluded.last_seen"
                    " WHERE knowledge_devices.owner=excluded.owner"
                ),
                (device, owner, cursor, now_ms()),
            )

            for ref in applied or []:
                item = await one(
                    c,
                    "SELECT revision FROM knowledge_records WHERE owner=? AND record_id=?",
                    (owner, ref.record_id),
                )
                if item is None or ref.revision > int(item[0]):
                    raise KnowledgeConflict("invalid_applied_revision")
                await c.execute(
                    (
                        "INSERT INTO knowledge_record_acks VALUES(?,?,?,?,?) ON "
                        "CONFLICT(owner,device_id,record_id) DO UPDATE SET "
                        "revision=max(revision,excluded.revision),applied_at=excluded.applied_at"
                    ),
                    (owner, device, ref.record_id, ref.revision, now_ms()),
                )

    async def record_ack(self, owner: str, record_id: str, revision: int) -> JsonObject:
        row = await self.db.fetchone(
            (
                "SELECT count(*),max(applied_at) FROM knowledge_record_acks WHERE"
                " owner=? AND record_id=? AND revision>=?"
            ),
            (owner, record_id, revision),
        )
        return {
            "applied_devices": int(row[0]) if row else 0,
            "last_applied_at": int(row[1]) if row and row[1] else None,
        }

    async def get(self, owner: str, record_id: str, revision: int | None = None) -> Record | None:
        if revision is None:
            row = await self.db.fetchone(
                "SELECT payload FROM knowledge_records WHERE owner=? AND record_id=?",
                (owner, record_id),
            )
        else:
            row = await self.db.fetchone(
                (
                    "SELECT payload FROM knowledge_versions WHERE owner=? AND "
                    "record_id=? AND revision=?"
                ),
                (owner, record_id, revision),
            )
        return Record.model_validate_json(str(row[0])) if row else None

    async def eligible(self, owner: str, citation: Citation, *, historical: bool = False) -> bool:
        async with self.db.transaction() as c:
            return await self.eligible_in(c, owner, citation, historical=historical)

    async def eligible_in(
        self, c: aiosqlite.Connection, owner: str, citation: Citation, *, historical: bool = False
    ) -> bool:
        row = await one(
            c,
            (
                "SELECT r.revision,r.deleted,coalesce(p.excluded,0),r.payload "
                "FROM knowledge_records r LEFT JOIN knowledge_policies p ON "
                "p.owner=r.owner AND p.room_id=r.room_id WHERE r.owner=? AND "
                "r.record_id=?"
            ),
            (owner, citation.record_id),
        )
        if not row or row[1] or row[2] or (not historical and int(row[0]) != citation.revision):
            return False
        version = await one(
            c,
            ("SELECT payload FROM knowledge_versions WHERE owner=? AND record_id=? AND revision=?"),
            (owner, citation.record_id, citation.revision),
        )
        if not version:
            return False
        item = Record.model_validate_json(str(version[0]))
        for ref in item.source_refs:
            source = await one(
                c,
                (
                    "SELECT r.revision,r.deleted,r.kind,coalesce(p.excluded,0) FROM "
                    "knowledge_records r LEFT JOIN knowledge_policies p ON "
                    "p.owner=r.owner AND p.room_id=r.room_id WHERE r.owner=? AND "
                    "r.record_id=?"
                ),
                (owner, ref.record_id),
            )
            if (
                not source
                or source[1]
                or source[3]
                or source[2] != "source"
                or (not historical and int(source[0]) != ref.revision)
            ):
                return False
        return not item.deleted

    async def status(self, owner: str) -> JsonObject:
        rows = await self.db.fetchall(
            "SELECT state,count(*) FROM knowledge_jobs WHERE owner=? GROUP BY state", (owner,)
        )
        count = await self.db.fetchone(
            "SELECT count(*) FROM knowledge_records WHERE owner=? AND deleted=0", (owner,)
        )
        devices = await self.db.fetchall(
            ("SELECT device_id,acknowledged,last_seen FROM knowledge_devices WHERE owner=?"),
            (owner,),
        )
        indexed = await self.db.fetchone(
            (
                "SELECT count(DISTINCT r.record_id) FROM knowledge_records r JOIN"
                " knowledge_jobs j ON j.owner=r.owner AND j.record_id=r.record_id"
                " AND j.revision=r.revision LEFT JOIN knowledge_policies p ON "
                "p.owner=r.owner AND p.room_id=r.room_id WHERE r.owner=? AND "
                "r.deleted=0 AND coalesce(p.excluded,0)=0 AND j.action='index' "
                "AND j.state='done' AND j.job_id=(SELECT recent.job_id FROM "
                "knowledge_jobs recent WHERE recent.owner=r.owner AND "
                "recent.record_id=r.record_id ORDER BY recent.created_at "
                "DESC,recent.rowid DESC LIMIT 1)"
            ),
            (owner,),
        )
        return {
            "schema_version": "1.0",
            "indexed_records": int(indexed[0]) if indexed else 0,
            "records": int(count[0]) if count else 0,
            "policy_epoch": await self.epoch(owner),
            "index": {str(r[0]): int(r[1]) for r in rows},
            "devices": [
                {"device_id": str(r[0]), "acknowledged": int(r[1]), "last_seen": int(r[2])}
                for r in devices
            ],
        }

    async def conflicts(self, owner: str) -> list[JsonObject]:
        rows = await self.db.fetchall(
            (
                "SELECT conflict_id,record_id,proposed,current_revision FROM "
                "knowledge_conflicts WHERE owner=? AND state='open' ORDER BY "
                "created_at LIMIT 100"
            ),
            (owner,),
        )
        return [
            {
                "conflict_id": str(r[0]),
                "record_id": str(r[1]),
                "proposed": document(str(r[2])),
                "current_revision": int(r[3]),
            }
            for r in rows
        ]

    async def records(
        self, owner: str, *, query: str = "", kind: str | None = None, limit: int = 100
    ) -> list[Record]:
        args: list[object] = [owner]
        sql = (
            "SELECT r.payload FROM knowledge_records r LEFT JOIN "
            "knowledge_policies p ON p.owner=r.owner AND p.room_id=r.room_id "
            "WHERE r.owner=? AND r.deleted=0 AND coalesce(p.excluded,0)=0"
        )
        if kind:
            sql += " AND r.kind=?"
            args.append(kind)
        if query.strip():
            tokens = self.lexical(query).split()[:30]
            match = " OR ".join('"' + token.replace('"', '""') + '"' for token in tokens)
            if not match:
                return []
            sql += (
                " AND r.record_id IN (SELECT record_id FROM knowledge_fts WHERE "
                "knowledge_fts MATCH ? AND owner=?)"
            )
            args.extend([match, owner])
        sql += " ORDER BY r.updated_at DESC LIMIT ?"
        args.append(min(500, limit))
        rows = await self.db.fetchall(sql, args)
        result: list[Record] = []
        for row in rows:
            record = Record.model_validate_json(str(row[0]))
            if await self.eligible(
                owner, Citation(record_id=record.record_id, revision=record.revision)
            ):
                result.append(record)
        return result

    async def allowed_rooms(self, owner: str) -> list[str]:
        rows = await self.db.fetchall(
            (
                "SELECT DISTINCT r.room_id FROM knowledge_records r LEFT JOIN "
                "knowledge_policies p ON p.owner=r.owner AND p.room_id=r.room_id "
                "WHERE r.owner=? AND r.deleted=0 AND coalesce(p.excluded,0)=0"
            ),
            (owner,),
        )
        return [str(row[0]) for row in rows]

    async def resolve_conflict(
        self,
        owner: str,
        conflict_id: str,
        keep_local: bool,
        operation_id: str,
        expected_revision: int,
    ) -> JsonObject:
        async with self.db.transaction() as c:
            row = await one(
                c,
                "SELECT * FROM knowledge_conflicts WHERE owner=? AND conflict_id=?",
                (owner, conflict_id),
            )
            if not row:
                raise KnowledgeConflict("unknown_conflict")
            if row["state"] != "open":
                return {"resolved": True}
            current_row = await one(
                c,
                ("SELECT revision,payload FROM knowledge_records WHERE owner=? AND record_id=?"),
                (owner, row["record_id"]),
            )
            if not current_row or int(current_row[0]) != expected_revision:
                raise KnowledgeConflict("record_changed")
            result: JsonObject = {"resolved": True}
            if keep_local:
                original = Mutation.model_validate_json(str(row["proposed"]))
                proposed = original.model_copy(
                    update={
                        "operation_id": UUID(operation_id),
                        "expected_revision": expected_revision,
                    }
                )
                result = await self._mutate(c, owner, proposed)
                if result["status"] != "accepted":
                    return result
            latest = await one(
                c,
                "SELECT payload FROM knowledge_records WHERE owner=? AND record_id=?",
                (owner, row["record_id"]),
            )
            assert latest is not None
            await self._change(
                c,
                owner,
                "conflict_resolved",
                conflict_id,
                {"conflict_id": conflict_id, "record": document(str(latest[0]))},
            )
            await c.execute(
                ("UPDATE knowledge_conflicts SET state='resolved' WHERE owner=? AND conflict_id=?"),
                (owner, conflict_id),
            )
            return result

    async def create_viewer(self, owner: str, session: str, device: str, ttl: int) -> str:
        import secrets

        token = secrets.token_urlsafe(32)
        async with self.db.transaction() as c:
            await c.execute(
                ("DELETE FROM knowledge_viewers WHERE expires_at<? OR (owner=? AND device_id=?)"),
                (now_ms(), owner, device),
            )
            await c.execute(
                "INSERT INTO knowledge_viewers VALUES(?,?,?,?,?)",
                (
                    hashlib.sha256(token.encode()).hexdigest(),
                    owner,
                    session,
                    device,
                    now_ms() + min(3600, max(60, ttl)) * 1000,
                ),
            )
        return token

    async def validate_viewer(self, token: str) -> str | None:
        if len(token) > 128:
            return None
        row = await self.db.fetchone(
            ("SELECT session_id FROM knowledge_viewers WHERE token_hash=? AND expires_at>?"),
            (hashlib.sha256(token.encode()).hexdigest(), now_ms()),
        )
        if row is None:
            return None
        try:
            await self.private_owner(str(row[0]))
        except KnowledgeAccessError:
            return None
        return str(row[0])

    async def index_formal_memory(self, record: MemoryRecord) -> None:
        if record.state != "active" or record.sensitivity.value == "sensitive":
            await self.remove_formal_memory(record.memory_id)
            return
        draft = RecordDraft(
            record_id="formal:" + str(record.memory_id),
            kind="memory",
            room_id="formal:" + hashlib.sha256(record.namespace.encode()).hexdigest(),
            title=record.text[:256] or "已确认记忆",
            text=record.text,
            confirmed=True,
            occurred_at=int(record.observed_at.timestamp() * 1000),
            origin={
                "source_type": "formal_memory",
                "memory_id": str(record.memory_id),
                "namespace": record.namespace,
                "source_event_ids": [str(i) for i in record.source_event_ids],
            },
        )
        async with self.db.transaction() as c:
            await self.ensure_owner_in(c, "local")
            row = await one(
                c,
                (
                    "SELECT payload,revision FROM knowledge_records WHERE "
                    "owner='local' AND record_id=?"
                ),
                (draft.record_id,),
            )
            if (
                row
                and Record.model_validate_json(str(row[0])).model_dump(
                    exclude={"revision", "updated_at", "deleted"}
                )
                == draft.model_dump()
            ):
                return
            identity = hashlib.sha256(record.model_dump_json().encode()).hexdigest()
            await self._mutate(
                c,
                "local",
                Mutation(
                    operation_id=uuid5(NAMESPACE_URL, "formal-memory:" + identity),
                    expected_revision=int(row[1]) if row else 0,
                    record=draft,
                ),
            )

    async def remove_formal_memory(self, memory_id: UUID) -> None:
        async with self.db.transaction() as c:
            row = await one(
                c,
                ("SELECT payload FROM knowledge_records WHERE owner='local' AND record_id=?"),
                ("formal:" + str(memory_id),),
            )
            if not row:
                return
            record = Record.model_validate_json(str(row[0]))
            if record.deleted:
                return
            await self._mutate(
                c,
                "local",
                Mutation(
                    operation_id=uuid4(),
                    expected_revision=record.revision,
                    action="forget",
                    record=RecordDraft.model_validate(
                        record.model_dump(exclude={"revision", "deleted", "updated_at"})
                    ),
                ),
            )

    async def apply_mutation_in(
        self, c: aiosqlite.Connection, owner: str, mutation: Mutation
    ) -> JsonObject:
        return await self._mutate(c, owner, mutation)

    async def receipt_in(
        self, c: aiosqlite.Connection, owner: str, operation: str, digest: str
    ) -> JsonObject | None:
        return await self._receipt(c, owner, operation, digest)

    async def save_receipt_in(
        self, c: aiosqlite.Connection, owner: str, operation: str, digest: str, receipt: JsonObject
    ) -> None:
        await self._save_receipt(c, owner, operation, digest, receipt)

    async def formal_graph_ready(self, namespaces: Sequence[str]) -> bool:
        if not namespaces:
            return False
        placeholders = ",".join("?" for _ in namespaces)
        row = await self.db.fetchone(
            (
                "SELECT 1 FROM knowledge_relations e JOIN "
                "json_each(e.source_refs) ref JOIN knowledge_records r ON "
                "r.owner=e.owner AND "
                "r.record_id=json_extract(ref.value,'$.record_id') AND "
                "r.revision=json_extract(ref.value,'$.revision') LEFT JOIN "
                "knowledge_policies p ON p.owner=r.owner AND p.room_id=r.room_id "
                "WHERE e.owner='local' AND e.status='confirmed' AND "
                "r.kind='memory' AND r.confirmed=1 AND r.deleted=0 AND "
                "coalesce(p.excluded,0)=0 AND "
                "json_extract(r.payload,'$.origin.source_type')='formal_memory' "
                "AND json_extract(r.payload,'$.origin.namespace') IN ("
            )
            + placeholders
            + ") LIMIT 1",
            list(namespaces),
        )
        return row is not None
