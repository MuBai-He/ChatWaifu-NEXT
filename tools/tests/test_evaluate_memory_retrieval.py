"""Unit and integration tests for tools/evaluate_memory_retrieval.py."""

from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import pytest
from chatwaifu_protocol.base import PrivacyLevel
from chatwaifu_protocol.memory import MemoryRecord
from chatwaifu_runtime.config.settings import StorageConfig
from chatwaifu_runtime.persistence.database import Database
from chatwaifu_runtime.persistence.sqlite_memory_repository import SQLiteMemoryRepository

from tools.evaluate_memory_retrieval import (
    DEFAULT_FIXTURES_PATH,
    MemoryRetrievalEvaluator,
    MetricScores,
    aggregate_query_metrics,
    compute_metrics,
    format_json_report,
    format_markdown_report,
    main,
)


def test_metric_scores_arithmetic() -> None:
    """Verify TP, FP, FN, precision, recall, false retrieval rate, and miss rate arithmetic."""
    # Case 1: Standard mixed retrieval
    retrieved = {"a", "b", "c"}
    expected = {"b", "c", "d"}
    scores = compute_metrics(retrieved, expected)
    assert scores.tp == 2  # b, c
    assert scores.fp == 1  # a
    assert scores.fn == 1  # d
    assert scores.total_retrieved == 3
    assert scores.total_expected == 3
    assert scores.precision == round(2 / 3, 4)
    assert scores.recall == round(2 / 3, 4)
    assert scores.false_retrieval_rate == round(1 / 3, 4)
    assert scores.miss_rate == round(1 / 3, 4)

    # Case 2: Negative query with 0 retrieved (perfect true negative)
    neg_scores = compute_metrics(set(), set())
    assert neg_scores.tp == 0
    assert neg_scores.fp == 0
    assert neg_scores.fn == 0
    assert neg_scores.precision == 1.0
    assert neg_scores.recall == 1.0
    assert neg_scores.false_retrieval_rate == 0.0
    assert neg_scores.miss_rate == 0.0

    # Case 3: Negative query with false alarm (FP > 0)
    neg_alarm = compute_metrics({"distractor"}, set())
    assert neg_alarm.tp == 0
    assert neg_alarm.fp == 1
    assert neg_alarm.fn == 0
    assert neg_alarm.precision == 0.0
    assert neg_alarm.recall == 1.0
    assert neg_alarm.false_retrieval_rate == 1.0
    assert neg_alarm.miss_rate == 0.0

    # Case 4: Complete miss (FN > 0, nothing retrieved)
    miss_scores = compute_metrics(set(), {"target"})
    assert miss_scores.tp == 0
    assert miss_scores.fp == 0
    assert miss_scores.fn == 1
    assert miss_scores.precision == 0.0
    assert miss_scores.recall == 0.0
    assert miss_scores.false_retrieval_rate == 0.0
    assert miss_scores.miss_rate == 1.0


def test_aggregate_query_metrics_micro_average() -> None:
    """Verify micro-averaging across queries with different expected/retrieved counts."""
    q1 = MetricScores(
        tp=1,
        fp=1,
        fn=0,
        total_retrieved=2,
        total_expected=1,
        precision=0.5,
        recall=1.0,
        false_retrieval_rate=0.5,
        miss_rate=0.0,
    )
    q2 = MetricScores(
        tp=2,
        fp=0,
        fn=1,
        total_retrieved=2,
        total_expected=3,
        precision=1.0,
        recall=round(2 / 3, 4),
        false_retrieval_rate=0.0,
        miss_rate=round(1 / 3, 4),
    )
    agg = aggregate_query_metrics([q1, q2])
    assert agg.tp == 3
    assert agg.fp == 1
    assert agg.fn == 1
    assert agg.total_retrieved == 4
    assert agg.total_expected == 4
    assert agg.precision == 0.75
    assert agg.recall == 0.75
    assert agg.false_retrieval_rate == 0.25
    assert agg.miss_rate == 0.25


def test_fixture_loading_and_structure() -> None:
    """Verify fixture covers all 10 target conditions and has expected query counts."""
    evaluator = MemoryRetrievalEvaluator(DEFAULT_FIXTURES_PATH)
    data = evaluator.load_fixture_data()
    assert data["version"] == "1.0"
    scenarios = data["scenarios"]
    assert len(scenarios) == 10

    scenario_ids = [s["scenario_id"] for s in scenarios]
    expected_ids = [
        "scenario_explicit_fact",
        "scenario_pending_implicit_fact",
        "scenario_sensitive_fact",
        "scenario_stale_superseded_fact",
        "scenario_shared_joke_relevance",
        "scenario_absent_source",
        "scenario_token_budget_pressure",
        "scenario_correction",
        "scenario_forget_delete",
        "scenario_restart",
    ]
    assert scenario_ids == expected_ids

    total_queries = sum(len(s.get("queries", [])) for s in scenarios)
    assert total_queries == 27

    # Verify absent source condition is explicitly non-evaluable
    absent_scen = next(s for s in scenarios if s["scenario_id"] == "scenario_absent_source")
    assert absent_scen["evaluable_in_retrieval"] is False
    assert "write contracts" in absent_scen["limitation_note"].lower()


@pytest.mark.asyncio
async def test_absent_source_write_boundary_rejection(tmp_path: Path) -> None:
    """Validate that records without sources are rejected at the repository write boundary."""
    db_path = tmp_path / "absent_source.sqlite3"
    storage_config = StorageConfig(database_path=db_path)
    db = Database(db_path, storage_config)
    await db.open()

    repo = SQLiteMemoryRepository(db)

    now = datetime.now(UTC)
    rec = MemoryRecord(
        memory_id=uuid4(),
        namespace="character/default/user/local",
        kind="semantic.fact",
        subject_id="user1",
        predicate="hobby",
        value={"text": "skating"},
        text="用户喜欢滑冰",
        confidence=0.9,
        importance=0.8,
        sensitivity=PrivacyLevel.PRIVATE,
        source_event_ids=[uuid4()],
        observed_at=now,
        created_at=now,
        updated_at=now,
    )

    with pytest.raises(ValueError, match="memory records require at least one source"):
        await repo.create_record(rec, sources=[])

    await db.close()


@pytest.mark.asyncio
async def test_sensitive_fact_policy_exclusion(tmp_path: Path) -> None:
    """Validate that private/sensitive facts are never returned during retrieval (FP=0)."""
    evaluator = MemoryRetrievalEvaluator(DEFAULT_FIXTURES_PATH)
    data = evaluator.load_fixture_data()
    scen_data = [s for s in data["scenarios"] if s["scenario_id"] == "scenario_sensitive_fact"]

    result = await evaluator.run_scenarios(scen_data, tmp_path, verbose=False)
    assert len(result.scenarios) == 1
    scen = result.scenarios[0]

    # Aggregate false retrieval must be 0 (no sensitive leak)
    assert scen.aggregate_metrics.fp == 0
    assert scen.aggregate_metrics.tp == 1
    assert scen.aggregate_metrics.fn == 0
    assert scen.aggregate_metrics.precision == 1.0


@pytest.mark.asyncio
async def test_forget_delete_tombstone_exclusion(tmp_path: Path) -> None:
    """Validate that tombstoned/forgotten records are excluded from retrieval (FP=0)."""
    evaluator = MemoryRetrievalEvaluator(DEFAULT_FIXTURES_PATH)
    data = evaluator.load_fixture_data()
    scen_data = [s for s in data["scenarios"] if s["scenario_id"] == "scenario_forget_delete"]

    result = await evaluator.run_scenarios(scen_data, tmp_path, verbose=False)
    assert len(result.scenarios) == 1
    scen = result.scenarios[0]

    # Deleted record probe must return empty
    probe_q = next(q for q in scen.queries if q.query_id == "q_forget_probe")
    assert probe_q.retrieved_keys == []
    assert probe_q.metrics.fp == 0

    # Surviving active record is retrieved
    surv_q = next(q for q in scen.queries if q.query_id == "q_forget_surviving")
    assert surv_q.retrieved_keys == ["rec_music_sleep"]
    assert surv_q.metrics.tp == 1

    assert scen.aggregate_metrics.tp == 1
    assert scen.aggregate_metrics.fp == 0
    assert scen.aggregate_metrics.fn == 0


@pytest.mark.asyncio
async def test_stale_superseded_fact_exclusion(tmp_path: Path) -> None:
    """Validate that superseded facts are excluded from retrieval while replacement is kept."""
    evaluator = MemoryRetrievalEvaluator(DEFAULT_FIXTURES_PATH)
    data = evaluator.load_fixture_data()
    scen_data = [
        s for s in data["scenarios"] if s["scenario_id"] == "scenario_stale_superseded_fact"
    ]

    result = await evaluator.run_scenarios(scen_data, tmp_path, verbose=False)
    assert len(result.scenarios) == 1
    scen = result.scenarios[0]

    # Stale probe must not retrieve old city
    probe_q = next(q for q in scen.queries if q.query_id == "q_superseded_old_probe")
    assert probe_q.retrieved_keys == []
    assert probe_q.metrics.fp == 0

    # Both active new city and distractor hobby are retrieved (TP=2)
    assert scen.aggregate_metrics.tp == 2
    assert scen.aggregate_metrics.fp == 0
    assert scen.aggregate_metrics.fn == 0


@pytest.mark.asyncio
async def test_pending_implicit_fact_exclusion(tmp_path: Path) -> None:
    """Validate that pending proposals in memory_proposals do not leak into active retrieval."""
    evaluator = MemoryRetrievalEvaluator(DEFAULT_FIXTURES_PATH)
    data = evaluator.load_fixture_data()
    scen_data = [
        s for s in data["scenarios"] if s["scenario_id"] == "scenario_pending_implicit_fact"
    ]

    result = await evaluator.run_scenarios(scen_data, tmp_path, verbose=False)
    assert len(result.scenarios) == 1
    scen = result.scenarios[0]

    # Pending proposal probe must return empty
    probe_q = next(q for q in scen.queries if q.query_id == "q_pending_probe")
    assert probe_q.retrieved_keys == []
    assert probe_q.metrics.fp == 0

    # Active distractor is retrieved (TP=1)
    assert scen.aggregate_metrics.tp == 1
    assert scen.aggregate_metrics.fp == 0
    assert scen.aggregate_metrics.fn == 0


@pytest.mark.asyncio
async def test_restart_durability_and_exclusion(tmp_path: Path) -> None:
    """Validate that active facts survive restart while superseded records remain excluded."""
    evaluator = MemoryRetrievalEvaluator(DEFAULT_FIXTURES_PATH)
    data = evaluator.load_fixture_data()
    scen_data = [s for s in data["scenarios"] if s["scenario_id"] == "scenario_restart"]

    result = await evaluator.run_scenarios(scen_data, tmp_path, verbose=False)
    assert len(result.scenarios) == 1
    scen = result.scenarios[0]

    assert scen.aggregate_metrics.tp == 1
    assert scen.aggregate_metrics.fp == 0
    assert scen.aggregate_metrics.fn == 0


@pytest.mark.asyncio
async def test_token_budget_pressure_truncation(tmp_path: Path) -> None:
    """Validate quantitative miss measurement when token budget truncates candidates."""
    evaluator = MemoryRetrievalEvaluator(DEFAULT_FIXTURES_PATH)
    data = evaluator.load_fixture_data()
    scen_data = [
        s for s in data["scenarios"] if s["scenario_id"] == "scenario_token_budget_pressure"
    ]

    result = await evaluator.run_scenarios(scen_data, tmp_path, verbose=False)
    assert len(result.scenarios) == 1
    scen = result.scenarios[0]

    # Generous budget: 4 out of 4 fit
    gen_q = next(q for q in scen.queries if q.query_id == "q_budget_generous")
    assert gen_q.metrics.tp == 4
    assert gen_q.metrics.fn == 0
    assert gen_q.metrics.miss_rate == 0.0

    # Constrained budget (25 tokens): only 1 fits out of 4 expected
    con_q = next(q for q in scen.queries if q.query_id == "q_budget_constrained")
    assert con_q.metrics.tp == 1
    assert con_q.metrics.fp == 0
    assert con_q.metrics.fn == 3
    assert con_q.metrics.miss_rate == 0.75

    # Overall scenario aggregate: TP=5, FP=0, FN=3
    assert scen.aggregate_metrics.tp == 5
    assert scen.aggregate_metrics.fp == 0
    assert scen.aggregate_metrics.fn == 3


@pytest.mark.asyncio
async def test_real_world_false_retrieval_and_miss_observations(tmp_path: Path) -> None:
    """Validate that the evaluator honestly captures real non-zero false retrievals."""
    evaluator = MemoryRetrievalEvaluator(DEFAULT_FIXTURES_PATH)
    data = evaluator.load_fixture_data()
    scen_data = [
        s
        for s in data["scenarios"]
        if s["scenario_id"] in ("scenario_shared_joke_relevance", "scenario_correction")
    ]

    result = await evaluator.run_scenarios(scen_data, tmp_path, verbose=False)
    assert len(result.scenarios) == 2

    # 1. shared-joke relevance: recency padding fallback is suppressed for topical joke query (FP=0)
    joke_scen = next(
        s for s in result.scenarios if s.scenario_id == "scenario_shared_joke_relevance"
    )
    assert joke_scen.aggregate_metrics.fp == 0
    assert joke_scen.queries_with_false_retrieval == 0
    joke_probe = next(q for q in joke_scen.queries if q.query_id == "q_joke_positive")
    assert joke_probe.expected_keys == ["rec_joke_popsicle"]
    assert joke_probe.retrieved_keys == ["rec_joke_popsicle"]

    # A question using the obsolete value should retrieve the corrected fact, never the old one.
    corr_scen = next(s for s in result.scenarios if s.scenario_id == "scenario_correction")
    probe = next(q for q in corr_scen.queries if q.query_id == "q_correction_old_probe")
    assert probe.expected_keys == ["rec_lang_python"]
    assert probe.retrieved_keys == ["rec_lang_python"]
    assert corr_scen.aggregate_metrics.fp == 0


@pytest.mark.asyncio
async def test_provenance_and_lifecycle_are_persisted(tmp_path: Path) -> None:
    """The fixture exercises actual source kinds, supersession, and tombstoning."""
    evaluator = MemoryRetrievalEvaluator(DEFAULT_FIXTURES_PATH)
    scenarios = evaluator.load_fixture_data()["scenarios"]
    selected = [
        s
        for s in scenarios
        if s["scenario_id"]
        in {"scenario_shared_joke_relevance", "scenario_correction", "scenario_forget_delete"}
    ]
    await evaluator.run_scenarios(selected, tmp_path, verbose=False)

    with sqlite3.connect(tmp_path / "scenario_shared_joke_relevance.db") as conn:
        rows = conn.execute(
            "SELECT source_kind, channel_attribution_json FROM memory_sources "
            "WHERE memory_id IN (SELECT memory_id FROM memory_records "
            "WHERE predicate = 'shared_joke.冰棒') ORDER BY source_kind"
        ).fetchall()
        default_source = conn.execute(
            "SELECT source_kind, channel_attribution_json FROM memory_sources "
            "WHERE memory_id IN (SELECT memory_id FROM memory_records "
            "WHERE predicate = 'preference.dessert')"
        ).fetchone()
    assert [row[0] for row in rows] == ["assistant_spoken", "user_turn"]
    assert all(json.loads(row[1])["provider_id"] == "qq" for row in rows)
    assert default_source == ("user_turn", None)

    with sqlite3.connect(tmp_path / "scenario_correction.db") as conn:
        states = dict(conn.execute("SELECT text, state FROM memory_records").fetchall())
    assert states["最喜爱的主力编程语言是Rust"] == "superseded"
    assert states["最喜爱的主力编程语言改成了Python"] == "active"

    with sqlite3.connect(tmp_path / "scenario_forget_delete.db") as conn:
        states = dict(conn.execute("SELECT text, state FROM memory_records").fetchall())
    assert states["经常失眠需要喝温热牛奶助眠"] == "tombstoned"


@pytest.mark.asyncio
async def test_full_suite_evaluation_runner(tmp_path: Path) -> None:
    """Validate end-to-end evaluation suite execution and metrics."""
    evaluator = MemoryRetrievalEvaluator(DEFAULT_FIXTURES_PATH)
    result = await evaluator.evaluate_suite(temp_dir=tmp_path, verbose=False)

    assert result.total_scenarios == 10
    assert result.evaluable_scenarios == 9
    assert result.non_evaluable_scenarios == 1
    assert result.total_queries == 27

    # Stable quantitative aggregate metrics:
    assert result.overall_metrics.tp == 18
    assert result.overall_metrics.fp == 0
    assert result.overall_metrics.fn == 3
    assert result.overall_metrics.precision == 1.0
    assert result.overall_metrics.recall == 0.8571
    assert result.overall_metrics.false_retrieval_rate == 0.0
    assert result.overall_metrics.miss_rate == 0.1429

    # Query level rates:
    assert result.overall_query_false_retrieval_rate == 0.0
    assert result.overall_query_miss_rate == 0.0667
    assert result.queries_with_false_retrieval == 0
    assert result.queries_with_misses == 1


def test_report_formatting_sanitization(tmp_path: Path) -> None:
    """Validate report output format and verify no credentials or prompts are leaked."""
    # Create dummy suite result
    scores = MetricScores(
        tp=10,
        fp=1,
        fn=2,
        total_retrieved=11,
        total_expected=12,
        precision=0.9091,
        recall=0.8333,
        false_retrieval_rate=0.0909,
        miss_rate=0.1667,
    )

    from tools.evaluate_memory_retrieval import (
        QueryEvaluationResult,
        ScenarioEvaluationResult,
        SuiteEvaluationResult,
    )

    q_res = QueryEvaluationResult(
        query_id="q1",
        label="Test Query",
        is_negative=False,
        token_budget=700,
        expected_keys=["rec_1"],
        retrieved_keys=["rec_1"],
        metrics=MetricScores(
            tp=1,
            fp=0,
            fn=0,
            total_retrieved=1,
            total_expected=1,
            precision=1.0,
            recall=1.0,
            false_retrieval_rate=0.0,
            miss_rate=0.0,
        ),
    )
    scen_res = ScenarioEvaluationResult(
        scenario_id="scenario_test",
        name="Test Scenario",
        condition="explicit_fact",
        evaluable=True,
        limitation_note=None,
        queries=[q_res],
        aggregate_metrics=scores,
        query_false_retrieval_rate=0.0,
        query_miss_rate=0.0,
        queries_with_false_retrieval=0,
        queries_with_misses=0,
    )
    suite_res = SuiteEvaluationResult(
        timestamp_iso=datetime.now(UTC).isoformat(),
        total_scenarios=1,
        evaluable_scenarios=1,
        non_evaluable_scenarios=0,
        total_queries=1,
        scenarios=[scen_res],
        overall_metrics=scores,
        overall_query_false_retrieval_rate=0.0,
        overall_query_miss_rate=0.0,
        queries_with_false_retrieval=0,
        queries_with_misses=0,
    )

    md = format_markdown_report(suite_res)
    json_str = format_json_report(suite_res)

    assert "# Memory Retrieval Quantitative Evaluation Report" in md
    assert "Executive Summary & Aggregate Metrics" in md
    assert "password" not in md.lower()
    assert "sk-" not in md.lower()

    parsed = json.loads(json_str)
    assert parsed["total_scenarios"] == 1
    assert parsed["overall_metrics"]["tp"] == 10


def test_cli_main_check(monkeypatch: pytest.MonkeyPatch) -> None:
    """Validate that CLI entrypoint runs cleanly with --check and returns exit code 0."""
    monkeypatch.setattr("sys.argv", ["evaluate_memory_retrieval.py", "--check"])
    exit_code = main()
    assert exit_code == 0


def test_cli_main_check_rejects_nonzero_false_retrieval(monkeypatch: pytest.MonkeyPatch) -> None:
    """Validate that CLI entrypoint rejects any future non-zero false retrieval."""
    monkeypatch.setattr("sys.argv", ["evaluate_memory_retrieval.py", "--check"])

    async def _mock_eval_suite(*args: object, **kwargs: object) -> object:
        from tools.evaluate_memory_retrieval import (
            MetricScores,
            QueryEvaluationResult,
            ScenarioEvaluationResult,
            SuiteEvaluationResult,
        )

        mock_metrics = MetricScores(
            tp=18,
            fp=1,  # Nonzero false retrieval regression
            fn=3,
            total_retrieved=19,
            total_expected=21,
            precision=0.9474,
            recall=0.8571,
            false_retrieval_rate=0.0526,
            miss_rate=0.1429,
        )
        dummy_query = QueryEvaluationResult(
            query_id="q_dummy",
            label="dummy",
            is_negative=False,
            token_budget=700,
            expected_keys=["rec1"],
            retrieved_keys=["rec1", "rec2"],
            metrics=mock_metrics,
        )
        dummy_scenario = ScenarioEvaluationResult(
            scenario_id="scenario_explicit_fact",
            name="dummy",
            condition="explicit fact",
            evaluable=True,
            limitation_note=None,
            queries=[dummy_query],
            aggregate_metrics=mock_metrics,
            query_false_retrieval_rate=1.0,
            query_miss_rate=0.0,
            queries_with_false_retrieval=1,
            queries_with_misses=0,
        )
        return SuiteEvaluationResult(
            timestamp_iso="2026-09-30T00:00:00Z",
            total_scenarios=1,
            evaluable_scenarios=1,
            non_evaluable_scenarios=0,
            total_queries=1,
            scenarios=[dummy_scenario],
            overall_metrics=mock_metrics,
            overall_query_false_retrieval_rate=1.0,
            overall_query_miss_rate=0.0,
            queries_with_false_retrieval=1,
            queries_with_misses=0,
        )

    monkeypatch.setattr(
        "tools.evaluate_memory_retrieval.MemoryRetrievalEvaluator.evaluate_suite",
        _mock_eval_suite,
    )
    exit_code = main()
    assert exit_code == 1
