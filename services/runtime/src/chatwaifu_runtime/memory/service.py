"""Policy-governed structured memory projection and retrieval application service."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
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
from chatwaifu_runtime.memory.inference import LlmMemoryCandidateExtractor
from chatwaifu_runtime.memory.policy import MemoryPolicy, MemoryWriteDecision
from chatwaifu_runtime.memory.ports import (
    NullSemanticMemoryIndex,
    NullTemporalMemoryGraph,
    SemanticMemoryIndex,
    TemporalMemoryGraph,
)
from chatwaifu_runtime.memory.repository import MemoryEventEvidence, MemoryRepository
from chatwaifu_runtime.memory.retrieval import MemoryRetriever
from chatwaifu_runtime.providers.model_config import ModelConfigurationService

type MemoryItem = MemoryRecord
logger = logging.getLogger(__name__)
_PROJECTION_QUEUE_SIZE = 128


@dataclass(frozen=True, slots=True)
class UserTurnMemoryObservation:
    session_id: UUID
    turn_id: UUID
    source_event_id: UUID
    character_id: str
    text: str


@dataclass(frozen=True, slots=True)
class _QueuedProjection:
    observation: UserTurnMemoryObservation
    global_epoch: int


@dataclass(slots=True)
class _ActiveProjection:
    task: asyncio.Task[list[MemoryProposal]]


class MemoryService:
    def __init__(
        self,
        repository: MemoryRepository,
        publisher: EventPublisher,
        *,
        semantic_index: SemanticMemoryIndex | None = None,
        temporal_graph: TemporalMemoryGraph | None = None,
        models: ModelConfigurationService | None = None,
    ) -> None:
        self._repository = repository
        self._publisher = publisher
        self._extractor = DeterministicMemoryExtractor()
        self._policy = MemoryPolicy()
        self._semantic_index = semantic_index or NullSemanticMemoryIndex()
        self._temporal_graph = temporal_graph or NullTemporalMemoryGraph()
        self._inference = LlmMemoryCandidateExtractor(models) if models else None
        self._retriever = MemoryRetriever(
            repository,
            self._policy,
            self._semantic_index,
            self._temporal_graph,
        )
        self._projection_queue: asyncio.Queue[_QueuedProjection] = asyncio.Queue(
            maxsize=_PROJECTION_QUEUE_SIZE
        )
        self._projection_worker: asyncio.Task[None] | None = None
        self._active_projection: _ActiveProjection | None = None
        self._projection_epoch = 0
        self._projection_stopping = False

    async def start(self) -> None:
        if self._projection_worker is not None and not self._projection_worker.done():
            return
        self._projection_stopping = False
        self._projection_worker = asyncio.create_task(
            self._run_projection_worker(), name="memory-projection-worker"
        )

    async def stop(self) -> None:
        self._projection_stopping = True
        worker = self._projection_worker
        if worker is not None and not worker.done():
            worker.cancel("runtime_stopping")
            await asyncio.gather(worker, return_exceptions=True)
        self._projection_worker = None
        self._active_projection = None
        while True:
            try:
                self._projection_queue.get_nowait()
            except asyncio.QueueEmpty:
                break
            else:
                self._projection_queue.task_done()

    async def enqueue_user_turn(self, observation: UserTurnMemoryObservation) -> bool:
        worker = self._projection_worker
        if worker is None or worker.done():
            raise RuntimeError("memory projection worker is not running")
        queued = _QueuedProjection(
            observation=observation,
            global_epoch=self._projection_epoch,
        )
        try:
            self._projection_queue.put_nowait(queued)
        except asyncio.QueueFull:
            logger.error(
                "memory projection queue is full",
                extra={
                    "session_id": str(observation.session_id),
                    "turn_id": str(observation.turn_id),
                    "queue_size": self._projection_queue.qsize(),
                },
            )
            await self._emit(
                observation.session_id,
                observation.turn_id,
                "memory.projection_deferred",
                {
                    "reason": "queue_full",
                    "source_event_id": str(observation.source_event_id),
                },
                causation_id=observation.source_event_id,
            )
            return False
        return True

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
        deterministic = self._extractor.extract(
            content,
            namespace=namespaces[0],
            observed_at=evidence.occurred_at,
            explicit=explicit,
        )
        candidates = [deterministic] if deterministic is not None else []
        if self._inference is not None and not explicit:
            related = await self._repository.search_fts(content, namespaces, limit=12)
            try:
                candidates.extend(
                    await self._inference.extract(
                        content,
                        namespace=namespaces[0],
                        observed_at=evidence.occurred_at,
                        related=[item.record for item in related],
                    )
                )
            except Exception as error:
                logger.warning(
                    "memory extraction provider failed",
                    extra={"source_event_id": str(source_event_id), "error": type(error).__name__},
                )
        unique: dict[tuple[str, str | None, str], ExtractedMemoryCandidate] = {}
        for item in candidates:
            unique.setdefault(
                (item.draft.kind, item.draft.predicate, _normalize(item.draft.text)), item
            )
        if not unique:
            return []
        proposals = [
            await self._process_candidate(session_id, turn_id, source_event_id, item)
            for item in unique.values()
        ]
        await self._emit(
            session_id,
            turn_id,
            "memory.extraction_completed",
            {
                "candidate_count": len(unique),
                "proposal_ids": [str(item.proposal_id) for item in proposals],
                "model_assisted": self._inference is not None,
            },
            causation_id=source_event_id,
        )
        return proposals

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

    async def observe_assistant_spoken(
        self,
        session_id: UUID,
        turn_id: UUID,
        source_event_id: UUID,
        character_id: str,
        spoken_text: str,
    ) -> list[MemoryProposal]:
        if self._inference is None or not spoken_text.strip():
            return []
        evidence = await self._repository.event_evidence(source_event_id)
        if evidence is None or evidence.event_type != "assistant.spoken_text_committed":
            raise ValueError("shared memory requires spoken-text evidence")
        namespaces = _namespaces(character_id)
        related = await self._repository.search_fts(spoken_text, namespaces, limit=12)
        try:
            candidates = await self._inference.extract(
                f"The user actually heard the character say: {spoken_text}",
                namespace=namespaces[0],
                observed_at=evidence.occurred_at,
                related=[item.record for item in related],
            )
        except Exception as error:
            logger.warning(
                "shared memory extraction provider failed",
                extra={"source_event_id": str(source_event_id), "error": type(error).__name__},
            )
            return []
        durable = [
            item
            for item in candidates
            if item.draft.kind in {"episodic.shared_event", "relationship.signal"}
        ]
        return [
            await self._process_candidate(session_id, turn_id, source_event_id, item)
            for item in durable
        ]

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
        await self._invalidate_all_projections()
        records = await self._repository.list_records(include_tombstoned=True, limit=500)
        removed = await self._repository.clear_all()
        for record in records:
            await self._semantic_index.delete(record.memory_id)
            await self._temporal_graph.delete(record.memory_id)
        return removed

    async def _invalidate_all_projections(self) -> None:
        self._projection_epoch += 1
        active = self._active_projection
        if active is not None and not active.task.done():
            active.task.cancel("all_memory_reset")
            await asyncio.gather(active.task, return_exceptions=True)

    async def _run_projection_worker(self) -> None:
        while True:
            queued = await self._projection_queue.get()
            try:
                if self._projection_is_stale(queued):
                    continue
                observation = queued.observation
                task = asyncio.create_task(
                    self.observe_user_turn(
                        observation.session_id,
                        observation.turn_id,
                        observation.source_event_id,
                        observation.character_id,
                        observation.text,
                    ),
                    name=f"memory-projection-{observation.turn_id}",
                )
                self._active_projection = _ActiveProjection(task)
                try:
                    await task
                except asyncio.CancelledError:
                    if self._projection_stopping:
                        raise
                except Exception:
                    logger.exception(
                        "background memory projection failed",
                        extra={
                            "session_id": str(observation.session_id),
                            "turn_id": str(observation.turn_id),
                            "source_event_id": str(observation.source_event_id),
                        },
                    )
                finally:
                    if self._active_projection.task is task:
                        self._active_projection = None
            finally:
                self._projection_queue.task_done()

    def _projection_is_stale(self, queued: _QueuedProjection) -> bool:
        return queued.global_epoch != self._projection_epoch

    async def reindex_all(self) -> int:
        records = await self._repository.list_records(limit=500)
        rebuild = getattr(self._semantic_index, "rebuild", None)
        if rebuild is not None:
            return int(await rebuild(records))
        count = 0
        for record in records:
            try:
                await self._semantic_index.upsert(record)
            except Exception:
                continue
            count += 1
        return count

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
                source_kind=(
                    "assistant_spoken"
                    if item.event_type == "assistant.spoken_text_committed"
                    else "user_turn"
                    if item.event_type == "user.turn_committed"
                    else "memory_management"
                ),
                created_at=now,
            )
            for item in evidence
        ]
        await self._repository.create_record(
            record, sources, supersede_target=proposal.target_memory_id
        )
        try:
            await self._semantic_index.upsert(record)
        except Exception as error:
            logger.warning(
                "memory semantic indexing failed",
                extra={"memory_id": str(record.memory_id), "error": type(error).__name__},
            )
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
            "memory.extraction_completed",
            "memory.projection_deferred",
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
