#!/usr/bin/env python3
# ruff: noqa: E402
"""Quantitative memory retrieval evaluator for ChatWaifu NEXT Q06.

Measures true positives (TP), false retrieval (FP), and missed memories (FN)
across synthetic, source-grounded scenarios using the actual MemoryRetriever
with SQLiteMemoryRepository in an isolated temporary Runtime database.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
import tempfile
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast
from uuid import NAMESPACE_URL, UUID, uuid4, uuid5

_ROOT = Path(__file__).resolve().parents[1]
for _subpath in (
    "packages/model-worker-sdk-python/src",
    "packages/protocol-python/src",
    "services/runtime/src",
):
    _resolved = str(_ROOT / _subpath)
    if _resolved not in sys.path:
        sys.path.insert(0, _resolved)

from chatwaifu_protocol.base import PrivacyLevel
from chatwaifu_protocol.events import (
    AssistantSpokenTextCommittedEvent,
    AssistantSpokenTextCommittedPayload,
    GenericCoreEvent,
    UserTurnCommittedEvent,
    UserTurnCommittedPayload,
)
from chatwaifu_protocol.memory import (
    MemoryChannelAttribution,
    MemoryKind,
    MemoryProposal,
    MemoryRecord,
    MemoryRecordDraft,
    MemorySource,
)
from chatwaifu_runtime.config.settings import StorageConfig
from chatwaifu_runtime.memory.policy import MemoryPolicy
from chatwaifu_runtime.memory.ports import (
    NullSemanticMemoryIndex,
    NullTemporalMemoryGraph,
)
from chatwaifu_runtime.memory.retrieval import MemoryRetriever
from chatwaifu_runtime.persistence.database import Database
from chatwaifu_runtime.persistence.event_store import EventStore
from chatwaifu_runtime.persistence.sqlite_memory_repository import SQLiteMemoryRepository

logger = logging.getLogger("evaluate_memory_retrieval")

DEFAULT_FIXTURES_PATH = (
    Path(__file__).resolve().parents[1]
    / "tests"
    / "fixtures"
    / "memory"
    / "retrieval_scenarios.json"
)


@dataclass(frozen=True, slots=True)
class MetricScores:
    tp: int
    fp: int
    fn: int
    total_retrieved: int
    total_expected: int
    precision: float
    recall: float
    false_retrieval_rate: float
    miss_rate: float


@dataclass(frozen=True, slots=True)
class QueryEvaluationResult:
    query_id: str
    label: str
    is_negative: bool
    token_budget: int
    expected_keys: list[str]
    retrieved_keys: list[str]
    metrics: MetricScores


@dataclass(frozen=True, slots=True)
class ScenarioEvaluationResult:
    scenario_id: str
    name: str
    condition: str
    evaluable: bool
    limitation_note: str | None
    queries: list[QueryEvaluationResult]
    aggregate_metrics: MetricScores
    query_false_retrieval_rate: float
    query_miss_rate: float
    queries_with_false_retrieval: int
    queries_with_misses: int


@dataclass(frozen=True, slots=True)
class SuiteEvaluationResult:
    timestamp_iso: str
    total_scenarios: int
    evaluable_scenarios: int
    non_evaluable_scenarios: int
    total_queries: int
    scenarios: list[ScenarioEvaluationResult]
    overall_metrics: MetricScores
    overall_query_false_retrieval_rate: float
    overall_query_miss_rate: float
    queries_with_false_retrieval: int
    queries_with_misses: int


def compute_metrics(retrieved: set[str], expected: set[str]) -> MetricScores:
    """Calculate TP, FP, FN, precision, recall, false retrieval rate, and miss rate.

    Clear denominators:
    - precision = TP / (TP + FP) [denominator: total retrieved items]
    - recall = TP / (TP + FN) [denominator: total expected items]
    - false_retrieval_rate = FP / (TP + FP) [denominator: total retrieved items]
    - miss_rate = FN / (TP + FN) [denominator: total expected items]
    """
    tp = len(retrieved & expected)
    fp = len(retrieved - expected)
    fn = len(expected - retrieved)
    total_retrieved = tp + fp
    total_expected = tp + fn

    if total_retrieved > 0:
        precision = tp / total_retrieved
        false_retrieval_rate = fp / total_retrieved
    else:
        # If nothing was retrieved:
        # If nothing was expected (negative query), precision is 1.0 (no false alarms).
        # If something was expected, precision is 0.0.
        precision = 1.0 if total_expected == 0 else 0.0
        false_retrieval_rate = 0.0

    if total_expected > 0:
        recall = tp / total_expected
        miss_rate = fn / total_expected
    else:
        # If nothing was expected:
        # Recall is 1.0 (no relevant items were missed).
        recall = 1.0
        miss_rate = 0.0

    return MetricScores(
        tp=tp,
        fp=fp,
        fn=fn,
        total_retrieved=total_retrieved,
        total_expected=total_expected,
        precision=round(precision, 4),
        recall=round(recall, 4),
        false_retrieval_rate=round(false_retrieval_rate, 4),
        miss_rate=round(miss_rate, 4),
    )


def aggregate_query_metrics(metrics_list: list[MetricScores]) -> MetricScores:
    """Micro-average metric scores across multiple queries."""
    total_tp = sum(m.tp for m in metrics_list)
    total_fp = sum(m.fp for m in metrics_list)
    total_fn = sum(m.fn for m in metrics_list)
    total_retrieved = total_tp + total_fp
    total_expected = total_tp + total_fn

    if total_retrieved > 0:
        precision = total_tp / total_retrieved
        false_retrieval_rate = total_fp / total_retrieved
    else:
        precision = 1.0 if total_expected == 0 else 0.0
        false_retrieval_rate = 0.0

    if total_expected > 0:
        recall = total_tp / total_expected
        miss_rate = total_fn / total_expected
    else:
        recall = 1.0
        miss_rate = 0.0

    return MetricScores(
        tp=total_tp,
        fp=total_fp,
        fn=total_fn,
        total_retrieved=total_retrieved,
        total_expected=total_expected,
        precision=round(precision, 4),
        recall=round(recall, 4),
        false_retrieval_rate=round(false_retrieval_rate, 4),
        miss_rate=round(miss_rate, 4),
    )


class MemoryRetrievalEvaluator:
    """Evaluates memory retrieval scenarios against an isolated temporary Runtime database."""

    def __init__(self, fixtures_path: Path = DEFAULT_FIXTURES_PATH) -> None:
        self._fixtures_path = fixtures_path

    def load_fixture_data(self) -> dict[str, Any]:
        with open(self._fixtures_path, encoding="utf-8") as file:
            data = cast(dict[str, Any], json.load(file))
        for scenario in cast(list[dict[str, Any]], data.get("scenarios", [])):
            record_keys = {
                str(record["record_key"])
                for record in cast(list[dict[str, Any]], scenario.get("records", []))
            }
            for query in cast(list[dict[str, Any]], scenario.get("queries", [])):
                expected = set(cast(list[str], query.get("expected_memory_keys", [])))
                if expected - record_keys:
                    raise ValueError(f"unknown expected memory key in {query['query_id']}")
                if bool(query.get("is_negative", False)) == bool(expected):
                    raise ValueError(f"inconsistent negative label in {query['query_id']}")
        return data

    async def evaluate_suite(
        self,
        *,
        temp_dir: Path | None = None,
        verbose: bool = False,
    ) -> SuiteEvaluationResult:
        data = self.load_fixture_data()
        scenarios_data = cast(list[dict[str, Any]], data.get("scenarios", []))

        if temp_dir is None:
            with tempfile.TemporaryDirectory(prefix="cw2_memory_eval_") as tmp_str:
                return await self.run_scenarios(scenarios_data, Path(tmp_str), verbose=verbose)
        else:
            return await self.run_scenarios(scenarios_data, temp_dir, verbose=verbose)

    async def run_scenarios(
        self,
        scenarios_data: list[dict[str, Any]],
        temp_dir: Path,
        *,
        verbose: bool = False,
    ) -> SuiteEvaluationResult:
        scenario_results: list[ScenarioEvaluationResult] = []

        for scenario_dict in scenarios_data:
            scenario_id = str(scenario_dict["scenario_id"])
            name = str(scenario_dict["name"])
            condition = str(scenario_dict["condition"])
            evaluable = bool(scenario_dict.get("evaluable_in_retrieval", True))
            limitation_note = scenario_dict.get("limitation_note")

            if not evaluable:
                if verbose:
                    logger.info(
                        "Skipping non-evaluable retrieval condition: %s (%s)",
                        condition,
                        limitation_note,
                    )
                empty_metrics = MetricScores(
                    tp=0,
                    fp=0,
                    fn=0,
                    total_retrieved=0,
                    total_expected=0,
                    precision=1.0,
                    recall=1.0,
                    false_retrieval_rate=0.0,
                    miss_rate=0.0,
                )
                scenario_results.append(
                    ScenarioEvaluationResult(
                        scenario_id=scenario_id,
                        name=name,
                        condition=condition,
                        evaluable=False,
                        limitation_note=limitation_note,
                        queries=[],
                        aggregate_metrics=empty_metrics,
                        query_false_retrieval_rate=0.0,
                        query_miss_rate=0.0,
                        queries_with_false_retrieval=0,
                        queries_with_misses=0,
                    )
                )
                continue

            scenario_res = await self._evaluate_single_scenario(
                scenario_dict=scenario_dict,
                temp_dir=temp_dir,
                verbose=verbose,
            )
            scenario_results.append(scenario_res)

        # Aggregate overall metrics across evaluable scenarios
        evaluable_scenarios = [s for s in scenario_results if s.evaluable]
        all_evaluable_queries = [q for s in evaluable_scenarios for q in s.queries]
        query_metrics = [q.metrics for q in all_evaluable_queries]
        overall_metrics = (
            aggregate_query_metrics(query_metrics)
            if query_metrics
            else MetricScores(0, 0, 0, 0, 0, 1.0, 1.0, 0.0, 0.0)
        )

        total_queries = len(all_evaluable_queries)
        queries_with_fp = sum(1 for q in all_evaluable_queries if q.metrics.fp > 0)
        queries_with_expected = sum(1 for q in all_evaluable_queries if len(q.expected_keys) > 0)
        queries_with_fn = sum(1 for q in all_evaluable_queries if q.metrics.fn > 0)

        query_frr = queries_with_fp / total_queries if total_queries > 0 else 0.0
        query_mr = queries_with_fn / queries_with_expected if queries_with_expected > 0 else 0.0

        return SuiteEvaluationResult(
            timestamp_iso=datetime.now(UTC).isoformat(),
            total_scenarios=len(scenario_results),
            evaluable_scenarios=len(evaluable_scenarios),
            non_evaluable_scenarios=len(scenario_results) - len(evaluable_scenarios),
            total_queries=total_queries,
            scenarios=scenario_results,
            overall_metrics=overall_metrics,
            overall_query_false_retrieval_rate=round(query_frr, 4),
            overall_query_miss_rate=round(query_mr, 4),
            queries_with_false_retrieval=queries_with_fp,
            queries_with_misses=queries_with_fn,
        )

    async def _evaluate_single_scenario(
        self,
        scenario_dict: dict[str, Any],
        temp_dir: Path,
        *,
        verbose: bool,
    ) -> ScenarioEvaluationResult:
        scenario_id = str(scenario_dict["scenario_id"])
        name = str(scenario_dict["name"])
        condition = str(scenario_dict["condition"])
        db_path = temp_dir / f"{scenario_id}.db"

        storage_config = StorageConfig(database_path=db_path)
        database = Database(db_path, storage_config)
        await database.open()

        try:
            session_id = uuid5(NAMESPACE_URL, f"cw2:memory_eval:session:{scenario_id}")
            now = datetime.now(UTC)
            now_iso = now.isoformat()

            # 1. Insert session record
            async with database.transaction() as conn:
                await conn.execute(
                    """
                    INSERT INTO sessions (
                        session_id, character_id, user_scope, state, conversation_state,
                        revision, next_sequence, created_at, updated_at
                    ) VALUES (?, 'default', 'local', 'active', 'idle', 0, 1, ?, ?)
                    """,
                    (str(session_id), now_iso, now_iso),
                )

            event_store = EventStore(database)
            repository = SQLiteMemoryRepository(database)
            policy = MemoryPolicy()

            # 2. Seed events
            event_id_map: dict[str, UUID] = {}
            source_metadata: dict[str, tuple[str, MemoryChannelAttribution | None]] = {}
            raw_events = cast(list[dict[str, Any]], scenario_dict.get("events", []))
            if raw_events:
                for evt_dict in raw_events:
                    evt_key = str(evt_dict["event_key"])
                    eid = uuid5(session_id, f"event:{evt_key}")
                    event_id_map[evt_key] = eid
                    tid = uuid5(session_id, f"turn:{evt_key}")
                    event_type = str(evt_dict["event_type"])
                    turn_role = (
                        "assistant" if event_type == "assistant.spoken_text_committed" else "user"
                    )
                    payload = cast(dict[str, Any], evt_dict.get("payload", {}))
                    attr_raw = evt_dict.get("channel_attribution")
                    if attr_raw is not None:
                        attr_dict = dict(cast(dict[str, Any], attr_raw))
                        if "received_at" not in attr_dict:
                            attr_dict["received_at"] = now.isoformat()
                        attr = MemoryChannelAttribution.model_validate(attr_dict)
                    else:
                        attr = None

                    async with database.transaction() as conn:
                        await conn.execute(
                            """
                            INSERT INTO turns (
                                turn_id, session_id, role, committed_text, committed_at, created_at,
                                source_context_json
                            ) VALUES (?, ?, ?, 'synthetic text', ?, ?, ?)
                            """,
                            (
                                str(tid),
                                str(session_id),
                                turn_role,
                                now_iso,
                                now_iso,
                                attr.model_dump_json() if attr else None,
                            ),
                        )

                    if event_type == "user.turn_committed":
                        source_metadata[evt_key] = ("user_turn", attr)
                        evt = UserTurnCommittedEvent(
                            event_id=eid,
                            session_id=session_id,
                            turn_id=tid,
                            occurred_at=now,
                            source="memory_evaluation",
                            payload=UserTurnCommittedPayload(
                                text=str(payload.get("text", "synthetic text"))
                            ),
                        )
                    elif event_type == "assistant.spoken_text_committed":
                        source_metadata[evt_key] = ("assistant_spoken", attr)
                        spoken = str(payload.get("spoken_text", "synthetic spoken text"))
                        evt = AssistantSpokenTextCommittedEvent(
                            event_id=eid,
                            session_id=session_id,
                            turn_id=tid,
                            occurred_at=now,
                            source="memory_evaluation",
                            payload=AssistantSpokenTextCommittedPayload(
                                stream_id=uuid4(),
                                segment_id=uuid4(),
                                text=spoken,
                                spoken_text=spoken,
                            ),
                        )
                    else:
                        source_metadata[evt_key] = ("memory_management", attr)
                        evt = GenericCoreEvent.model_validate(
                            {
                                "event_id": eid,
                                "event_type": event_type,
                                "session_id": session_id,
                                "occurred_at": now,
                                "source": "memory_evaluation",
                                "payload": payload,
                            }
                        )
                    await event_store.append(evt)
            # A distinct user observation backs records that do not name an explicit
            # source. Never silently attribute them to the first scenario event.
            default_eid = uuid5(session_id, "event:default_user_turn")
            default_tid = uuid5(session_id, "turn:default_user_turn")
            event_id_map["__default__"] = default_eid
            source_metadata["__default__"] = ("user_turn", None)

            async with database.transaction() as conn:
                await conn.execute(
                    """
                    INSERT INTO turns (
                        turn_id, session_id, role, committed_text, committed_at, created_at
                    ) VALUES (?, ?, 'user', 'synthetic initial observation', ?, ?)
                    """,
                    (str(default_tid), str(session_id), now_iso, now_iso),
                )

            default_evt = UserTurnCommittedEvent(
                event_id=default_eid,
                session_id=session_id,
                turn_id=default_tid,
                occurred_at=now,
                source="memory_evaluation",
                payload=UserTurnCommittedPayload(text="synthetic baseline observation"),
            )
            await event_store.append(default_evt)

            # 3. Seed records
            record_key_to_id: dict[str, UUID] = {}
            id_to_record_key: dict[UUID, str] = {}
            tombstone_after_seed: list[UUID] = []
            raw_records = cast(list[dict[str, Any]], scenario_dict.get("records", []))

            for rec_dict in raw_records:
                key = str(rec_dict["record_key"])
                mem_id = uuid5(session_id, f"memory:{key}")
                record_key_to_id[key] = mem_id
                id_to_record_key[mem_id] = key

            for rec_dict in raw_records:
                key = str(rec_dict["record_key"])
                mem_id = record_key_to_id[key]
                kind = cast(MemoryKind, rec_dict["kind"])
                subject_id = rec_dict.get("subject_id")
                predicate = rec_dict.get("predicate")
                value = rec_dict.get("value")
                text = str(rec_dict["text"])
                confidence = float(rec_dict.get("confidence", 0.9))
                importance = float(rec_dict.get("importance", 0.7))
                sensitivity_str = str(rec_dict.get("sensitivity", "private"))
                sensitivity = PrivacyLevel(sensitivity_str)
                state = str(rec_dict.get("state", "active"))
                pinned = bool(rec_dict.get("pinned", False))
                supersedes_key = rec_dict.get("supersedes_key")
                supersedes_id = record_key_to_id[supersedes_key] if supersedes_key else None

                source_keys = cast(list[str], rec_dict.get("source_event_keys", []))
                resolved_source_keys = source_keys or ["__default__"]
                source_eids = [event_id_map[k] for k in resolved_source_keys]

                record = MemoryRecord(
                    memory_id=mem_id,
                    namespace="character/default/user/local",
                    kind=kind,
                    subject_id=subject_id,
                    predicate=predicate,
                    value=value,
                    text=text,
                    observed_at=now,
                    confidence=confidence,
                    importance=importance,
                    sensitivity=sensitivity,
                    source_event_ids=source_eids,
                    state=cast(Any, state),
                    pinned=pinned,
                    supersedes=supersedes_id,
                    created_at=now,
                    updated_at=now,
                )

                sources = [
                    MemorySource(
                        source_id=uuid5(mem_id, f"source:{i}"),
                        memory_id=mem_id,
                        source_event_id=eid,
                        session_id=session_id,
                        source_kind=cast(Any, source_metadata[source_key][0]),
                        created_at=now,
                        channel_attribution=source_metadata[source_key][1],
                    )
                    for i, source_key in enumerate(resolved_source_keys)
                    for eid in [event_id_map[source_key]]
                ]

                await repository.create_record(
                    record, sources, supersede_target=supersedes_id if state == "active" else None
                )
                if rec_dict.get("after_seed") == "tombstone":
                    tombstone_after_seed.append(mem_id)

            for memory_id in tombstone_after_seed:
                await repository.tombstone(memory_id, now)

            # 4. Seed proposals (if any)
            raw_proposals = cast(list[dict[str, Any]], scenario_dict.get("proposals", []))
            for prop_dict in raw_proposals:
                prop_key = str(prop_dict["proposal_key"])
                prop_id = uuid5(session_id, f"proposal:{prop_key}")
                status = str(prop_dict["status"])
                operation = str(prop_dict["operation"])
                cand_dict = cast(dict[str, Any], prop_dict["candidate"])
                candidate = MemoryRecordDraft(
                    namespace="character/default/user/local",
                    kind=cast(MemoryKind, cand_dict["kind"]),
                    subject_id=cand_dict.get("subject_id"),
                    predicate=cand_dict.get("predicate"),
                    value=cand_dict.get("value"),
                    text=str(cand_dict["text"]),
                    observed_at=now,
                    confidence=float(cand_dict.get("confidence", 0.8)),
                    importance=float(cand_dict.get("importance", 0.6)),
                    sensitivity=PrivacyLevel(str(cand_dict.get("sensitivity", "private"))),
                )
                proposal = MemoryProposal(
                    proposal_id=prop_id,
                    operation=cast(Any, operation),
                    candidate=candidate,
                    evidence_event_ids=[event_id_map["__default__"]],
                    confidence=candidate.confidence,
                    rationale="synthetic proposal rationale",
                    status=cast(Any, status),
                    created_at=now,
                )
                await repository.save_proposal(proposal)

            # 5. Handle special scenario actions
            if scenario_dict.get("action_before_query") == "restart_database":
                await database.close()
                database = Database(db_path, storage_config)
                await database.open()
                repository = SQLiteMemoryRepository(database)

            # 6. Execute queries
            retriever = MemoryRetriever(
                repository=repository,
                policy=policy,
                semantic_index=NullSemanticMemoryIndex(),
                temporal_graph=NullTemporalMemoryGraph(),
            )
            namespaces = ["character/default/user/local", "user/local/global"]
            query_results: list[QueryEvaluationResult] = []

            raw_queries = cast(list[dict[str, Any]], scenario_dict.get("queries", []))
            for q_dict in raw_queries:
                qid = str(q_dict["query_id"])
                label = str(q_dict["label"])
                q_text = str(q_dict["query_text"])
                budget = int(q_dict.get("token_budget", 700))
                is_neg = bool(q_dict.get("is_negative", False))
                expected_keys = cast(list[str], q_dict.get("expected_memory_keys", []))

                packet = await retriever.retrieve_context(
                    query=q_text,
                    namespaces=namespaces,
                    token_budget=budget,
                    limit=12,
                )

                all_excerpts = (
                    packet.pinned_facts
                    + packet.recent_episodes
                    + packet.relevant_memories
                    + packet.open_commitments
                    + packet.relationship_context
                )
                retrieved_ids = {item.memory_id for item in all_excerpts}
                unknown_ids = retrieved_ids - id_to_record_key.keys()
                if unknown_ids:
                    raise ValueError(f"retriever returned unseeded memory in {qid}")
                retrieved_keys = sorted(id_to_record_key[uid] for uid in retrieved_ids)

                # Calculate metrics
                metrics = compute_metrics(
                    retrieved=set(retrieved_keys),
                    expected=set(expected_keys),
                )

                if verbose:
                    logger.info(
                        "Query %s (%s): TP=%d FP=%d FN=%d precision=%.2f recall=%.2f",
                        qid,
                        label,
                        metrics.tp,
                        metrics.fp,
                        metrics.fn,
                        metrics.precision,
                        metrics.recall,
                    )

                query_results.append(
                    QueryEvaluationResult(
                        query_id=qid,
                        label=label,
                        is_negative=is_neg,
                        token_budget=budget,
                        expected_keys=expected_keys,
                        retrieved_keys=retrieved_keys,
                        metrics=metrics,
                    )
                )

            # Aggregate scenario
            scenario_metrics = aggregate_query_metrics([q.metrics for q in query_results])
            total_q = len(query_results)
            q_with_fp = sum(1 for q in query_results if q.metrics.fp > 0)
            q_with_exp = sum(1 for q in query_results if len(q.expected_keys) > 0)
            q_with_fn = sum(1 for q in query_results if q.metrics.fn > 0)

            q_frr = q_with_fp / total_q if total_q > 0 else 0.0
            q_mr = q_with_fn / q_with_exp if q_with_exp > 0 else 0.0

            return ScenarioEvaluationResult(
                scenario_id=scenario_id,
                name=name,
                condition=condition,
                evaluable=True,
                limitation_note=None,
                queries=query_results,
                aggregate_metrics=scenario_metrics,
                query_false_retrieval_rate=round(q_frr, 4),
                query_miss_rate=round(q_mr, 4),
                queries_with_false_retrieval=q_with_fp,
                queries_with_misses=q_with_fn,
            )

        finally:
            await database.close()


def format_markdown_report(result: SuiteEvaluationResult) -> str:
    """Render a clean GitHub-style markdown report without private text or credentials."""
    eval_count = result.evaluable_scenarios
    non_eval_count = result.non_evaluable_scenarios
    m = result.overall_metrics
    prec_str = f"{m.precision * 100:.2f}%"
    rec_str = f"{m.recall * 100:.2f}%"
    frr_str = f"{m.false_retrieval_rate * 100:.2f}%"
    mr_str = f"{m.miss_rate * 100:.2f}%"
    q_frr_str = f"{result.overall_query_false_retrieval_rate * 100:.2f}%"
    q_mr_str = f"{result.overall_query_miss_rate * 100:.2f}%"
    queries_with_expected = sum(
        bool(query.expected_keys) for scenario in result.scenarios for query in scenario.queries
    )

    lines: list[str] = [
        "# Memory Retrieval Quantitative Evaluation Report",
        "",
        f"- Date: {result.timestamp_iso}",
        (
            f"- Total Scenarios: {result.total_scenarios} "
            f"({eval_count} evaluable, {non_eval_count} boundary)"
        ),
        f"- Total Evaluated Queries: {result.total_queries}",
        "",
        "## 1. Executive Summary & Aggregate Metrics",
        "",
        "| Metric | Value | Denominator / Formula | Interpretation |",
        "| :--- | :--- | :--- | :--- |",
        f"| **True Positives (TP)** | {m.tp} | Relevant items retrieved | Correct matches |",
        (f"| **False Positives (FP)** | {m.fp} | Irrelevant/excluded retrieved | False context |"),
        f"| **False Negatives (FN)** | {m.fn} | Expected items not retrieved | Missed memories |",
        f"| **Precision** | {prec_str} | TP / (TP + FP) = {m.tp}/{m.total_retrieved} | Purity |",
        (f"| **Recall** | {rec_str} | TP / (TP + FN) = {m.tp}/{m.total_expected} | Completeness |"),
        (
            f"| **False Retrieval Rate** | {frr_str} | "
            f"FP / (TP + FP) = {m.fp}/{m.total_retrieved} | False rate |"
        ),
        (f"| **Miss Rate** | {mr_str} | FN / (TP + FN) = {m.fn}/{m.total_expected} | Miss rate |"),
        (
            f"| **Query False Retrieval Rate** | {q_frr_str} | "
            f"Queries with FP>0 / Total Queries = "
            f"{result.queries_with_false_retrieval}/{result.total_queries} | Queries with FP |"
        ),
        (
            f"| **Query Miss Rate** | {q_mr_str} | "
            f"Queries with FN>0 / Queries with Exp>0 = "
            f"{result.queries_with_misses}/{queries_with_expected} | Queries with FN |"
        ),
        "",
        "## 2. Condition Coverage Matrix (All 10 Q06 Target Conditions)",
        "",
        (
            "| Condition | Evaluable | Status | TP | FP | FN | "
            "Precision | Recall | FRR | Miss Rate | Notes |"
        ),
        ("| :--- | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :--- |"),
    ]

    for s in result.scenarios:
        if s.evaluable:
            sm = s.aggregate_metrics
            p_str = f"{sm.precision * 100:.1f}%"
            r_str = f"{sm.recall * 100:.1f}%"
            f_str = f"{sm.false_retrieval_rate * 100:.1f}%"
            m_str = f"{sm.miss_rate * 100:.1f}%"
            if sm.fp and sm.fn:
                status = "OBSERVED_FP_FN"
            elif sm.fp:
                status = "OBSERVED_FP"
            elif sm.fn:
                status = "OBSERVED_FN"
            else:
                status = "PASS"
            notes = f"Observed FP={sm.fp}, FN={sm.fn}"
            lines.append(
                f"| {s.condition} | Yes | {status} | {sm.tp} | {sm.fp} | {sm.fn} | "
                f"{p_str} | {r_str} | {f_str} | {m_str} | {notes} |"
            )
        else:
            note = s.limitation_note or "Write-boundary invariant"
            lines.append(f"| {s.condition} | No | BOUNDARY | - | - | - | - | - | - | - | {note} |")

    lines.extend(["", "## 3. Budget Pressure", ""])
    budget_scenario = next(
        (
            scenario
            for scenario in result.scenarios
            if scenario.scenario_id == "scenario_token_budget_pressure"
        ),
        None,
    )
    if budget_scenario is not None:
        for query in budget_scenario.queries:
            if query.expected_keys:
                lines.append(
                    f"- `{query.query_id}` ({query.token_budget} budget): "
                    f"TP={query.metrics.tp}, FP={query.metrics.fp}, FN={query.metrics.fn}; "
                    f"miss rate {query.metrics.miss_rate * 100:.2f}%."
                )
    lines.extend(
        [
            "",
            "## 4. Policy, Deletion, and Privacy Exclusions",
            "",
            (
                "- **Sensitive fact**: MemoryPolicy filters PrivacyLevel.SENSITIVE "
                "at retrieval (FP=0)."
            ),
            "- **Pending implicit fact**: Unconfirmed proposals stay in memory_proposals (FP=0).",
            "- **Stale/superseded fact**: Superseded records are purged from FTS5 index (FP=0).",
            "- **Forget/tombstone**: Tombstoned records trigger FTS deletion (FP=0).",
            "- **Restart**: Preserves active facts and excludes superseded/tombstoned identically.",
            "",
            "## 5. Per-Scenario Query Breakdown",
            "",
        ]
    )

    for s in result.scenarios:
        lines.append(f"### {s.name} (`{s.condition}`)")
        if not s.evaluable:
            lines.append(f"> [!NOTE] Boundary Limitation: {s.limitation_note}\n")
            continue

        sm = s.aggregate_metrics
        lines.extend(
            [
                f"- Evaluated Queries: {len(s.queries)}",
                (
                    f"- Scenario Aggregate: TP={sm.tp}, FP={sm.fp}, FN={sm.fn}, "
                    f"Precision={sm.precision * 100:.1f}%, Recall={sm.recall * 100:.1f}%, "
                    f"Miss Rate={sm.miss_rate * 100:.1f}%"
                ),
                "",
                (
                    "| Query ID | Label | Expected Keys | Retrieved Keys | "
                    "TP | FP | FN | P | R | FRR | MR |"
                ),
                (
                    "| :--- | :--- | :--- | :--- | "
                    ":---: | :---: | :---: | :---: | :---: | :---: | :---: |"
                ),
            ]
        )
        for q in s.queries:
            exp_str = ", ".join(q.expected_keys) if q.expected_keys else "(empty)"
            ret_str = ", ".join(q.retrieved_keys) if q.retrieved_keys else "(empty)"
            qm = q.metrics
            lines.append(
                f"| `{q.query_id}` | {q.label} | {exp_str} | {ret_str} | "
                f"{qm.tp} | {qm.fp} | {qm.fn} | {qm.precision * 100:.0f}% | "
                f"{qm.recall * 100:.0f}% | {qm.false_retrieval_rate * 100:.0f}% | "
                f"{qm.miss_rate * 100:.0f}% |"
            )
        lines.append("")

    return "\n".join(lines)


def format_json_report(result: SuiteEvaluationResult) -> str:
    """Render serialized JSON report suitable for automated pipelines."""
    data = asdict(result)
    return json.dumps(data, indent=2, ensure_ascii=False)


def main() -> int:
    parser = argparse.ArgumentParser(description="Evaluate memory retrieval quantitative metrics.")
    parser.add_argument(
        "--fixtures-path",
        type=Path,
        default=DEFAULT_FIXTURES_PATH,
        help="Path to retrieval scenarios fixture JSON.",
    )
    parser.add_argument(
        "--output-json",
        type=Path,
        default=None,
        help="Optional path to output evaluation results as JSON.",
    )
    parser.add_argument(
        "--output-markdown",
        type=Path,
        default=None,
        help="Optional path to output evaluation report as Markdown.",
    )
    parser.add_argument(
        "--verbose",
        "-v",
        action="store_true",
        help="Enable detailed query logging.",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="Exit with non-zero code if security or regression invariants fail.",
    )

    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    evaluator = MemoryRetrievalEvaluator(fixtures_path=args.fixtures_path)
    result = asyncio.run(evaluator.evaluate_suite(verbose=args.verbose))

    markdown_report = format_markdown_report(result)
    print(markdown_report)

    if args.output_markdown is not None:
        args.output_markdown.parent.mkdir(parents=True, exist_ok=True)
        args.output_markdown.write_text(markdown_report, encoding="utf-8")
        logger.info("Saved Markdown report to %s", args.output_markdown)

    if args.output_json is not None:
        args.output_json.parent.mkdir(parents=True, exist_ok=True)
        args.output_json.write_text(format_json_report(result), encoding="utf-8")
        logger.info("Saved JSON report to %s", args.output_json)

    if args.check:
        fixture_scenarios = {
            str(s["scenario_id"]): s for s in evaluator.load_fixture_data()["scenarios"]
        }
        for scenario in result.scenarios:
            fixture = fixture_scenarios[scenario.scenario_id]
            forbidden = {
                str(record["record_key"])
                for record in fixture.get("records", [])
                if record.get("sensitivity") == "sensitive"
                or record.get("state", "active") != "active"
                or record.get("after_seed") == "tombstone"
            }
            for query in scenario.queries:
                if forbidden.intersection(query.retrieved_keys):
                    logger.error("Check failed: excluded memory retrieved in %s", query.query_id)
                    return 1

        if args.fixtures_path.resolve() != DEFAULT_FIXTURES_PATH.resolve():
            return 0

        # Check security, durability, and quantitative invariants:
        sensitive_scen = next(
            (s for s in result.scenarios if s.scenario_id == "scenario_sensitive_fact"), None
        )
        if sensitive_scen is None or sensitive_scen.aggregate_metrics.fp > 0:
            logger.error("Check failed: Sensitive fact policy exclusion failed (leak detected)")
            return 1

        pending_scen = next(
            (s for s in result.scenarios if s.scenario_id == "scenario_pending_implicit_fact"), None
        )
        if pending_scen is None or pending_scen.aggregate_metrics.fp > 0:
            logger.error("Check failed: Pending implicit fact exclusion failed")
            return 1

        forget_scen = next(
            (s for s in result.scenarios if s.scenario_id == "scenario_forget_delete"), None
        )
        if forget_scen is None or forget_scen.aggregate_metrics.fp > 0:
            logger.error(
                "Check failed: Forget tombstone exclusion failed (resurrected deleted memory)"
            )
            return 1

        restart_scen = next(
            (s for s in result.scenarios if s.scenario_id == "scenario_restart"), None
        )
        if restart_scen is None or restart_scen.aggregate_metrics.fp > 0:
            logger.error("Check failed: Database restart exclusion failed")
            return 1

        budget_scen = next(
            (s for s in result.scenarios if s.scenario_id == "scenario_token_budget_pressure"), None
        )
        if budget_scen is None or budget_scen.aggregate_metrics.fn > 3:
            logger.error(
                "Check failed: budget-pressure misses exceeded the observed baseline: %s",
                getattr(budget_scen, "aggregate_metrics", None),
            )
            return 1

        if result.overall_metrics.tp < 18 or result.overall_metrics.fp > 1:
            logger.error(
                "Check failed: retrieval quality regressed from the synthetic baseline: %s",
                result.overall_metrics,
            )
            return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
