"""Policy-filtered FTS retrieval with reserved semantic and temporal contributors."""

from dataclasses import dataclass, field
from datetime import UTC, datetime
from uuid import UUID

from chatwaifu_protocol.memory import (
    MemoryChannelAttribution,
    MemoryContextPacket,
    MemoryExcerpt,
    MemoryRecord,
    MemoryRetrievalSource,
)

from chatwaifu_runtime.memory.policy import MemoryPolicy
from chatwaifu_runtime.memory.ports import SemanticMemoryIndex, TemporalMemoryGraph
from chatwaifu_runtime.memory.repository import MemoryRepository


@dataclass(slots=True)
class _Candidate:
    record: MemoryRecord
    sources: set[MemoryRetrievalSource] = field(
        default_factory=lambda: set[MemoryRetrievalSource]()
    )
    lexical: float = 0
    semantic: float = 0
    temporal: float = 0
    pinned: bool = False

    @property
    def score(self) -> float:
        if self.pinned:
            return 1.0
        return min(
            1.0,
            0.55 * self.lexical
            + 0.2 * self.semantic
            + 0.1 * self.temporal
            + 0.1 * self.record.importance
            + (0.05 if "recent" in self.sources else 0),
        )


class MemoryRetriever:
    def __init__(
        self,
        repository: MemoryRepository,
        policy: MemoryPolicy,
        semantic_index: SemanticMemoryIndex,
        temporal_graph: TemporalMemoryGraph,
    ) -> None:
        self._repository = repository
        self._policy = policy
        self._semantic_index = semantic_index
        self._temporal_graph = temporal_graph

    async def retrieve_context(
        self,
        query: str,
        namespaces: list[str],
        *,
        token_budget: int = 700,
        limit: int = 12,
    ) -> MemoryContextPacket:
        candidates: dict[UUID, _Candidate] = {}
        for record in await self._repository.list_pinned(namespaces, limit=8):
            candidates[record.memory_id] = _Candidate(record, {"pinned"}, pinned=True)
        for hit in await self._repository.search_fts(query, namespaces, limit=limit):
            item = candidates.setdefault(hit.record.memory_id, _Candidate(hit.record))
            item.sources.add("fts")
            item.lexical = max(item.lexical, hit.lexical_score)

        try:
            semantic = await self._semantic_index.search(query, namespaces, limit)
        except Exception:
            semantic = []
        try:
            temporal = await self._temporal_graph.search(
                query, namespaces, datetime.now(UTC), limit
            )
        except Exception:
            temporal = []
        missing_ids = [
            reference.memory_id
            for reference in (*semantic, *temporal)
            if reference.memory_id not in candidates
        ]
        for record in await self._repository.get_many(missing_ids):
            candidates[record.memory_id] = _Candidate(record)
        for reference in semantic:
            if item := candidates.get(reference.memory_id):
                item.sources.add("semantic")
                item.semantic = max(item.semantic, reference.score)
        for reference in temporal:
            if item := candidates.get(reference.memory_id):
                item.sources.add("temporal")
                item.temporal = max(item.temporal, reference.score)

        if len(candidates) < 3 and _requests_memory_recall(query):
            for record in await self._repository.list_recent(namespaces, limit=3):
                item = candidates.setdefault(record.memory_id, _Candidate(record))
                item.sources.add("recent")

        ranked = sorted(candidates.values(), key=lambda item: item.score, reverse=True)
        selected: list[_Candidate] = []
        budget_used = 0
        for item in ranked:
            record = item.record
            if record.state != "active" or not self._policy.allow_retrieval(record.sensitivity):
                continue
            estimated_tokens = max(1, len(record.text))
            if budget_used + estimated_tokens > token_budget:
                continue
            budget_used += estimated_tokens
            selected.append(item)
            if len(selected) >= limit:
                break

        sources_by_memory = await self._repository.list_sources_many(
            [item.record.memory_id for item in selected]
        )
        excerpts: list[tuple[MemoryRecord, MemoryExcerpt]] = []
        for item in selected:
            record = item.record
            attributions: list[MemoryChannelAttribution] = []
            seen_attributions: set[str] = set()
            for source in sources_by_memory.get(record.memory_id, []):
                attribution = source.channel_attribution
                if attribution is None:
                    continue
                fingerprint = attribution.model_dump_json()
                if fingerprint in seen_attributions:
                    continue
                seen_attributions.add(fingerprint)
                attributions.append(attribution)
            excerpts.append(
                (
                    record,
                    MemoryExcerpt(
                        memory_id=record.memory_id,
                        text=record.text,
                        source_event_ids=record.source_event_ids,
                        relevance=item.score,
                        lexical_relevance=item.lexical,
                        semantic_relevance=item.semantic,
                        temporal_relevance=item.temporal,
                        retrieval_sources=sorted(item.sources),
                        channel_attributions=attributions,
                    ),
                )
            )

        return MemoryContextPacket(
            pinned_facts=[excerpt for record, excerpt in excerpts if record.pinned],
            recent_episodes=[
                excerpt for record, excerpt in excerpts if record.kind == "episodic.shared_event"
            ],
            relevant_memories=[
                excerpt
                for record, excerpt in excerpts
                if not record.pinned
                and record.kind
                not in {
                    "episodic.shared_event",
                    "prospective.commitment",
                    "relationship.signal",
                }
            ],
            open_commitments=[
                excerpt for record, excerpt in excerpts if record.kind == "prospective.commitment"
            ],
            relationship_context=[
                excerpt for record, excerpt in excerpts if record.kind == "relationship.signal"
            ],
            provenance_ids=list(
                dict.fromkeys(
                    source_id
                    for _record, excerpt in excerpts
                    for source_id in excerpt.source_event_ids
                )
            ),
            token_budget_used=budget_used,
        )


def _requests_memory_recall(query: str) -> bool:
    normalized = query.casefold()
    return any(
        marker in normalized
        for marker in ("记得", "记住的", "我的喜好", "我的信息", "remember", "recall")
    )
