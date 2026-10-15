"Canonical graph projection, fenced leases, temporal reads and human review."

from __future__ import annotations

import hashlib
import json
from collections.abc import AsyncIterator
from datetime import datetime
from typing import Any
from uuid import NAMESPACE_URL, uuid4, uuid5
from zoneinfo import ZoneInfo

from chatwaifu_protocol.base import JsonObject

from chatwaifu_runtime.knowledge.contracts import (
    Citation,
    MergeRequest,
    Mutation,
    Node,
    Projection,
    Record,
    RecordDraft,
    Relation,
    RelationCorrection,
    ReviewRequest,
)
from chatwaifu_runtime.persistence.database import Database
from chatwaifu_runtime.persistence.sqlite_knowledge import (
    KnowledgeConflict,
    SQLiteKnowledgeRepository,
    document,
    encode,
    now_ms,
    one,
)


class SQLiteKnowledgeGraph:
    def __init__(
        self,
        db: Database,
        sources: SQLiteKnowledgeRepository,
        *,
        history_limit: int = 100,
        history_enabled: bool = True,
    ) -> None:
        self.db, self.sources = db, sources
        self.history_limit = history_limit
        self.history_enabled = history_enabled

    async def claim(self) -> JsonObject | None:
        now = now_ms()
        async with self.db.transaction() as c:
            await c.execute(
                (
                    "UPDATE knowledge_jobs SET state='pending',lease=NULL WHERE "
                    "state='leased' AND lease_until<=?"
                ),
                (now,),
            )
            # Excluded source jobs are discarded; explicit removal jobs remain runnable.
            await c.execute(
                "UPDATE knowledge_jobs SET state='superseded' WHERE "
                "state='pending' AND action='index' AND EXISTS (SELECT 1 FROM "
                "knowledge_policies p JOIN knowledge_records r ON r.owner=p.owner"
                " AND r.room_id=p.room_id WHERE r.owner=knowledge_jobs.owner AND "
                "r.record_id=knowledge_jobs.record_id AND p.excluded=1)"
            )
            rows = list(
                await c.execute_fetchall(
                    (
                        "SELECT j.*,r.payload FROM knowledge_jobs j JOIN "
                        "knowledge_records r ON r.owner=j.owner AND "
                        "r.record_id=j.record_id WHERE j.state='pending' AND "
                        "j.next_attempt<=? AND (j.action='remove' OR (r.deleted=0 AND "
                        "r.revision=j.revision)) ORDER BY CASE WHEN j.action='remove' "
                        "THEN 0 ELSE 1 END,j.historical,j.created_at DESC LIMIT 100"
                    ),
                    (now,),
                )
            )
            day = datetime.now(ZoneInfo("Asia/Shanghai")).date().isoformat()
            for row in rows:
                if row["historical"] and row["action"] != "remove" and not self.history_enabled:
                    continue
                owner = str(row["owner"])
                state = await self.sources.ensure_owner_in(c, owner)
                if str(state["history_day"]) != day:
                    await c.execute(
                        "UPDATE knowledge_owners SET history_day=?,history_used=0 WHERE owner=?",
                        (day, owner),
                    )
                elif (
                    row["historical"]
                    and row["action"] != "remove"
                    and self.history_limit > 0
                    and int(state["history_used"]) >= self.history_limit
                ):
                    continue
                if row["action"] == "index":
                    record = Record.model_validate_json(str(row["payload"]))
                    if not await self.sources.eligible_in(
                        c, owner, Citation(record_id=record.record_id, revision=record.revision)
                    ):
                        await c.execute(
                            (
                                "UPDATE knowledge_jobs SET "
                                "state='superseded',error_code='source_unavailable' WHERE "
                                "job_id=?"
                            ),
                            (row["job_id"],),
                        )
                        continue
                    pending_removal = await one(
                        c,
                        (
                            "SELECT 1 FROM knowledge_jobs j JOIN knowledge_records r ON "
                            "r.owner=j.owner AND r.record_id=j.record_id WHERE j.owner=? AND "
                            "r.room_id=? AND j.action='remove' AND j.state IN "
                            "('pending','leased','failed') LIMIT 1"
                        ),
                        (owner, record.room_id),
                    )
                    if pending_removal:
                        continue
                lease = str(uuid4())
                epoch = int(state["policy_epoch"])
                await c.execute(
                    (
                        "UPDATE knowledge_jobs SET "
                        "state='leased',lease=?,lease_until=?,policy_epoch=? WHERE "
                        "job_id=?"
                    ),
                    (lease, now + 600000, epoch, row["job_id"]),
                )
                if row["historical"] and row["action"] != "remove":
                    await c.execute(
                        "UPDATE knowledge_owners SET history_used=history_used+1 WHERE owner=?",
                        (owner,),
                    )
                return {
                    "job_id": str(row["job_id"]),
                    "owner": owner,
                    "lease": lease,
                    "policy_epoch": epoch,
                    "action": str(row["action"]),
                    "record": document(str(row["payload"])),
                }
            return None

    async def renew(self, job: JsonObject) -> bool:
        count = await self.db.execute(
            (
                "UPDATE knowledge_jobs SET lease_until=? WHERE job_id=? AND "
                "state='leased' AND lease=? AND policy_epoch=(SELECT policy_epoch"
                " FROM knowledge_owners WHERE owner=knowledge_jobs.owner)"
            ),
            (now_ms() + 600000, str(job["job_id"]), str(job["lease"])),
        )
        return count == 1

    async def fail(
        self, job: JsonObject, cancelled: bool = False, error_code: str = "index_failed"
    ) -> None:
        await self.db.execute(
            (
                "UPDATE knowledge_jobs SET state=CASE WHEN attempts>=4 AND ?=0 "
                "THEN 'failed' ELSE 'pending' "
                "END,attempts=attempts+?,lease=NULL,next_attempt=?,error_code=? "
                "WHERE job_id=? AND state='leased' AND lease=?"
            ),
            (
                int(cancelled),
                0 if cancelled else 1,
                now_ms() + (0 if cancelled else 60000),
                "cancelled" if cancelled else error_code[:80],
                str(job["job_id"]),
                str(job["lease"]),
            ),
        )

    async def versions(self, owner: str, record_id: str) -> list[Record]:
        rows = await self.db.fetchall(
            (
                "SELECT payload FROM knowledge_versions WHERE owner=? AND "
                "record_id=? ORDER BY revision"
            ),
            (owner, record_id),
        )
        return [Record.model_validate_json(str(row[0])) for row in rows]

    async def complete_removal(self, job: JsonObject) -> bool:
        return (
            await self.db.execute(
                (
                    "UPDATE knowledge_jobs SET "
                    "state='done',lease=NULL,error_code=NULL WHERE job_id=? AND "
                    "state='leased' AND lease=?"
                ),
                (str(job["job_id"]), str(job["lease"])),
            )
            == 1
        )

    async def publish(self, job: JsonObject, projection: Projection) -> bool:
        owner = str(job["owner"])
        record = Record.model_validate(job["record"])
        current = Citation(record_id=record.record_id, revision=record.revision)
        for relation in projection.relations:
            if current not in relation.source_refs:
                raise ValueError("projection_missing_current_evidence")
        async with self.db.transaction() as c:
            ticket = await one(
                c,
                "SELECT state,lease,lease_until FROM knowledge_jobs WHERE job_id=?",
                (str(job["job_id"]),),
            )
            state = await self.sources.ensure_owner_in(c, owner)
            row = await one(
                c,
                ("SELECT revision,deleted FROM knowledge_records WHERE owner=? AND record_id=?"),
                (owner, record.record_id),
            )
            if (
                not ticket
                or ticket[0] != "leased"
                or ticket[1] != job["lease"]
                or int(ticket[2]) <= now_ms()
                or int(state["policy_epoch"]) != job["policy_epoch"]
                or not row
                or row[1]
                or int(row[0]) != record.revision
                or not await self.sources.eligible_in(c, owner, current)
            ):
                return False
            # All cited versions are checked inside the same commit transaction.
            for edge in projection.relations:
                for ref in edge.source_refs:
                    r = await one(
                        c,
                        (
                            "SELECT r.revision,r.deleted,coalesce(p.excluded,0) FROM "
                            "knowledge_records r LEFT JOIN knowledge_policies p ON "
                            "p.owner=r.owner AND p.room_id=r.room_id WHERE r.owner=? AND "
                            "r.record_id=?"
                        ),
                        (owner, ref.record_id),
                    )
                    if not r or r[1] or r[2] or int(r[0]) != ref.revision:
                        return False
            ids = {n.node_id for n in projection.nodes}
            if any(
                edge.source not in ids or edge.target not in ids for edge in projection.relations
            ):
                raise ValueError("projection_unknown_endpoint")
            for node in projection.nodes:
                if node.room_id != record.room_id:
                    raise ValueError("projection_outside_room")
                await c.execute(
                    (
                        "INSERT INTO "
                        "knowledge_nodes(owner,node_id,kind,label,room_id,stable_identity)"
                        " VALUES(?,?,?,?,?,?) ON CONFLICT(owner,node_id) DO UPDATE SET "
                        "label=CASE WHEN label_locked=0 THEN excluded.label ELSE "
                        "knowledge_nodes.label END"
                    ),
                    (
                        owner,
                        node.node_id,
                        node.kind,
                        node.label,
                        node.room_id,
                        node.stable_identity,
                    ),
                )
                await c.execute(
                    "INSERT OR IGNORE INTO knowledge_node_sources VALUES(?,?,?,?)",
                    (owner, node.node_id, record.record_id, record.revision),
                )
            for edge in projection.relations:
                # Reindexing never overrides a human decision or changes reviewed text.
                await c.execute(
                    (
                        "INSERT INTO "
                        "knowledge_relations(owner,relation_id,source,target,predicate,description,status,valid_from,valid_to,source_refs,created_at)"
                        " VALUES(?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(owner,relation_id) DO"
                        " NOTHING"
                    ),
                    (
                        owner,
                        edge.relation_id,
                        edge.source,
                        edge.target,
                        edge.predicate,
                        edge.description,
                        "proposed",
                        edge.valid_from,
                        edge.valid_to,
                        encode([r.model_dump() for r in edge.source_refs]),
                        now_ms(),
                    ),
                )
            if record.confirmed and record.kind in ("memory", "todo", "event"):
                object_id = (
                    "object:"
                    + hashlib.sha256((owner + "\0" + record.record_id).encode()).hexdigest()
                )
                for node in projection.nodes:
                    relation_id = (
                        "evidence:"
                        + hashlib.sha256(
                            (object_id + "\0" + node.node_id + "\0" + str(record.revision)).encode()
                        ).hexdigest()
                    )
                    await c.execute(
                        (
                            "INSERT OR IGNORE INTO "
                            "knowledge_relations(owner,relation_id,source,target,predicate,description,status,source_refs,created_at)"
                            " VALUES(?,?,?,?,?,?,?,?,?)"
                        ),
                        (
                            owner,
                            relation_id,
                            object_id,
                            node.node_id,
                            "derived_from",
                            "已确认记录的实体关联，仍需核实",
                            "proposed",
                            encode([current.model_dump()]),
                            now_ms(),
                        ),
                    )
            for invalidation in projection.invalidations:
                mapped = await c.execute_fetchall(
                    ("SELECT relation_id FROM knowledge_index_edges WHERE owner=? AND index_id=?"),
                    (owner, invalidation.index_id),
                )
                for match in mapped:
                    prior = await one(
                        c,
                        "SELECT * FROM knowledge_relations WHERE owner=? AND relation_id=?",
                        (owner, match[0]),
                    )
                    if prior:
                        newer_evidence = False
                        for source in json.loads(str(prior["source_refs"])):
                            previous = await one(
                                c,
                                (
                                    "SELECT payload FROM knowledge_versions WHERE owner=? AND "
                                    "record_id=? AND revision=?"
                                ),
                                (owner, source["record_id"], source["revision"]),
                            )
                            if (
                                previous
                                and Record.model_validate_json(str(previous[0])).occurred_at
                                > record.occurred_at
                            ):
                                newer_evidence = True
                                break
                        if newer_evidence:
                            continue  # Late historical batches cannot invalidate a newer agreement.
                    if prior and prior["status"] == "proposed":
                        await c.execute(
                            (
                                "UPDATE knowledge_relations SET "
                                "status='superseded',valid_to=?,revision=revision+1 WHERE owner=?"
                                " AND relation_id=?"
                            ),
                            (invalidation.valid_to, owner, match[0]),
                        )
                    elif prior and prior["status"] == "confirmed":
                        await c.execute(
                            (
                                "INSERT INTO "
                                "knowledge_reviews(owner,object_id,action,previous,created_at) "
                                "VALUES(?,?,?,?,?)"
                            ),
                            (
                                owner,
                                match[0],
                                "invalidation_proposed",
                                encode(
                                    {
                                        "source": current.model_dump(),
                                        "valid_to": invalidation.valid_to,
                                    }
                                ),
                                now_ms(),
                            ),
                        )
            for index_id, relation_id in projection.index_refs.items():
                if relation_id not in {e.relation_id for e in projection.relations}:
                    raise ValueError("projection_unknown_index_edge")
                await c.execute(
                    "INSERT OR IGNORE INTO knowledge_index_edges VALUES(?,?,?)",
                    (owner, index_id, relation_id),
                )
            await c.execute(
                (
                    "UPDATE knowledge_jobs SET "
                    "state='done',lease=NULL,error_code=NULL WHERE job_id=?"
                ),
                (str(job["job_id"]),),
            )
            await c.execute(
                ("UPDATE knowledge_owners SET graph_revision=graph_revision+1 WHERE owner=?"),
                (owner,),
            )
            return True

    async def _evidence_allowed(self, owner: str, refs: list[Citation], historical: bool) -> bool:
        for ref in refs:
            if not await self.sources.eligible(owner, ref, historical=historical):
                return False
        return bool(refs)

    async def graph(
        self,
        owner: str,
        *,
        query: str = "",
        center: str | None = None,
        kind: str | None = None,
        at: int | None = None,
        hops: int = 2,
        limit: int = 500,
        index_ids: list[str] | None = None,
    ) -> JsonObject:
        epoch = await self.sources.epoch(owner)
        state = await self.db.fetchone(
            "SELECT graph_revision FROM knowledge_owners WHERE owner=?", (owner,)
        )
        revision = int(state[0]) if state else 0
        limit = min(500, max(1, limit))
        nodes: dict[str, dict[str, Any]] = {}
        edges: dict[str, dict[str, Any]] = {}
        truncated = False
        evidence_cache: dict[tuple[str, int], bool] = {}

        async def evidence(refs: list[Citation]) -> bool:
            if not refs:
                return False
            for ref in refs:
                key = (ref.record_id, ref.revision)
                if key not in evidence_cache:
                    evidence_cache[key] = await self.sources.eligible(
                        owner, ref, historical=at is not None
                    )
                if not evidence_cache[key]:
                    return False
            return True

        async def node(key: str) -> str | None:
            seen: set[str] = set()
            while key not in seen:
                seen.add(key)
                row = await self.db.fetchone(
                    "SELECT * FROM knowledge_nodes WHERE owner=? AND node_id=?", (owner, key)
                )
                if not row:
                    return None
                if row["merged_into"]:
                    key = str(row["merged_into"])
                    continue
                refs = await self.db.fetchall(
                    (
                        "SELECT record_id,revision FROM knowledge_node_sources WHERE "
                        "owner=? AND node_id=? LIMIT 1000"
                    ),
                    (owner, key),
                )
                visible = False
                for ref in refs:
                    if await evidence([Citation(record_id=str(ref[0]), revision=int(ref[1]))]):
                        visible = True
                        break
                if not visible:
                    return None
                nodes[key] = dict(row)
                return key
            return None

        seeds: list[str] = []
        if center:
            seeds.append(center)
        if query:
            terms = [word for word in query.split() if word][:12]
            conditions = " AND ".join("instr(lower(label),lower(?))>0" for _ in terms)
            rows = await self.db.fetchall(
                ("SELECT node_id FROM knowledge_nodes WHERE owner=? AND merged_into IS NULL AND ")
                + conditions
                + " LIMIT 500",
                [owner, *terms],
            )
            seeds.extend(str(row[0]) for row in rows)
            descriptions = " AND ".join("instr(lower(description),lower(?))>0" for _ in terms)
            rows = await self.db.fetchall(
                (
                    "SELECT source,target FROM knowledge_relations WHERE owner=? AND "
                    "status NOT IN ('forgotten','rejected') AND "
                )
                + descriptions
                + " ORDER BY created_at DESC LIMIT 100",
                [owner, *terms],
            )
            for row in rows:
                seeds.extend((str(row[0]), str(row[1])))
        if index_ids:
            for key in index_ids[:50]:
                matches = await self.db.fetchall(
                    (
                        "SELECT e.source,e.target FROM knowledge_relations e JOIN "
                        "knowledge_index_edges i ON i.owner=e.owner AND "
                        "i.relation_id=e.relation_id WHERE i.owner=? AND i.index_id=? AND"
                        " e.status NOT IN ('forgotten','rejected')"
                    ),
                    (owner, key),
                )
                for row in matches:
                    seeds.extend((str(row[0]), str(row[1])))
        if not query and not center:
            rows = await self.db.fetchall(
                (
                    "SELECT n.node_id FROM knowledge_nodes n JOIN "
                    "knowledge_node_sources ns ON ns.owner=n.owner AND "
                    "ns.node_id=n.node_id JOIN knowledge_records r ON "
                    "r.owner=ns.owner AND r.record_id=ns.record_id LEFT JOIN "
                    "knowledge_policies p ON p.owner=r.owner AND p.room_id=r.room_id "
                    "WHERE n.owner=? AND n.merged_into IS NULL AND r.deleted=0 AND "
                    "coalesce(p.excluded,0)=0 AND (? IS NULL OR n.kind=?) GROUP BY "
                    "n.node_id ORDER BY max(r.updated_at) DESC LIMIT 1000"
                ),
                (owner, kind, kind),
            )
            seeds.extend(str(row[0]) for row in rows)
        chosen: set[str] = set()
        for key in dict.fromkeys(seeds):
            resolved = await node(key)
            if resolved and (not kind or nodes[resolved]["kind"] == kind):
                chosen.add(resolved)
            if len(chosen) >= limit:
                truncated = True
                break
        frontier = set(chosen)
        for _ in range(min(3, max(1, hops))):
            if not frontier:
                break
            lookup = set(frontier)
            for key in frontier:
                aliases = await self.db.fetchall(
                    (
                        "WITH RECURSIVE aliases(id) AS (SELECT node_id FROM "
                        "knowledge_nodes WHERE owner=? AND merged_into=? UNION SELECT "
                        "n.node_id FROM knowledge_nodes n JOIN aliases a ON "
                        "n.merged_into=a.id WHERE n.owner=?) SELECT id FROM aliases LIMIT"
                        " 500"
                    ),
                    (owner, key, owner),
                )
                lookup.update(str(row[0]) for row in aliases)
            placeholders = ",".join("?" for _ in lookup)
            raw = await self.db.fetchall(
                "SELECT * FROM knowledge_relations WHERE owner=? AND (source IN ("
                + placeholders
                + ") OR target IN ("
                + placeholders
                + ")) AND status NOT IN ('rejected','forgotten') ORDER BY "
                "created_at DESC LIMIT 3000",
                [owner, *lookup, *lookup],
            )
            truncated |= len(raw) >= 3000
            next_frontier: set[str] = set()
            for row in raw:
                point = now_ms() if at is None else at
                if (
                    (at is None and row["status"] == "superseded")
                    or (row["valid_to"] is not None and int(row["valid_to"]) <= point)
                    or (row["valid_from"] is not None and int(row["valid_from"]) > point)
                ):
                    continue
                refs = [Citation.model_validate(ref) for ref in json.loads(str(row["source_refs"]))]
                if not await evidence(refs):
                    continue
                source, target = await node(str(row["source"])), await node(str(row["target"]))
                if not source or not target:
                    continue
                endpoints = {source, target}
                if kind and any(nodes[key]["kind"] != kind for key in endpoints):
                    continue
                if len(chosen | endpoints) > limit:
                    truncated = True
                    continue
                next_frontier.update(endpoints - chosen)
                chosen.update(endpoints)
                item = {k: v for k, v in dict(row).items() if k != "owner"}
                item.update(
                    source=source, target=target, source_refs=[ref.model_dump() for ref in refs]
                )
                change = await self.db.fetchone(
                    (
                        "SELECT 1 FROM knowledge_reviews WHERE owner=? AND object_id=? "
                        "AND action='invalidation_proposed' LIMIT 1"
                    ),
                    (owner, row["relation_id"]),
                )
                item["change_proposed"] = change is not None
                edges[str(row["relation_id"])] = item
            frontier = next_frontier
        final = await self.db.fetchone(
            "SELECT graph_revision,policy_epoch FROM knowledge_owners WHERE owner=?", (owner,)
        )
        if final and (int(final[0]) != revision or int(final[1]) != epoch):
            raise KnowledgeConflict("graph_changed_retry")
        return document(
            encode(
                {
                    "schema_version": "1.0",
                    "nodes": [
                        {
                            k: v
                            for k, v in nodes[key].items()
                            if k not in ("owner", "merged_into", "label_locked")
                        }
                        for key in sorted(chosen)
                    ],
                    "edges": list(edges.values())[:1500],
                    "revision": revision,
                    "policy_epoch": epoch,
                    "truncated": truncated or len(edges) > 1500,
                }
            )
        )

    async def review(self, owner: str, relation: str, body: ReviewRequest) -> JsonObject:
        async with self.db.transaction() as c:
            row = await one(
                c,
                "SELECT * FROM knowledge_relations WHERE owner=? AND relation_id=?",
                (owner, relation),
            )
            if not row or int(row["revision"]) != body.expected_revision:
                raise KnowledgeConflict("relation_changed")
            for raw_ref in json.loads(str(row["source_refs"])):
                if not await self.sources.eligible_in(c, owner, Citation.model_validate(raw_ref)):
                    raise KnowledgeConflict("source_changed")
            status = {"confirm": "confirmed", "reject": "rejected", "forget": "forgotten"}[
                body.decision
            ]
            await c.execute(
                (
                    "INSERT INTO "
                    "knowledge_reviews(owner,object_id,action,previous,created_at) "
                    "VALUES(?,?,?,?,?)"
                ),
                (owner, relation, body.decision, encode(dict(row)), now_ms()),
            )
            await c.execute(
                (
                    "UPDATE knowledge_relations SET status=?,revision=revision+1 "
                    "WHERE owner=? AND relation_id=?"
                ),
                (status, owner, relation),
            )
            await c.execute(
                ("UPDATE knowledge_owners SET graph_revision=graph_revision+1 WHERE owner=?"),
                (owner,),
            )
            return {"status": status, "revision": body.expected_revision + 1}

    async def merge(self, owner: str, body: MergeRequest) -> JsonObject:
        async with self.db.transaction() as c:
            previous = await one(
                c,
                ("SELECT snapshot,state FROM knowledge_merges WHERE owner=? AND operation_id=?"),
                (owner, str(body.operation_id)),
            )
            digest = hashlib.sha256(body.model_dump_json().encode()).hexdigest()
            if previous:
                snapshot = json.loads(str(previous["snapshot"]))
                if snapshot["request_hash"] != digest:
                    raise KnowledgeConflict("operation_id_reused")
                return {
                    "merged": True,
                    "operation_id": str(body.operation_id),
                    "state": str(previous["state"]),
                }
            state = await self.sources.ensure_owner_in(c, owner)
            if int(state["graph_revision"]) != body.expected_graph_revision:
                raise KnowledgeConflict("graph_changed")
            if body.target_id not in body.source_ids:
                raise KnowledgeConflict("merge_target_not_selected")
            snapshots: list[dict[str, Any]] = []
            for key in set(body.source_ids):
                row = await one(
                    c, "SELECT * FROM knowledge_nodes WHERE owner=? AND node_id=?", (owner, key)
                )
                if not row or row["merged_into"]:
                    raise KnowledgeConflict("entity_changed")
                refs = await c.execute_fetchall(
                    (
                        "SELECT record_id,revision FROM knowledge_node_sources WHERE "
                        "owner=? AND node_id=?"
                    ),
                    (owner, key),
                )
                visible = False
                for ref in refs:
                    if await self.sources.eligible_in(
                        c, owner, Citation(record_id=str(ref[0]), revision=int(ref[1]))
                    ):
                        visible = True
                        break
                if not visible:
                    raise KnowledgeConflict("entity_unavailable")
                snapshots.append(
                    {"node_id": key, "kind": str(row["kind"]), "merged_into": row["merged_into"]}
                )
            if len({r["kind"] for r in snapshots}) != 1:
                raise KnowledgeConflict("different_entity_types")
            await c.execute(
                "INSERT INTO knowledge_merges VALUES(?,?,?,'active')",
                (
                    owner,
                    str(body.operation_id),
                    encode({"nodes": snapshots, "target": body.target_id, "request_hash": digest}),
                ),
            )
            for key in body.source_ids:
                if key != body.target_id:
                    await c.execute(
                        "UPDATE knowledge_nodes SET merged_into=? WHERE owner=? AND node_id=?",
                        (body.target_id, owner, key),
                    )
            await c.execute(
                ("UPDATE knowledge_owners SET graph_revision=graph_revision+1 WHERE owner=?"),
                (owner,),
            )
            return {
                "merged": True,
                "operation_id": str(body.operation_id),
                "revision": body.expected_graph_revision + 1,
            }

    async def split(self, owner: str, operation_id: str, expected_revision: int) -> None:
        async with self.db.transaction() as c:
            state = await self.sources.ensure_owner_in(c, owner)
            if int(state["graph_revision"]) != expected_revision:
                raise KnowledgeConflict("graph_changed")
            row = await one(
                c,
                ("SELECT snapshot,state FROM knowledge_merges WHERE owner=? AND operation_id=?"),
                (owner, operation_id),
            )
            if not row or row[1] != "active":
                raise KnowledgeConflict("merge_not_active")
            snapshot = json.loads(str(row[0]))
            for node in snapshot["nodes"]:
                current = await one(
                    c,
                    "SELECT merged_into FROM knowledge_nodes WHERE owner=? AND node_id=?",
                    (owner, node["node_id"]),
                )
                expected = (
                    node["merged_into"]
                    if node["node_id"] == snapshot["target"]
                    else snapshot["target"]
                )
                if current is None or current[0] != expected:
                    raise KnowledgeConflict("later_merge_requires_review")
            for node in snapshot["nodes"]:
                await c.execute(
                    "UPDATE knowledge_nodes SET merged_into=? WHERE owner=? AND node_id=?",
                    (node["merged_into"], owner, node["node_id"]),
                )
            await c.execute(
                ("UPDATE knowledge_merges SET state='reverted' WHERE owner=? AND operation_id=?"),
                (owner, operation_id),
            )
            await c.execute(
                ("UPDATE knowledge_owners SET graph_revision=graph_revision+1 WHERE owner=?"),
                (owner,),
            )

    async def entity(self, owner: str, node_id: str) -> JsonObject:
        row = await self.db.fetchone(
            "SELECT * FROM knowledge_nodes WHERE owner=? AND node_id=?", (owner, node_id)
        )
        if row is None:
            raise KnowledgeConflict("entity_not_found")
        refs = await self.db.fetchall(
            ("SELECT record_id,revision FROM knowledge_node_sources WHERE owner=? AND node_id=?"),
            (owner, node_id),
        )
        citations: list[dict[str, Any]] = []
        for ref in refs:
            citation = Citation(record_id=str(ref[0]), revision=int(ref[1]))
            if await self.sources.eligible(owner, citation):
                citations.append(citation.model_dump())
        if not citations:
            raise KnowledgeConflict("entity_unavailable")
        return document(encode({"node": dict(row), "sources": citations}))

    async def rename(self, owner: str, node_id: str, label: str, expected_revision: int) -> None:
        if not label.strip() or len(label) > 256:
            raise ValueError("invalid_label")
        await self.entity(owner, node_id)
        async with self.db.transaction() as c:
            state = await self.sources.ensure_owner_in(c, owner)
            if int(state["graph_revision"]) != expected_revision:
                raise KnowledgeConflict("graph_changed")
            old = await one(
                c, "SELECT label FROM knowledge_nodes WHERE owner=? AND node_id=?", (owner, node_id)
            )
            await c.execute(
                (
                    "INSERT INTO "
                    "knowledge_reviews(owner,object_id,action,previous,created_at) "
                    "VALUES(?,?,?,?,?)"
                ),
                (owner, node_id, "rename", encode({"label": str(old[0]) if old else ""}), now_ms()),
            )
            await c.execute(
                ("UPDATE knowledge_nodes SET label=?,label_locked=1 WHERE owner=? AND node_id=?"),
                (label.strip(), owner, node_id),
            )
            await c.execute(
                ("UPDATE knowledge_owners SET graph_revision=graph_revision+1 WHERE owner=?"),
                (owner,),
            )

    async def retry_failed(self, owner: str) -> None:
        await self.db.execute(
            (
                "UPDATE knowledge_jobs SET "
                "state='pending',attempts=0,next_attempt=0 WHERE owner=? AND "
                "state='failed'"
            ),
            (owner,),
        )

    async def rebuild_revision(self) -> str:
        rows = await self.db.fetchall(
            "SELECT owner,graph_revision,policy_epoch FROM knowledge_owners ORDER BY owner"
        )
        return hashlib.sha256(encode([list(row) for row in rows]).encode()).hexdigest()

    async def rebuild_groups(self) -> dict[str, list[str]]:
        rows = await self.db.fetchall("SELECT DISTINCT owner,room_id FROM knowledge_records")
        result: dict[str, list[str]] = {}
        for row in rows:
            result.setdefault(str(row[0]), []).append(str(row[1]))
        return result

    async def rebuild_pages(
        self,
    ) -> AsyncIterator[tuple[str, list[Node], list[Relation], list[Record]]]:
        owners = await self.db.fetchall("SELECT owner FROM knowledge_owners ORDER BY owner")
        for owner_row in owners:
            owner = str(owner_row[0])
            cursor = ""
            while True:
                rows = await self.db.fetchall(
                    (
                        "SELECT * FROM knowledge_relations WHERE owner=? AND "
                        "relation_id>? AND status NOT IN ('forgotten','rejected') ORDER "
                        "BY relation_id LIMIT 20"
                    ),
                    (owner, cursor),
                )
                if not rows:
                    break
                cursor = str(rows[-1]["relation_id"])
                nodes: dict[str, Node] = {}
                records: dict[tuple[str, int], Record] = {}
                edges: list[Relation] = []
                for row in rows:
                    refs = [
                        Citation.model_validate(ref) for ref in json.loads(str(row["source_refs"]))
                    ]
                    if not await self._evidence_allowed(owner, refs, True):
                        continue
                    endpoints: list[Node] = []
                    for key in (str(row["source"]), str(row["target"])):
                        node = await self.db.fetchone(
                            (
                                "SELECT node_id,kind,label,room_id,stable_identity FROM "
                                "knowledge_nodes WHERE owner=? AND node_id=?"
                            ),
                            (owner, key),
                        )
                        if node:
                            endpoints.append(Node.model_validate(dict(node)))
                    if len(endpoints) != 2:
                        continue
                    for endpoint in endpoints:
                        nodes[endpoint.node_id] = endpoint
                    for ref in refs:
                        record = await self.sources.get(owner, ref.record_id, ref.revision)
                        assert record is not None
                        records[(ref.record_id, ref.revision)] = record
                    fields = {k: v for k, v in dict(row).items() if k in Relation.model_fields}
                    fields["source_refs"] = [ref.model_dump() for ref in refs]
                    edges.append(Relation.model_validate(fields))
                if edges:
                    yield owner, list(nodes.values()), edges, list(records.values())

    async def restore_aliases(self, owner: str, aliases: dict[str, str]) -> None:
        async with self.db.transaction() as c:
            for key, value in aliases.items():
                await c.execute(
                    "INSERT OR REPLACE INTO knowledge_index_edges VALUES(?,?,?)",
                    (owner, key, value),
                )

    async def correct_relation(self, owner: str, key: str, body: RelationCorrection) -> JsonObject:
        digest = hashlib.sha256((key + body.model_dump_json()).encode()).hexdigest()
        async with self.db.transaction() as c:
            prior_receipt = await self.sources.receipt_in(c, owner, str(body.operation_id), digest)
            if prior_receipt is not None:
                return prior_receipt
            row = await one(
                c, "SELECT * FROM knowledge_relations WHERE owner=? AND relation_id=?", (owner, key)
            )
            if (
                row is None
                or int(row["revision"]) != body.expected_revision
                or row["status"] == "forgotten"
            ):
                raise KnowledgeConflict("relation_changed")
            parents: dict[tuple[str, int], Citation] = {}
            room = "manual"
            for value in json.loads(str(row["source_refs"])):
                ref = Citation.model_validate(value)
                if not await self.sources.eligible_in(c, owner, ref):
                    raise KnowledgeConflict("source_changed")
                snapshot = await one(
                    c,
                    (
                        "SELECT payload FROM knowledge_versions WHERE owner=? AND "
                        "record_id=? AND revision=?"
                    ),
                    (owner, ref.record_id, ref.revision),
                )
                assert snapshot is not None
                original = Record.model_validate_json(str(snapshot[0]))
                room = original.room_id
                for parent in [ref] if original.kind == "source" else original.source_refs:
                    parents[(parent.record_id, parent.revision)] = parent
            for endpoint in (body.source, body.target):
                refs = await c.execute_fetchall(
                    (
                        "SELECT record_id,revision FROM knowledge_node_sources WHERE "
                        "owner=? AND node_id=?"
                    ),
                    (owner, endpoint),
                )
                visible = False
                for ref in refs:
                    if await self.sources.eligible_in(
                        c, owner, Citation(record_id=str(ref[0]), revision=int(ref[1]))
                    ):
                        visible = True
                        break
                if not visible:
                    raise KnowledgeConflict("entity_unavailable")
            if len(parents) > 30:
                raise KnowledgeConflict("correction_evidence_limit")
            annotation_id = "correction:" + str(body.operation_id)
            receipt = await self.sources.apply_mutation_in(
                c,
                owner,
                Mutation(
                    operation_id=uuid5(
                        NAMESPACE_URL, "relation-correction:" + str(body.operation_id)
                    ),
                    expected_revision=0,
                    record=RecordDraft(
                        record_id=annotation_id,
                        kind="reference",
                        room_id=room,
                        title="用户更正关系",
                        text=body.description,
                        confirmed=True,
                        occurred_at=now_ms(),
                        sent_by_owner=True,
                        sender_name="我",
                        source_refs=list(parents.values()),
                        origin={"source_type": "user_annotation", "relation_id": key},
                    ),
                ),
            )
            if receipt["status"] != "accepted":
                raise KnowledgeConflict("correction_not_accepted")
            await c.execute(
                (
                    "INSERT INTO "
                    "knowledge_reviews(owner,object_id,action,previous,created_at) "
                    "VALUES(?,?,?,?,?)"
                ),
                (owner, key, "correct", encode(dict(row)), now_ms()),
            )
            await c.execute(
                (
                    "UPDATE knowledge_relations SET "
                    "source=?,target=?,predicate=?,description=?,status='confirmed',revision=revision+1,valid_from=?,valid_to=?,source_refs=?"
                    " WHERE owner=? AND relation_id=?"
                ),
                (
                    body.source,
                    body.target,
                    body.predicate,
                    body.description,
                    body.valid_from,
                    body.valid_to,
                    encode([Citation(record_id=annotation_id, revision=1).model_dump()]),
                    owner,
                    key,
                ),
            )
            await c.execute(
                (
                    "DELETE FROM knowledge_reviews WHERE owner=? AND object_id=? AND "
                    "action='invalidation_proposed'"
                ),
                (owner, key),
            )
            await c.execute(
                ("UPDATE knowledge_owners SET graph_revision=graph_revision+1 WHERE owner=?"),
                (owner,),
            )
            result: JsonObject = {
                "updated": True,
                "revision": body.expected_revision + 1,
                "annotation": {"record_id": annotation_id, "revision": 1},
            }
            await self.sources.save_receipt_in(c, owner, str(body.operation_id), digest, result)
            return result

    async def relation_history(self, owner: str, key: str) -> JsonObject:
        async with self.db.transaction() as c:
            current = await one(
                c,
                ("SELECT source_refs FROM knowledge_relations WHERE owner=? AND relation_id=?"),
                (owner, key),
            )
            if current is None:
                raise KnowledgeConflict("relation_unavailable")
            for ref in json.loads(str(current[0])):
                if not await self.sources.eligible_in(c, owner, Citation.model_validate(ref)):
                    raise KnowledgeConflict("relation_unavailable")
            rows = await c.execute_fetchall(
                (
                    "SELECT action,previous,created_at FROM knowledge_reviews WHERE "
                    "owner=? AND object_id=? ORDER BY sequence DESC LIMIT 30"
                ),
                (owner, key),
            )
            items: list[JsonObject] = []
            for row in rows:
                previous = document(str(row[1]))
                old_refs = previous.get("source_refs")
                allowed = True
                if isinstance(old_refs, str):
                    for ref in json.loads(old_refs):
                        if not await self.sources.eligible_in(
                            c, owner, Citation.model_validate(ref), historical=True
                        ):
                            allowed = False
                            break
                if allowed:
                    items.append({"action": str(row[0]), "previous": previous, "at": int(row[2])})
            return document(encode({"items": items}))

    async def validate_snapshot(
        self, owner: str, epoch: int, refs: list[Citation], edges: dict[str, int], historical: bool
    ) -> bool:
        async with self.db.transaction() as c:
            state = await self.sources.ensure_owner_in(c, owner)
            if int(state["policy_epoch"]) != epoch:
                return False
            for ref in refs:
                if not await self.sources.eligible_in(c, owner, ref, historical=historical):
                    return False
            for key, revision in edges.items():
                row = await one(
                    c,
                    ("SELECT revision FROM knowledge_relations WHERE owner=? AND relation_id=?"),
                    (owner, key),
                )
                if row is None or int(row[0]) != revision:
                    return False
            return True
