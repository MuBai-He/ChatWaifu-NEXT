"""Unit and integration tests for tools/evaluate_character_scenarios.py."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tools.evaluate_character_scenarios import (
    DEFAULT_FIXTURES_PATH,
    ControlledEvaluatorProvider,
    EvaluationRunner,
    estimate_pricing,
    load_scenarios,
)


def test_load_scenarios_validates_12_scenarios_and_48_turns() -> None:
    scenarios = load_scenarios(DEFAULT_FIXTURES_PATH)
    assert len(scenarios) == 12

    expected_ids = [
        "greeting",
        "teasing",
        "correction",
        "stop_joking",
        "technical_help",
        "low_mood",
        "goodbye",
        "identity",
        "verbose_history_pressure",
        "topic_switch",
        "insufficient_shared_history",
        "detailed_answer",
    ]
    actual_ids = [s.id for s in scenarios]
    assert actual_ids == expected_ids

    total_turns = sum(len(s.turns) for s in scenarios)
    assert total_turns == 48

    for s in scenarios:
        assert s.name
        assert s.description
        assert len(s.turns) == 4
        for turn in s.turns:
            assert turn.turn_id in {1, 2, 3, 4}
            assert turn.user_text
            assert len(turn.expected_behavior) >= 1
            assert len(turn.forbidden_behavior) >= 1
            assert turn.review_criteria


def test_pricing_estimation_known_and_unknown_models() -> None:
    cost, text = estimate_pricing(
        "specified-model",
        1_000_000,
        1_000_000,
        input_usd_per_million=0.10,
        output_usd_per_million=0.20,
    )
    assert cost == pytest.approx(0.30)
    assert "estimated" in text

    cost_demo, text_demo = estimate_pricing("demo-model", 1_000_000, 1_000_000)
    assert cost_demo == 0.0
    assert "$0.0000 USD" in text_demo

    cost_unknown, text_unknown = estimate_pricing("custom-internal-llm", 1_000_000, 1_000_000)
    assert cost_unknown is None
    assert "unknown" in text_unknown


def test_dry_run_estimation_math(tmp_path: Path) -> None:
    runner = EvaluationRunner(
        fixtures_path=DEFAULT_FIXTURES_PATH,
        output_dir=tmp_path / "eval",
        provider="openai_compatible",
        model_name="specified-model",
        input_usd_per_million=0.10,
        output_usd_per_million=0.20,
        pricing_source="test rate 2026-09-29",
        repeats=3,
    )

    variant_a = tmp_path / "persona_a.md"
    variant_a.write_text("Variant A persona", encoding="utf-8")
    variant_b = tmp_path / "persona_b.md"
    variant_b.write_text("Variant B persona", encoding="utf-8")

    # 1 variant: 12 scenarios * 4 turns * 3 repeats * 1 variant = 144
    estimate_single = runner.estimate_dry_run([("vA", variant_a)])
    assert estimate_single["total_requests"] == 144
    assert estimate_single["estimated_cost_usd"] is not None

    # 2 variants: 12 scenarios * 4 turns * 3 repeats * 2 variants = 288
    estimate_ab = runner.estimate_dry_run([("vA", variant_a), ("vB", variant_b)])
    assert estimate_ab["total_requests"] == 288
    assert estimate_ab["estimated_cost_usd"] == pytest.approx(
        estimate_single["estimated_cost_usd"] * 2
    )


@pytest.mark.asyncio
async def test_execute_deterministic_demo_mode_records_results(tmp_path: Path) -> None:
    output_dir = tmp_path / "eval_run"
    runner = EvaluationRunner(
        fixtures_path=DEFAULT_FIXTURES_PATH,
        output_dir=output_dir,
        provider="demo",
        repeats=1,
    )

    persona = tmp_path / "test_persona.md"
    persona.write_text("你是一个温柔认真的测试角色。", encoding="utf-8")

    samples = await runner.execute(
        variants_to_run=[("test_v1", persona)],
        selected_scenario_ids=["greeting"],
    )

    assert len(samples) == 4
    for idx, sample in enumerate(samples, start=1):
        assert sample.turn_id == idx
        assert sample.scenario_id == "greeting"
        assert sample.variant == "test_v1"
        assert sample.raw_reply
        assert sample.finish_reason == "stop"
        assert sample.tokens_prompt > 0
        assert sample.tokens_completion > 0
        assert sample.latency_ms >= 0

    results_file = output_dir / "results.jsonl"
    assert results_file.exists()
    lines = [
        json.loads(line) for line in results_file.read_text(encoding="utf-8").strip().splitlines()
    ]
    assert len(lines) == 4
    assert lines[0]["scenario_id"] == "greeting"
    assert "sample_key" in lines[0]


@pytest.mark.asyncio
async def test_resume_skips_already_completed_turns(tmp_path: Path) -> None:
    output_dir = tmp_path / "eval_resume"
    persona = tmp_path / "test_persona.md"
    persona.write_text("测试角色", encoding="utf-8")

    runner1 = EvaluationRunner(
        fixtures_path=DEFAULT_FIXTURES_PATH,
        output_dir=output_dir,
        provider="demo",
        repeats=1,
        resume=True,
    )

    samples1 = await runner1.execute(
        variants_to_run=[("v1", persona)],
        selected_scenario_ids=["greeting"],
    )
    assert len(samples1) == 4

    # Run again with resume=True
    runner2 = EvaluationRunner(
        fixtures_path=DEFAULT_FIXTURES_PATH,
        output_dir=output_dir,
        provider="demo",
        repeats=1,
        resume=True,
    )
    samples2 = await runner2.execute(
        variants_to_run=[("v1", persona)],
        selected_scenario_ids=["greeting"],
    )
    assert len(samples2) == 0  # All skipped because already done!


@pytest.mark.asyncio
async def test_max_requests_halts_execution(tmp_path: Path) -> None:
    output_dir = tmp_path / "eval_max_req"
    persona = tmp_path / "test_persona.md"
    persona.write_text("测试角色", encoding="utf-8")

    runner = EvaluationRunner(
        fixtures_path=DEFAULT_FIXTURES_PATH,
        output_dir=output_dir,
        provider="demo",
        repeats=1,
        max_requests=2,  # Stop after 2 requests
    )

    samples = await runner.execute(
        variants_to_run=[("v1", persona)],
        selected_scenario_ids=["greeting"],
    )
    assert len(samples) == 2


@pytest.mark.asyncio
async def test_generate_blinded_review_template_pairs_variants_and_hides_identity(
    tmp_path: Path,
) -> None:
    output_dir = tmp_path / "eval_ab"
    persona_a = tmp_path / "persona_a.md"
    persona_a.write_text("角色A设定", encoding="utf-8")
    persona_b = tmp_path / "persona_b.md"
    persona_b.write_text("角色B设定", encoding="utf-8")

    runner = EvaluationRunner(
        fixtures_path=DEFAULT_FIXTURES_PATH,
        output_dir=output_dir,
        provider="demo",
        repeats=1,
    )

    samples = await runner.execute(
        variants_to_run=[("varA", persona_a), ("varB", persona_b)],
        selected_scenario_ids=["greeting"],
    )
    assert len(samples) == 8  # 4 turns * 2 variants = 8

    template_file = output_dir / "blinded_review_template.md"
    key_file = output_dir / "blinded_key.json"
    assert template_file.exists()
    assert key_file.exists()

    content = template_file.read_text(encoding="utf-8")
    assert "双盲角色场景评估评审表" in content
    assert "候选 1" in content
    assert "候选 2" in content
    assert "普通问候" in content

    # Verify that variant names "varA" and "varB" do NOT appear in the review text table headers
    # Instead, candidate 1 and candidate 2 are used
    keys = json.loads(key_file.read_text(encoding="utf-8"))
    assert "greeting:r0:t1" in keys
    assert set(keys["greeting:r0:t1"].keys()) == {"candidate_1", "candidate_2"}
    assert set(keys["greeting:r0:t1"].values()) == {"varA", "varB"}


@pytest.mark.asyncio
async def test_controlled_provider_deterministic_streaming() -> None:
    provider = ControlledEvaluatorProvider(latency_ms=1)
    from uuid import uuid4

    from chatwaifu_runtime.providers.contracts import LlmRequest, LlmResponseCompleted, LlmTextDelta

    req = LlmRequest(
        generation_id=uuid4(),
        character_name="绫地宁宁",
        user_text="测试输入",
        system_prompt="sys",
    )
    deltas: list[str] = []
    completed = False
    async for event in provider.stream(req):
        if isinstance(event, LlmTextDelta):
            deltas.append(event.text)
        elif isinstance(event, LlmResponseCompleted):  # pyright: ignore[reportUnnecessaryIsInstance]
            completed = True

    assert completed
    full_text = "".join(deltas)
    assert "【绫地宁宁受控回复】" in full_text
    assert "测试输入" in full_text


def test_fixture_validation_rejects_duplicates_and_malformed(tmp_path: Path) -> None:
    # 1. Duplicate scenario ID
    dup_file = tmp_path / "dup.json"
    dup_file.write_text(
        json.dumps(
            [
                {
                    "id": "scenario_1",
                    "name": "S1",
                    "description": "d",
                    "turns": [
                        {
                            "turn_id": 1,
                            "user_text": "hi",
                            "expected_behavior": ["exp"],
                            "forbidden_behavior": ["forb"],
                            "review_criteria": "crit",
                        }
                    ],
                },
                {
                    "id": "scenario_1",
                    "name": "S1 duplicate",
                    "description": "d",
                    "turns": [
                        {
                            "turn_id": 1,
                            "user_text": "hi",
                            "expected_behavior": ["exp"],
                            "forbidden_behavior": ["forb"],
                            "review_criteria": "crit",
                        }
                    ],
                },
            ]
        ),
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="Duplicate scenario ID"):
        load_scenarios(dup_file)

    # 2. Duplicate turn ID within scenario
    dup_turn_file = tmp_path / "dup_turn.json"
    dup_turn_file.write_text(
        json.dumps(
            [
                {
                    "id": "scenario_1",
                    "name": "S1",
                    "description": "d",
                    "turns": [
                        {
                            "turn_id": 1,
                            "user_text": "hi",
                            "expected_behavior": ["exp"],
                            "forbidden_behavior": ["forb"],
                            "review_criteria": "crit",
                        },
                        {
                            "turn_id": 1,
                            "user_text": "hi again",
                            "expected_behavior": ["exp"],
                            "forbidden_behavior": ["forb"],
                            "review_criteria": "crit",
                        },
                    ],
                }
            ]
        ),
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="Duplicate turn_id"):
        load_scenarios(dup_turn_file)


def test_unknown_provider_refuses_execution(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="Unsupported evaluation provider"):
        EvaluationRunner(output_dir=tmp_path / "out", provider="unknown_llm")


@pytest.mark.asyncio
async def test_remote_execution_requires_explicit_bounds(tmp_path: Path) -> None:
    runner = EvaluationRunner(
        output_dir=tmp_path / "out", provider="openai_compatible", model_name="remote-model"
    )
    persona = tmp_path / "persona.md"
    persona.write_text("persona", encoding="utf-8")
    with pytest.raises(ValueError, match="base-url"):
        await runner.execute([("v", persona)], ["greeting"])
    assert not (tmp_path / "out").exists()


def test_remote_dry_run_has_unknown_price_without_supplied_rates(tmp_path: Path) -> None:
    runner = EvaluationRunner(
        output_dir=tmp_path / "out", provider="openai_compatible", model_name="remote-model"
    )
    persona = tmp_path / "persona.md"
    persona.write_text("persona", encoding="utf-8")
    estimate = runner.estimate_dry_run([("v", persona)], ["greeting"])
    assert estimate["estimated_cost_usd"] is None
    assert "unknown" in estimate["estimated_cost_display"]
    assert estimate["estimate_is_billing_cap"] is False


@pytest.mark.asyncio
async def test_estimated_cost_ceiling_stops_before_next_call(tmp_path: Path) -> None:
    persona = tmp_path / "persona.md"
    persona.write_text("persona", encoding="utf-8")
    runner = EvaluationRunner(
        output_dir=tmp_path / "out",
        provider="controlled",
        repeats=1,
        input_usd_per_million=1000.0,
        output_usd_per_million=1000.0,
        pricing_source="synthetic test rate",
        cost_ceiling=0.001,
    )
    samples = await runner.execute([("v", persona)], ["greeting"])
    assert samples == []
    incomplete = (tmp_path / "out" / "incomplete.jsonl").read_text(encoding="utf-8")
    assert "estimated_cost_ceiling" in incomplete


@pytest.mark.asyncio
async def test_identical_synthetic_inputs_have_stable_prompt_token_counts(tmp_path: Path) -> None:
    persona = tmp_path / "persona.md"
    persona.write_text("persona", encoding="utf-8")
    first = await EvaluationRunner(
        output_dir=tmp_path / "first", provider="controlled", repeats=1
    ).execute([("v", persona)], ["insufficient_shared_history"])
    second = await EvaluationRunner(
        output_dir=tmp_path / "second", provider="controlled", repeats=1
    ).execute([("v", persona)], ["insufficient_shared_history"])
    assert [(s.sample_key, s.tokens_prompt, s.raw_reply) for s in first] == [
        (s.sample_key, s.tokens_prompt, s.raw_reply) for s in second
    ]


@pytest.mark.asyncio
async def test_resume_replays_prior_replies_into_later_prompts(tmp_path: Path) -> None:
    persona = tmp_path / "persona.md"
    persona.write_text("persona", encoding="utf-8")
    clean = await EvaluationRunner(
        output_dir=tmp_path / "clean", provider="controlled", repeats=1
    ).execute([("v", persona)], ["greeting"])
    partial_dir = tmp_path / "partial"
    partial = await EvaluationRunner(
        output_dir=partial_dir, provider="controlled", repeats=1, max_requests=2
    ).execute([("v", persona)], ["greeting"])
    assert [sample.turn_id for sample in partial] == [1, 2]
    resumed = await EvaluationRunner(
        output_dir=partial_dir, provider="controlled", repeats=1
    ).execute([("v", persona)], ["greeting"])
    assert [sample.turn_id for sample in resumed] == [3, 4]
    assert [(s.tokens_prompt, s.raw_reply) for s in resumed] == [
        (s.tokens_prompt, s.raw_reply) for s in clean[2:]
    ]
    rows = [
        json.loads(line)
        for line in (partial_dir / "results.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    assert len(rows) == len({row["sample_key"] for row in rows}) == 4


@pytest.mark.asyncio
async def test_resume_rejects_persona_change(tmp_path: Path) -> None:
    output_dir = tmp_path / "out"
    persona = tmp_path / "persona.md"
    persona.write_text("first", encoding="utf-8")
    await EvaluationRunner(output_dir=output_dir, provider="controlled", repeats=1).execute(
        [("v", persona)], ["greeting"]
    )
    persona.write_text("second", encoding="utf-8")
    with pytest.raises(ValueError, match="resume inputs"):
        await EvaluationRunner(output_dir=output_dir, provider="controlled", repeats=1).execute(
            [("v", persona)], ["greeting"]
        )


@pytest.mark.asyncio
async def test_resume_retries_legacy_timeout_record(tmp_path: Path) -> None:
    output_dir = tmp_path / "out"
    persona = tmp_path / "persona.md"
    persona.write_text("persona", encoding="utf-8")
    first = await EvaluationRunner(
        output_dir=output_dir, provider="controlled", repeats=1, max_requests=1
    ).execute([("v", persona)], ["greeting"])
    assert len(first) == 1
    rows_path = output_dir / "results.jsonl"
    first_row = json.loads(rows_path.read_text(encoding="utf-8").strip())
    timeout_row = {
        **first_row,
        "sample_key": "greeting:r0:t2:v",
        "turn_id": 2,
        "raw_reply": "partial output",
        "finish_reason": "timeout",
    }
    with rows_path.open("a", encoding="utf-8") as out:
        out.write(json.dumps(timeout_row, ensure_ascii=False) + "\n")
    resumed = await EvaluationRunner(
        output_dir=output_dir, provider="controlled", repeats=1
    ).execute([("v", persona)], ["greeting"])
    assert [sample.turn_id for sample in resumed] == [2, 3, 4]
    assert resumed[0].finish_reason == "stop"
