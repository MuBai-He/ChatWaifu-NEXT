"""SQLite projection adapter for structured memory records."""

from __future__ import annotations

import json
import re
from collections.abc import Sequence
from datetime import datetime
from typing import cast
from uuid import UUID

from chatwaifu_protocol.memory import MemoryProposal, MemoryRecord, MemorySource

from chatwaifu_runtime.memory.repository import (
    MemoryEventEvidence,
    MemoryRepository,
    MemorySearchHit,
)
from chatwaifu_runtime.persistence.database import Database

_RECORD_SELECT = """
SELECT record.*,
       COALESCE(
           (SELECT json_group_array(source_event_id)
            FROM memory_sources WHERE memory_id = record.memory_id),
           '[]'
       ) AS source_event_ids_json
FROM memory_records AS record
"""
_ASCII_WORD = re.compile(r"[a-z0-9_]{2,}")
_CJK_RUN = re.compile(r"[\u3400-\u9fff]+")


class SQLiteMemoryRepository(MemoryRepository):
    def __init__(self, database: Database) -> None:
        self._database = database

    async def event_exists(self, event_id: UUID) -> bool:
        return (
            await self._database.fetchone(
                "SELECT 1 FROM events WHERE event_id = ?", (str(event_id),)
            )
            is not None
        )

    async def event_evidence(self, event_id: UUID) -> MemoryEventEvidence | None:
        row = await self._database.fetchone(
            "SELECT event_id, event_type, session_id, occurred_at, envelope_json "
            "FROM events WHERE event_id = ?",
            (str(event_id),),
        )
        if row is None:
            return None
        envelope = cast(dict[str, object], json.loads(str(row["envelope_json"])))
        turn_id = envelope.get("turn_id")
        return MemoryEventEvidence(
            event_id=UUID(str(row["event_id"])),
            session_id=UUID(str(row["session_id"])),
            turn_id=UUID(str(turn_id)) if turn_id else None,
            occurred_at=datetime.fromisoformat(str(row["occurred_at"])),
            event_type=str(row["event_type"]),
        )

    async def find_exact(self, namespace: str, normalized_text: str) -> MemoryRecord | None:
        row = await self._database.fetchone(
            _RECORD_SELECT + " WHERE record.namespace = ? AND record.normalized_text = ? "
            "AND record.state = 'active' ORDER BY record.created_at DESC LIMIT 1",
            (namespace, normalized_text),
        )
        return _record_from_row(dict(row)) if row is not None else None

    async def find_identity(
        self, namespace: str, subject_id: str, predicate: str
    ) -> list[MemoryRecord]:
        rows = await self._database.fetchall(
            _RECORD_SELECT + " WHERE record.namespace = ? AND record.subject_id = ? "
            "AND record.predicate = ? AND record.state = 'active' "
            "ORDER BY record.created_at DESC",
            (namespace, subject_id, predicate),
        )
        return [_record_from_row(dict(row)) for row in rows]

    async def get(self, memory_id: UUID) -> MemoryRecord | None:
        row = await self._database.fetchone(
            _RECORD_SELECT + " WHERE record.memory_id = ?", (str(memory_id),)
        )
        return _record_from_row(dict(row)) if row is not None else None

    async def get_many(self, memory_ids: Sequence[UUID]) -> list[MemoryRecord]:
        if not memory_ids:
            return []
        placeholders = ", ".join("?" for _ in memory_ids)
        rows = await self._database.fetchall(
            _RECORD_SELECT + f" WHERE record.memory_id IN ({placeholders})",
            tuple(str(item) for item in memory_ids),
        )
        by_id = {
            record.memory_id: record for record in (_record_from_row(dict(row)) for row in rows)
        }
        return [by_id[memory_id] for memory_id in memory_ids if memory_id in by_id]

    async def create_record(
        self,
        record: MemoryRecord,
        sources: Sequence[MemorySource],
        *,
        supersede_target: UUID | None = None,
    ) -> None:
        if not sources:
            raise ValueError("memory records require at least one source")
        async with self._database.transaction() as connection:
            if supersede_target is not None:
                await connection.execute(
                    """
                    UPDATE memory_records SET state = 'superseded', updated_at = ?
                    WHERE memory_id = ? AND state = 'active'
                    """,
                    (record.created_at.isoformat(), str(supersede_target)),
                )
            await connection.execute(
                """
                INSERT INTO memory_records(
                    memory_id, namespace, kind, subject_id, predicate, value_json,
                    text, normalized_text, search_terms, observed_at, valid_from,
                    valid_to, confidence, importance, sensitivity, state,
                    supersedes, pinned, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    str(record.memory_id),
                    record.namespace,
                    record.kind,
                    record.subject_id,
                    record.predicate,
                    json.dumps(record.value, ensure_ascii=False),
                    record.text,
                    _normalize(record.text),
                    _search_terms(record.text),
                    record.observed_at.isoformat(),
                    record.valid_from.isoformat() if record.valid_from else None,
                    record.valid_to.isoformat() if record.valid_to else None,
                    record.confidence,
                    record.importance,
                    record.sensitivity.value,
                    record.state,
                    str(record.supersedes) if record.supersedes else None,
                    int(record.pinned),
                    record.created_at.isoformat(),
                    record.updated_at.isoformat(),
                ),
            )
            for source in sources:
                await connection.execute(
                    """
                    INSERT INTO memory_sources(
                        source_id, memory_id, source_event_id, session_id,
                        turn_id, source_kind, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        str(source.source_id),
                        str(source.memory_id),
                        str(source.source_event_id),
                        str(source.session_id),
                        str(source.turn_id) if source.turn_id else None,
                        source.source_kind,
                        source.created_at.isoformat(),
                    ),
                )

    async def tombstone(self, memory_id: UUID, changed_at: datetime) -> bool:
        async with self._database.transaction() as connection:
            cursor = await connection.execute(
                """
                UPDATE memory_records
                SET state = 'tombstoned', updated_at = ?, tombstoned_at = ?
                WHERE memory_id = ? AND state = 'active'
                """,
                (changed_at.isoformat(), changed_at.isoformat(), str(memory_id)),
            )
            changed = cursor.rowcount > 0
            await cursor.close()
        return changed

    async def set_pinned(
        self, memory_id: UUID, pinned: bool, changed_at: datetime
    ) -> MemoryRecord | None:
        async with self._database.transaction() as connection:
            await connection.execute(
                """
                UPDATE memory_records SET pinned = ?, updated_at = ?
                WHERE memory_id = ? AND state = 'active'
                """,
                (int(pinned), changed_at.isoformat(), str(memory_id)),
            )
        return await self.get(memory_id)

    async def save_proposal(self, proposal: MemoryProposal) -> None:
        await self._execute_write(
            """
            INSERT INTO memory_proposals(
                proposal_id, operation, candidate_json, target_memory_id,
                evidence_event_ids_json, confidence, rationale, status,
                created_at, decided_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                str(proposal.proposal_id),
                proposal.operation,
                proposal.candidate.model_dump_json() if proposal.candidate else None,
                str(proposal.target_memory_id) if proposal.target_memory_id else None,
                json.dumps([str(item) for item in proposal.evidence_event_ids]),
                proposal.confidence,
                proposal.rationale,
                proposal.status,
                proposal.created_at.isoformat(),
                proposal.decided_at.isoformat() if proposal.decided_at else None,
            ),
        )

    async def get_proposal(self, proposal_id: UUID) -> MemoryProposal | None:
        row = await self._database.fetchone(
            "SELECT * FROM memory_proposals WHERE proposal_id = ?", (str(proposal_id),)
        )
        return _proposal_from_row(dict(row)) if row is not None else None

    async def decide_proposal(self, proposal_id: UUID, status: str, decided_at: datetime) -> None:
        await self._execute_write(
            """
            UPDATE memory_proposals SET status = ?, decided_at = ?
            WHERE proposal_id = ? AND status = 'pending'
            """,
            (status, decided_at.isoformat(), str(proposal_id)),
        )

    async def list_proposals(
        self, *, status: str | None = None, limit: int = 100
    ) -> list[MemoryProposal]:
        where = " WHERE status = ?" if status else ""
        parameters: tuple[object, ...] = (
            (status, min(max(limit, 1), 200)) if status else (min(max(limit, 1), 200),)
        )
        rows = await self._database.fetchall(
            f"SELECT * FROM memory_proposals{where} ORDER BY created_at DESC LIMIT ?",
            parameters,
        )
        return [_proposal_from_row(dict(row)) for row in rows]

    async def list_records(
        self,
        *,
        include_tombstoned: bool = False,
        namespace: str | None = None,
        kind: str | None = None,
        sensitivity: str | None = None,
        limit: int = 200,
    ) -> list[MemoryRecord]:
        clauses = [] if include_tombstoned else ["record.state = 'active'"]
        parameters: list[object] = []
        for column, value in (
            ("record.namespace", namespace),
            ("record.kind", kind),
            ("record.sensitivity", sensitivity),
        ):
            if value is not None:
                clauses.append(f"{column} = ?")
                parameters.append(value)
        where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
        parameters.append(min(max(limit, 1), 500))
        rows = await self._database.fetchall(
            _RECORD_SELECT + where + " ORDER BY record.pinned DESC, record.created_at DESC LIMIT ?",
            tuple(parameters),
        )
        return [_record_from_row(dict(row)) for row in rows]

    async def list_sources(self, memory_id: UUID) -> list[MemorySource]:
        rows = await self._database.fetchall(
            "SELECT * FROM memory_sources WHERE memory_id = ? ORDER BY created_at",
            (str(memory_id),),
        )
        return [_source_from_row(dict(row)) for row in rows]

    async def search_fts(
        self, query: str, namespaces: Sequence[str], limit: int
    ) -> list[MemorySearchHit]:
        if not namespaces:
            return []
        terms = _query_terms(query)
        placeholders = ", ".join("?" for _ in namespaces)
        by_id: dict[UUID, MemorySearchHit] = {}
        if terms:
            match = " OR ".join(f'"{term.replace(chr(34), chr(34) * 2)}"' for term in terms)
            rows = await self._database.fetchall(
                """
                SELECT memory_id, bm25(memory_records_fts) AS rank
                FROM memory_records_fts
                WHERE memory_records_fts MATCH ?
                ORDER BY rank LIMIT ?
                """,
                (match, min(max(limit * 3, 1), 100)),
            )
            ids = [UUID(str(row["memory_id"])) for row in rows]
            records = await self.get_many(ids)
            for index, record in enumerate(records):
                if record.namespace in namespaces and record.state == "active":
                    by_id[record.memory_id] = MemorySearchHit(record, max(0.2, 1.0 - index * 0.08))

        normalized = _normalize(query)
        like_rows = await self._database.fetchall(
            _RECORD_SELECT
            + f" WHERE record.state = 'active' AND record.namespace IN ({placeholders}) "
            "AND (record.normalized_text LIKE ? OR ? LIKE '%' || record.normalized_text || '%') "
            "ORDER BY record.pinned DESC, record.importance DESC LIMIT ?",
            (
                *namespaces,
                f"%{normalized}%",
                normalized,
                min(max(limit * 2, 1), 100),
            ),
        )
        for row in like_rows:
            record = _record_from_row(dict(row))
            by_id.setdefault(record.memory_id, MemorySearchHit(record, 0.9))
        return sorted(by_id.values(), key=lambda item: item.lexical_score, reverse=True)[:limit]

    async def list_pinned(self, namespaces: Sequence[str], limit: int) -> list[MemoryRecord]:
        return await self._list_active_namespaces(namespaces, limit, pinned=True)

    async def list_recent(self, namespaces: Sequence[str], limit: int) -> list[MemoryRecord]:
        return await self._list_active_namespaces(namespaces, limit, pinned=None)

    async def clear_all(self) -> int:
        async with self._database.transaction() as connection:
            cursor = await connection.execute("DELETE FROM memory_records")
            removed = max(cursor.rowcount, 0)
            await cursor.close()
            await connection.execute("DELETE FROM memory_proposals")
            await connection.execute("DELETE FROM memory_items")
        return removed

    async def _list_active_namespaces(
        self, namespaces: Sequence[str], limit: int, *, pinned: bool | None
    ) -> list[MemoryRecord]:
        if not namespaces:
            return []
        placeholders = ", ".join("?" for _ in namespaces)
        pinned_clause = " AND record.pinned = 1" if pinned else ""
        rows = await self._database.fetchall(
            _RECORD_SELECT
            + f" WHERE record.state = 'active' AND record.namespace IN ({placeholders})"
            + pinned_clause
            + " ORDER BY record.pinned DESC, record.importance DESC, "
            "record.created_at DESC LIMIT ?",
            (*namespaces, min(max(limit, 1), 100)),
        )
        return [_record_from_row(dict(row)) for row in rows]

    async def _execute_write(self, query: str, parameters: Sequence[object]) -> None:
        async with self._database.transaction() as connection:
            await connection.execute(query, parameters)


def _record_from_row(row: dict[str, object]) -> MemoryRecord:
    source_ids = cast(list[str], json.loads(str(row["source_event_ids_json"])))
    return MemoryRecord.model_validate(
        {
            "memory_id": row["memory_id"],
            "namespace": row["namespace"],
            "kind": row["kind"],
            "subject_id": row.get("subject_id"),
            "predicate": row.get("predicate"),
            "value": json.loads(str(row["value_json"])) if row.get("value_json") else None,
            "text": row["text"],
            "source_event_ids": source_ids,
            "observed_at": row["observed_at"],
            "valid_from": row.get("valid_from"),
            "valid_to": row.get("valid_to"),
            "confidence": row["confidence"],
            "importance": row["importance"],
            "sensitivity": row["sensitivity"],
            "state": row["state"],
            "supersedes": row.get("supersedes"),
            "pinned": bool(row["pinned"]),
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }
    )


def _proposal_from_row(row: dict[str, object]) -> MemoryProposal:
    candidate = json.loads(str(row["candidate_json"])) if row.get("candidate_json") else None
    return MemoryProposal.model_validate(
        {
            "proposal_id": row["proposal_id"],
            "operation": row["operation"],
            "candidate": candidate,
            "target_memory_id": row.get("target_memory_id"),
            "evidence_event_ids": json.loads(str(row["evidence_event_ids_json"])),
            "confidence": row["confidence"],
            "rationale": row["rationale"],
            "status": row["status"],
            "created_at": row["created_at"],
            "decided_at": row.get("decided_at"),
        }
    )


def _source_from_row(row: dict[str, object]) -> MemorySource:
    return MemorySource.model_validate(row)


def _normalize(text: str) -> str:
    return " ".join(text.casefold().split())


def _query_terms(text: str) -> list[str]:
    normalized = _normalize(text)
    terms = list(_ASCII_WORD.findall(normalized))
    for run in _CJK_RUN.findall(normalized):
        if len(run) == 1:
            terms.append(run)
        else:
            terms.extend(run[index : index + 2] for index in range(len(run) - 1))
    return list(dict.fromkeys(terms))[:32]


def _search_terms(text: str) -> str:
    return " ".join(_query_terms(text))
