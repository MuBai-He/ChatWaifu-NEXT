"""Policy-governed structured memory projection and retrieval application service."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Literal
from uuid import UUID, uuid4

from chatwaifu_protocol.base import PrivacyLevel
from chatwaifu_protocol.events import GenericCoreEvent
from chatwaifu_protocol.memory import (
    MemoryContextPacket,
    MemoryProposal,
    MemoryProposalStatus,
    MemoryRecord,
    MemorySource,
)

from chatwaifu_runtime.eventing.publisher import EventPublisher
from chatwaifu_runtime.memory.extractor import (
    DeterministicMemoryExtractor,
    ExplicitMemoryCommand,
    ExtractedMemoryCandidate,
)
from chatwaifu_runtime.memory.policy import MemoryPolicy, MemoryWriteDecision
from chatwaifu_runtime.memory.ports import (
    NullSemanticMemoryIndex,
    NullTemporalMemoryGraph,
    SemanticMemoryIndex,
    TemporalMemoryGraph,
)
from chatwaifu_runtime.memory.repository import MemoryEventEvidence, MemoryRepository
from chatwaifu_runtime.memory.retrieval import MemoryRetriever

type MemoryItem = MemoryRecord


class MemoryService:
    def __init__(
        self,
        repository: MemoryRepository,
        publisher: EventPublisher,
        *,
        semantic_index: SemanticMemoryIndex | None = None,
        temporal_graph: TemporalMemoryGraph | None = None,
    ) -> None:
        self._repository = repository
        self._publisher = publisher
        self._extractor = DeterministicMemoryExtractor()
        self._policy = MemoryPolicy()
        self._semantic_index = semantic_index or NullSemanticMemoryIndex()
        self._temporal_graph = temporal_graph or NullTemporalMemoryGraph()
        self._retriever = MemoryRetriever(
            repository,
            self._policy,
            self._semantic_index,
            self._temporal_graph,
        )

    def parse_explicit_command(self, text: str) -> ExplicitMemoryCommand | None:
        return self._extractor.parse_explicit_command(text)

    async def observe_user_turn(
        self,
        session_id: UUID,
        turn_id: UUID,
        source_event_id: UUID,
        character_id: str,
        text: str,
    ) -> list[MemoryProposal]:
        if not await self._repository.event_exists(source_event_id):
            raise ValueError("memory source event does not exist")
        command = self.parse_explicit_command(text)
        namespaces = _namespaces(character_id)
        if command is not None and command.operation == "forget":
            await self.forget_matching(
                session_id,
                turn_id,
                source_event_id,
                command.content,
                namespaces,
            )
            return []
        content = command.content if command is not None else text
        explicit = command is not None and command.operation == "remember"
        evidence = await self._repository.event_evidence(source_event_id)
        if evidence is None:
            raise ValueError("memory source event disappeared")
        extracted = self._extractor.extract(
            content,
            namespace=namespaces[0],
            observed_at=evidence.occurred_at,
            explicit=explicit,
        )
        if extracted is None:
            return []
        proposal = await self._process_candidate(
            session_id,
            turn_id,
            source_event_id,
            extracted,
        )
        return [proposal]

    async def decide_proposal(
        self, session_id: UUID, proposal_id: UUID, decision: Literal["accept", "reject"]
    ) -> MemoryProposal:
        proposal = await self._repository.get_proposal(proposal_id)
        if proposal is None:
            raise KeyError(f"unknown memory proposal {proposal_id}")
        if proposal.status != "pending":
            raise RuntimeError(f"memory proposal is already {proposal.status}")
        now = datetime.now(UTC)
        if decision == "reject":
            await self._repository.decide_proposal(proposal_id, "rejected", now)
            decided = proposal.model_copy(update={"status": "rejected", "decided_at": now})
            await self._emit(
                session_id,
                None,
                "memory.proposed",
                {"proposal_id": str(proposal_id), "decision": "rejected"},
            )
            return decided
        if proposal.candidate is None:
            raise RuntimeError("accepted memory proposal has no candidate")
        extracted = ExtractedMemoryCandidate(
            draft=proposal.candidate,
            explicit=True,
            rationale=proposal.rationale,
        )
        if self._policy.decide_write(extracted, confirmed=True) is not MemoryWriteDecision.COMMIT:
            raise RuntimeError("memory proposal is not permitted by policy")
        await self._commit_proposal(session_id, proposal)
        await self._repository.decide_proposal(proposal_id, "accepted", now)
        return proposal.model_copy(update={"status": "accepted", "decided_at": now})

    async def correct(self, session_id: UUID, memory_id: UUID, text: str) -> MemoryRecord:
        current = await self._repository.get(memory_id)
        if current is None or current.state != "active":
            raise KeyError(f"active memory not found: {memory_id}")
        management_event = await self._emit(
            session_id,
            None,
            "memory.proposed",
            {"operation": "correct", "target_memory_id": str(memory_id)},
        )
        extracted = self._extractor.extract(
            text,
            namespace=current.namespace,
            observed_at=management_event.occurred_at,
            explicit=True,
        )
        if extracted is None:
            raise ValueError("corrected memory text must not be blank")
        proposal = await self._process_candidate(
            session_id,
            None,
            management_event.event_id,
            extracted,
            target_override=memory_id,
        )
        if proposal.status != "accepted":
            raise RuntimeError("sensitive corrections require proposal confirmation")
        records = await self._repository.list_records(
            include_tombstoned=True, namespace=current.namespace
        )
        corrected = next(
            (
                record
                for record in records
                if record.supersedes == memory_id and record.state == "active"
            ),
            None,
        )
        if corrected is None:
            raise RuntimeError("corrected memory record was not created")
        if current.pinned and not corrected.pinned:
            pinned = await self._repository.set_pinned(corrected.memory_id, True, datetime.now(UTC))
            if pinned is None:
                raise RuntimeError("corrected memory record could not preserve pinned state")
            await self._semantic_index.upsert(pinned)
            await self._temporal_graph.upsert(pinned)
            corrected = pinned
        return corrected

    async def set_pinned(self, session_id: UUID, memory_id: UUID, pinned: bool) -> MemoryRecord:
        record = await self._repository.set_pinned(memory_id, pinned, datetime.now(UTC))
        if record is None:
            raise KeyError(f"active memory not found: {memory_id}")
        await self._emit(
            session_id,
            None,
            "memory.committed",
            {"memory_id": str(memory_id), "pinned": pinned, "policy": "user_management"},
        )
        return record

    async def forget(
        self,
        session_id: UUID,
        memory_id: UUID,
        *,
        turn_id: UUID | None = None,
        causation_id: UUID | None = None,
    ) -> bool:
        changed = await self._repository.tombstone(memory_id, datetime.now(UTC))
        if changed:
            await self._semantic_index.delete(memory_id)
            await self._temporal_graph.delete(memory_id)
            await self._emit(
                session_id,
                turn_id,
                "memory.tombstoned",
                {"memory_id": str(memory_id), "policy": "explicit"},
                causation_id=causation_id,
            )
        return changed

    async def forget_matching(
        self,
        session_id: UUID,
        turn_id: UUID,
        source_event_id: UUID,
        query: str,
        namespaces: list[str],
    ) -> int:
        hits = await self._repository.search_fts(query, namespaces, limit=20)
        count = 0
        for hit in hits:
            if await self.forget(
                session_id,
                hit.record.memory_id,
                turn_id=turn_id,
                causation_id=source_event_id,
            ):
                count += 1
        return count

    async def retrieve_context(
        self,
        session_id: UUID,
        turn_id: UUID,
        character_id: str,
        query: str,
        *,
        token_budget: int = 700,
    ) -> MemoryContextPacket:
        packet = await self._retriever.retrieve_context(
            query, _namespaces(character_id), token_budget=token_budget
        )
        excerpts = (
            packet.pinned_facts
            + packet.recent_episodes
            + packet.relevant_memories
            + packet.open_commitments
            + packet.relationship_context
        )
        if excerpts:
            await self._emit(
                session_id,
                turn_id,
                "memory.recalled",
                {
                    "memory_ids": [str(item.memory_id) for item in excerpts],
                    "scores": [round(item.relevance, 4) for item in excerpts],
                    "count": len(excerpts),
                    "token_budget_used": packet.token_budget_used,
                },
            )
        return packet

    async def list(
        self,
        *,
        include_tombstoned: bool = False,
        namespace: str | None = None,
        kind: str | None = None,
        sensitivity: str | None = None,
    ) -> list[MemoryRecord]:
        return await self._repository.list_records(
            include_tombstoned=include_tombstoned,
            namespace=namespace,
            kind=kind,
            sensitivity=sensitivity,
        )

    async def list_proposals(self, *, status: str | None = None) -> list[MemoryProposal]:
        return await self._repository.list_proposals(status=status)

    async def list_sources(self, memory_id: UUID) -> list[MemorySource]:
        return await self._repository.list_sources(memory_id)

    async def clear_all(self) -> int:
        records = await self._repository.list_records(include_tombstoned=True, limit=500)
        removed = await self._repository.clear_all()
        for record in records:
            await self._semantic_index.delete(record.memory_id)
            await self._temporal_graph.delete(record.memory_id)
        return removed

    async def _process_candidate(
        self,
        session_id: UUID,
        turn_id: UUID | None,
        source_event_id: UUID,
        extracted: ExtractedMemoryCandidate,
        *,
        target_override: UUID | None = None,
    ) -> MemoryProposal:
        now = datetime.now(UTC)
        draft = extracted.draft
        exact = await self._repository.find_exact(draft.namespace, _normalize(draft.text))
        if exact is not None and target_override is None:
            proposal = MemoryProposal(
                proposal_id=uuid4(),
                operation="ignore",
                candidate=draft,
                target_memory_id=exact.memory_id,
                evidence_event_ids=[source_event_id],
                confidence=extracted.draft.confidence,
                rationale="duplicate active memory",
                status="ignored",
                created_at=now,
                decided_at=now,
            )
            await self._repository.save_proposal(proposal)
            return proposal

        target = target_override
        if target is None and draft.subject_id and draft.predicate:
            identities = await self._repository.find_identity(
                draft.namespace, draft.subject_id, draft.predicate
            )
            target = identities[0].memory_id if identities else None
        operation: Literal["add", "supersede"] = "supersede" if target else "add"
        decision = self._policy.decide_write(extracted)
        status_by_decision: dict[MemoryWriteDecision, MemoryProposalStatus] = {
            MemoryWriteDecision.COMMIT: "accepted",
            MemoryWriteDecision.REVIEW: "pending",
            MemoryWriteDecision.REJECT: "ignored",
        }
        status = status_by_decision[decision]
        proposal = MemoryProposal(
            proposal_id=uuid4(),
            operation=operation,
            candidate=draft,
            target_memory_id=target,
            evidence_event_ids=[source_event_id],
            confidence=draft.confidence,
            rationale=extracted.rationale,
            status=status,
            created_at=now,
            decided_at=now if status != "pending" else None,
        )
        await self._repository.save_proposal(proposal)
        await self._emit(
            session_id,
            turn_id,
            "memory.proposed",
            {
                "proposal_id": str(proposal.proposal_id),
                "operation": proposal.operation,
                "status": proposal.status,
                "kind": draft.kind,
                "sensitivity": draft.sensitivity.value,
            },
            causation_id=source_event_id,
        )
        if decision is MemoryWriteDecision.COMMIT:
            await self._commit_proposal(session_id, proposal)
        return proposal

    async def _commit_proposal(self, session_id: UUID, proposal: MemoryProposal) -> MemoryRecord:
        draft = proposal.candidate
        if draft is None:
            raise ValueError("committed proposal requires a candidate")
        now = datetime.now(UTC)
        evidence: list[MemoryEventEvidence] = []
        for event_id in proposal.evidence_event_ids:
            item = await self._repository.event_evidence(event_id)
            if item is None:
                raise ValueError(f"memory evidence event does not exist: {event_id}")
            evidence.append(item)
        record = MemoryRecord(
            **draft.model_dump(),
            memory_id=uuid4(),
            source_event_ids=[item.event_id for item in evidence],
            valid_from=draft.observed_at,
            state="active",
            supersedes=proposal.target_memory_id,
            pinned=draft.kind == "core",
            created_at=now,
            updated_at=now,
        )
        sources = [
            MemorySource(
                source_id=uuid4(),
                memory_id=record.memory_id,
                source_event_id=item.event_id,
                session_id=item.session_id,
                turn_id=item.turn_id,
                source_kind="user_turn" if item.turn_id else "memory_management",
                created_at=now,
            )
            for item in evidence
        ]
        await self._repository.create_record(
            record, sources, supersede_target=proposal.target_memory_id
        )
        await self._semantic_index.upsert(record)
        await self._temporal_graph.upsert(record)
        if proposal.target_memory_id:
            await self._semantic_index.delete(proposal.target_memory_id)
            await self._temporal_graph.delete(proposal.target_memory_id)
            await self._emit(
                session_id,
                evidence[0].turn_id,
                "memory.superseded",
                {
                    "memory_id": str(proposal.target_memory_id),
                    "superseded_by": str(record.memory_id),
                },
                causation_id=evidence[0].event_id,
            )
        await self._emit(
            session_id,
            evidence[0].turn_id,
            "memory.committed",
            {
                "memory_id": str(record.memory_id),
                "kind": record.kind,
                "policy": "explicit" if proposal.status == "accepted" else "reviewed",
            },
            causation_id=evidence[0].event_id,
        )
        return record

    async def _emit(
        self,
        session_id: UUID,
        turn_id: UUID | None,
        event_type: Literal[
            "memory.proposed",
            "memory.committed",
            "memory.superseded",
            "memory.tombstoned",
            "memory.recalled",
        ],
        payload: dict[str, object],
        *,
        causation_id: UUID | None = None,
    ) -> GenericCoreEvent:
        return await self._publisher.emit(
            GenericCoreEvent.model_validate(
                {
                    "event_id": uuid4(),
                    "event_type": event_type,
                    "session_id": session_id,
                    "turn_id": turn_id,
                    "occurred_at": datetime.now(UTC),
                    "source": "runtime.memory",
                    "causation_id": causation_id,
                    "privacy": PrivacyLevel.PRIVATE,
                    "payload": payload,
                }
            )
        )


def _normalize(content: str) -> str:
    return " ".join(content.casefold().split())


def _namespaces(character_id: str) -> list[str]:
    return [f"character/{character_id}/user/local", "user/local/global"]
