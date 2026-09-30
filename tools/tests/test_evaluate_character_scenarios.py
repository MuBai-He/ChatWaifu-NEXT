"""Unit and integration tests for tools/evaluate_character_scenarios.py."""

# pyright: reportPrivateUsage=false
from __future__ import annotations

import json
import sys
from copy import deepcopy
from pathlib import Path
from typing import Any

import pytest
from chatwaifu_protocol.character import (
    RelationshipState,
)
from chatwaifu_runtime.character_kernel.prompt import PromptCompilation, PromptCompiler
from chatwaifu_runtime.character_kernel.service import (
    _classify,
    _reduce_affect,
    _reduce_relationship,
)
from chatwaifu_runtime.characters.service import CharacterService
from chatwaifu_runtime.providers.contracts import (
    LlmRequest,
    LlmResponseCompleted,
    LlmStreamEvent,
    LlmTextDelta,
    LlmUsage,
)

from tools.evaluate_character_scenarios import (
    _FIXED_TIME,
    DEFAULT_CHARACTERS_DIR,
    DEFAULT_FIXTURES_PATH,
    ControlledEvaluatorProvider,
    EvaluatedSample,
    EvaluationRunner,
    _read_completed_records,
    build_arg_parser,
    estimate_pricing,
    load_scenarios,
    main,
    parse_and_validate_initial_affect,
    parse_and_validate_initial_relationship,
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


def test_runtime_source_mode_requires_explicit_permission_and_provider_round_bound(
    tmp_path: Path,
) -> None:
    args = build_arg_parser().parse_args(
        [
            "--runtime-source-tools",
            "--allow-source-tools-once",
            "--max-provider-requests",
            "12",
            "--source-dns-resolver",
            "cloudflare",
        ]
    )
    assert args.runtime_source_tools and args.allow_source_tools_once
    assert args.max_provider_requests == 12
    with pytest.raises(ValueError, match="allow-source-tools-once"):
        EvaluationRunner(output_dir=tmp_path, runtime_source_tools=True, max_provider_requests=12)
    with pytest.raises(ValueError, match="max-provider-requests"):
        EvaluationRunner(
            output_dir=tmp_path, runtime_source_tools=True, allow_source_tools_once=True
        )
    with pytest.raises(ValueError, match="no-cost-ceiling"):
        EvaluationRunner(
            output_dir=tmp_path,
            provider="openai_compatible",
            runtime_source_tools=True,
            allow_source_tools_once=True,
            max_provider_requests=12,
        )


@pytest.mark.asyncio
async def test_runtime_mode_preserves_trace_and_refuses_direct_path_resume(tmp_path: Path) -> None:
    runner = EvaluationRunner(
        output_dir=tmp_path,
        provider="demo",
        repeats=1,
        max_requests=1,
        runtime_source_tools=True,
        allow_source_tools_once=True,
        max_provider_requests=5,
    )
    variants = [("baseline", runner.variant_a_persona_path)]
    samples = await runner.execute(variants, ["greeting"])
    assert len(samples) == 1
    assert samples[0].runtime_source_trace is not None
    assert samples[0].runtime_source_trace["execution_path"] == "runtime_source_tools"
    assert len(samples[0].runtime_source_trace["provider_calls"]) == 1
    identity = json.loads((tmp_path / "metadata.json").read_text(encoding="utf-8"))["identity"]
    assert identity["execution_path"] == "runtime_source_tools"
    assert identity["runtime_source_config"]["permission_policy"] == "allow_once"
    assert identity["runtime_source_config"]["implementation_sha256"]
    journal = tmp_path / "provider-rounds.jsonl"
    saved_journal = journal.read_text(encoding="utf-8")
    journal.unlink()
    with pytest.raises(ValueError, match="provider-rounds"):
        await runner.execute(variants, ["greeting"])
    journal.write_text(saved_journal, encoding="utf-8")
    direct = EvaluationRunner(output_dir=tmp_path, provider="demo", repeats=1, max_requests=1)
    with pytest.raises(ValueError, match="differ"):
        await direct.execute(variants, ["greeting"])


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


def test_dry_run_prices_each_selected_persona(tmp_path: Path) -> None:
    runner = EvaluationRunner(
        output_dir=tmp_path / "unused_output",
        provider="openai_compatible",
        model_name="specified-model",
        input_usd_per_million=0.10,
        output_usd_per_million=0.20,
        pricing_source="synthetic test rates",
        repeats=3,
    )
    short_persona = tmp_path / "short.md"
    short_persona.write_text("你是温柔的角色。", encoding="utf-8")
    long_persona = tmp_path / "long.md"
    long_persona.write_text("听完问题后认真回答，保持角色口吻。" * 30, encoding="utf-8")

    short = runner.estimate_dry_run([("short", short_persona)], ["greeting", "technical_help"])
    long = runner.estimate_dry_run([("long", long_persona)], ["greeting", "technical_help"])
    paired = runner.estimate_dry_run(
        [("short", short_persona), ("long", long_persona)], ["greeting", "technical_help"]
    )

    assert long["estimated_prompt_tokens"] > short["estimated_prompt_tokens"]
    assert paired["estimated_prompt_tokens"] == (
        short["estimated_prompt_tokens"] + long["estimated_prompt_tokens"]
    )
    assert paired["estimated_completion_tokens"] == (
        short["estimated_completion_tokens"] + long["estimated_completion_tokens"]
    )
    assert paired["estimated_cost_usd"] == pytest.approx(
        short["estimated_cost_usd"] + long["estimated_cost_usd"]
    )
    assert paired["total_requests"] == 48
    assert "without later-turn history" in paired["prompt_estimate_scope"]
    assert not runner.output_dir.exists()


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
    assert "匿名配对角色场景评审表" in content
    assert "不保证独立盲评" in content
    assert "- 耗时:" not in content
    assert "Provider Compl:" not in content
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
async def test_review_preserves_nested_fences_and_saved_labels(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    choices = iter((False, True, False, True))

    def choose_label(_: Any) -> bool:
        return next(choices)

    monkeypatch.setattr("tools.evaluate_character_scenarios.secrets.choice", choose_label)
    output_dir = tmp_path / "eval_review_fences"
    runner = EvaluationRunner(
        fixtures_path=DEFAULT_FIXTURES_PATH,
        output_dir=output_dir,
        provider="demo",
        repeats=1,
    )
    persona_a = tmp_path / "persona_a.md"
    persona_b = tmp_path / "persona_b.md"
    persona_a.write_text("Persona A", encoding="utf-8")
    persona_b.write_text("Persona B", encoding="utf-8")
    await runner.execute([("vA", persona_a), ("vB", persona_b)], ["greeting"])
    key_path = output_dir / "blinded_key.json"
    saved_key_bytes = key_path.read_bytes()
    keys = json.loads(saved_key_bytes)
    assert keys["greeting:r0:t1"]["candidate_1"] == "vA"
    assert keys["greeting:r0:t2"]["candidate_1"] == "vB"

    # A reply that used to close the review's outer fence and inject headings.
    reply = "完整代码:\n```python\nprint('hello')\n```\n````\n## 仍属于模型回复\n````"
    results_path = output_dir / "results.jsonl"
    rows = [json.loads(line) for line in results_path.read_text(encoding="utf-8").splitlines()]
    rows[0]["raw_reply"] = reply
    rows[0]["latency_ms"] = 987654321
    rows[0]["provider_tokens_completion"] = 123456789
    results_path.write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows), encoding="utf-8"
    )
    raw_bytes = results_path.read_bytes()

    def refuse_new_labels(_: Any) -> bool:
        raise AssertionError("Regeneration must reuse the already saved labels")

    monkeypatch.setattr("tools.evaluate_character_scenarios.secrets.choice", refuse_new_labels)
    template = runner.generate_blinded_review_template("vA", "vB").read_text(encoding="utf-8")
    assert f"`````text\n{reply}\n`````" in template
    assert "987654321" not in template
    assert "123456789" not in template
    assert key_path.read_bytes() == saved_key_bytes
    assert results_path.read_bytes() == raw_bytes

    invalid_keys: dict[str, Any] = dict(keys)
    for bad_pair in (None, {"candidate_1": "unrelated_version", "candidate_2": "vB"}):
        invalid_keys["greeting:r0:t1"] = bad_pair
        key_path.write_text(json.dumps(invalid_keys), encoding="utf-8")
        with pytest.raises(ValueError, match="saved review key does not match variants"):
            runner.generate_blinded_review_template("vA", "vB")


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


def test_cli_argument_parser_accepts_no_cost_ceiling() -> None:
    parser = build_arg_parser()
    args = parser.parse_args(["--no-cost-ceiling", "--execute", "--max-requests", "10"])
    assert args.no_cost_ceiling is True
    assert args.execute is True
    assert args.max_requests == 10


def test_cli_reports_partial_run_and_completed_resume(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    persona = tmp_path / "persona.md"
    persona.write_text("测试角色", encoding="utf-8")
    output_dir = tmp_path / "eval"
    args = [
        "evaluate_character_scenarios.py",
        "--execute",
        "--provider",
        "controlled",
        "--scenario",
        "greeting",
        "--repeats",
        "1",
        "--persona",
        str(persona),
        "--output-dir",
        str(output_dir),
    ]
    monkeypatch.setattr(sys, "argv", [*args, "--max-requests", "2"])
    assert main() == 2

    monkeypatch.setattr(sys, "argv", [*args, "--max-requests", "4"])
    assert main() == 0
    assert main() == 0  # A fully resumed run has zero new calls but is complete.


def test_unpriced_remote_validation_and_contradictions(tmp_path: Path) -> None:
    persona = tmp_path / "persona.md"
    persona.write_text("persona", encoding="utf-8")

    # 1. no-cost-ceiling requires max-requests
    r1 = EvaluationRunner(
        output_dir=tmp_path / "o1",
        provider="openai_compatible",
        model_name="remote-model",
        base_url="https://api.test",
        no_cost_ceiling=True,
    )
    with pytest.raises(ValueError, match="requires explicit --max-requests"):
        r1._validate_execution()

    # 2. Cannot combine --no-cost-ceiling with --cost-ceiling
    with pytest.raises(ValueError, match="cannot combine --no-cost-ceiling with --cost-ceiling"):
        EvaluationRunner(
            output_dir=tmp_path / "o2",
            provider="openai_compatible",
            model_name="remote-model",
            base_url="https://api.test",
            no_cost_ceiling=True,
            cost_ceiling=5.0,
            max_requests=10,
        )

    # 3. Cannot combine --no-cost-ceiling with explicit pricing rates
    r3 = EvaluationRunner(
        output_dir=tmp_path / "o3",
        provider="openai_compatible",
        model_name="remote-model",
        base_url="https://api.test",
        no_cost_ceiling=True,
        input_usd_per_million=0.5,
        max_requests=10,
    )
    with pytest.raises(ValueError, match="cannot combine --no-cost-ceiling with explicit pricing"):
        r3._validate_execution()

    # 4. Remote without either --cost-ceiling or --no-cost-ceiling
    r4 = EvaluationRunner(
        output_dir=tmp_path / "o4",
        provider="openai_compatible",
        model_name="remote-model",
        base_url="https://api.test",
        max_requests=10,
    )
    with pytest.raises(ValueError, match="either --cost-ceiling or --no-cost-ceiling"):
        r4._validate_execution()

    # 5. Valid no-cost-ceiling remote configuration passes validation
    r5 = EvaluationRunner(
        output_dir=tmp_path / "o5",
        provider="openai_compatible",
        model_name="gemini-3.8-flash-high",
        base_url="https://api.test",
        no_cost_ceiling=True,
        max_requests=10,
    )
    r5._validate_execution()  # No exception raised


def test_dry_run_with_no_cost_ceiling(tmp_path: Path) -> None:
    runner = EvaluationRunner(
        output_dir=tmp_path / "out",
        provider="openai_compatible",
        model_name="gemini-3.8-flash-high",
        base_url="https://api.test",
        no_cost_ceiling=True,
        max_requests=10,
    )
    persona = tmp_path / "persona.md"
    persona.write_text("persona", encoding="utf-8")
    estimate = runner.estimate_dry_run([("v", persona)], ["greeting"])

    assert estimate["estimated_cost_usd"] is None
    assert "unpriced" in estimate["estimated_cost_display"]
    assert "user authorized --no-cost-ceiling" in estimate["estimated_cost_display"]
    assert estimate["cost_ceiling_usd"] is None
    assert estimate["cost_policy"] == "no_cost_ceiling"
    assert "adapter retries may cause up to 3 HTTP attempts" in estimate["notice"]


@pytest.mark.asyncio
async def test_resume_metadata_guard_prevents_cost_policy_tampering(tmp_path: Path) -> None:
    output_dir = tmp_path / "out"
    persona = tmp_path / "persona.md"
    persona.write_text("persona", encoding="utf-8")

    # Run with no_cost_ceiling
    r1 = EvaluationRunner(
        output_dir=output_dir,
        provider="controlled",
        repeats=1,
        max_requests=1,
        no_cost_ceiling=True,
    )
    samples1 = await r1.execute([("v", persona)], ["greeting"])
    assert len(samples1) == 1

    metadata = json.loads((output_dir / "metadata.json").read_text(encoding="utf-8"))
    assert metadata["identity"]["cost_policy"] == "no_cost_ceiling"
    assert metadata["identity"]["cost_ceiling"] is None

    # Resume with cost_ceiling=1.0 (policy mismatch)
    r2 = EvaluationRunner(
        output_dir=output_dir,
        provider="controlled",
        repeats=1,
        cost_ceiling=1.0,
        no_cost_ceiling=False,
    )
    with pytest.raises(ValueError, match="resume inputs or model configuration differ"):
        await r2.execute([("v", persona)], ["greeting"])


@pytest.mark.asyncio
async def test_legacy_metadata_resume_guard(tmp_path: Path) -> None:
    output_dir = tmp_path / "out"
    persona = tmp_path / "persona.md"
    persona.write_text("persona", encoding="utf-8")

    r1 = EvaluationRunner(
        output_dir=output_dir,
        provider="controlled",
        repeats=1,
        max_requests=1,
    )
    samples1 = await r1.execute([("v", persona)], ["greeting"])
    assert len(samples1) == 1

    # Simulate legacy metadata without cost_policy key
    meta_path = output_dir / "metadata.json"
    meta_data = json.loads(meta_path.read_text(encoding="utf-8"))
    meta_data["identity"].pop("cost_policy", None)
    meta_data["identity"].pop("cost_ceiling", None)
    meta_path.write_text(json.dumps(meta_data, ensure_ascii=False, indent=2), encoding="utf-8")

    # Attempting to resume with --no-cost-ceiling is rejected
    r_no_ceiling = EvaluationRunner(
        output_dir=output_dir,
        provider="controlled",
        repeats=1,
        no_cost_ceiling=True,
    )
    with pytest.raises(ValueError, match="cannot resume legacy capped run with --no-cost-ceiling"):
        await r_no_ceiling.execute([("v", persona)], ["greeting"])

    # Resuming with standard capped/demo mode succeeds
    r_legacy_compat = EvaluationRunner(
        output_dir=output_dir,
        provider="controlled",
        repeats=1,
    )
    resumed = await r_legacy_compat.execute([("v", persona)], ["greeting"])
    assert [s.turn_id for s in resumed] == [2, 3, 4]


def test_legacy_demo_results_jsonl_loads_compatibly() -> None:
    results_path = (
        Path(__file__).resolve().parents[2]
        / "docs"
        / "research"
        / "qq-agent-plus-evidence"
        / "character_scenarios_ab_demo_v2"
        / "results.jsonl"
    )
    assert results_path.exists()
    records = _read_completed_records(results_path)
    assert len(records) == 288

    for sample in records.values():
        assert isinstance(sample, EvaluatedSample)
        assert sample.tokens_source == "estimated"
        assert sample.tokens_prompt > 0
        assert sample.tokens_completion > 0
        assert sample.provider_tokens_prompt is None
        assert sample.provider_tokens_completion is None
        assert sample.provider_tokens_total is None
        assert sample.provider_tokens_reasoning is None
        assert sample.finish_reason == "stop"


@pytest.mark.asyncio
async def test_provider_usage_persists_alongside_estimates_in_results_jsonl(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from collections.abc import AsyncIterator

    from chatwaifu_runtime.providers.contracts import (
        LlmResponseCompleted,
        LlmTextDelta,
    )

    class _MockUsageProvider:
        kind = "openai_compatible"
        supports_tool_calling = False

        def __init__(self, **_kwargs: object) -> None:
            pass

        async def stream(self, _request: LlmRequest) -> AsyncIterator[LlmStreamEvent]:
            yield LlmTextDelta("模型回复测试")
            yield LlmResponseCompleted(
                "stop",
                usage=LlmUsage(
                    prompt_tokens=5,
                    completion_tokens=1,
                    total_tokens=111,
                    reasoning_tokens=105,
                ),
            )

        async def close(self) -> None:
            pass

    monkeypatch.setattr(
        "tools.evaluate_character_scenarios.OpenAiCompatibleLlmProvider",
        _MockUsageProvider,
    )

    output_dir = tmp_path / "eval_usage"
    runner = EvaluationRunner(
        fixtures_path=DEFAULT_FIXTURES_PATH,
        output_dir=output_dir,
        provider="openai_compatible",
        model_name="gemini-3.8-flash-high",
        base_url="https://example.test",
        max_requests=1,
        no_cost_ceiling=True,
    )

    persona = tmp_path / "persona.md"
    persona.write_text("测试角色人设", encoding="utf-8")

    samples = await runner.execute([("test_variant", persona)], ["greeting"])
    assert len(samples) == 1
    sample = samples[0]

    # Verify provider-reported fields
    assert sample.tokens_source == "provider_reported"
    assert sample.provider_tokens_prompt == 5
    assert sample.provider_tokens_completion == 1
    assert sample.provider_tokens_total == 111
    assert sample.provider_tokens_reasoning == 105

    # Verify local estimates exist separately and match compilation
    assert sample.tokens_prompt > 0
    assert sample.tokens_completion > 0
    assert sample.estimated_tokens_prompt == sample.tokens_prompt
    assert sample.estimated_tokens_completion == sample.tokens_completion

    # Verify file round-trip
    records = _read_completed_records(output_dir / "results.jsonl")
    assert len(records) == 1
    saved = records[sample.sample_key]
    assert saved.tokens_source == "provider_reported"
    assert saved.provider_tokens_prompt == 5
    assert saved.provider_tokens_completion == 1
    assert saved.provider_tokens_total == 111
    assert saved.provider_tokens_reasoning == 105
    assert saved.tokens_prompt == sample.tokens_prompt
    assert saved.tokens_completion == sample.tokens_completion


def test_red_before_green_after_familiar_state_trajectories() -> None:
    """Verify red-before vs green-after behavior across real reducer and compiler."""
    chars = CharacterService(DEFAULT_CHARACTERS_DIR)
    chars.start()
    char = chars.get("default")
    assert char is not None

    scenarios = load_scenarios(DEFAULT_FIXTURES_PATH)
    assert len(scenarios) == 12

    # Red before simulation:
    # Under old fixture, greeting declared relationship_stage="familiar",
    # but metrics were default (count=0, familiarity=0.2).
    old_greeting_rel = RelationshipState(
        stage="familiar",
        familiarity=0.2,
        trust=0.2,
        affinity=0.25,
        comfort=0.2,
        recent_tension=0.0,
        interaction_count=0,
        updated_at=_FIXED_TIME,
    )
    # Turn 1 user input: "晚上好，今天过得怎么样？"
    signal_t1 = _classify("晚上好，今天过得怎么样？")
    old_t1_reduced = _reduce_relationship(old_greeting_rel, signal_t1, char, _FIXED_TIME)
    # RED: In old run, stage dropped from familiar to acquaintance on turn 1!
    assert old_t1_reduced.interaction_count == 1
    assert old_t1_reduced.familiarity == pytest.approx(0.212)
    assert old_t1_reduced.stage == "acquaintance"

    # Green after verification:
    # All 12 scenarios with new seed metrics maintain their declared stages across all 4 turns!
    familiar_count = 0
    acquaintance_count = 0

    for s in scenarios:
        init_st = s.initial_state
        init_rel = parse_and_validate_initial_relationship(
            init_st, character=char, now=_FIXED_TIME, scenario_id=s.id
        )
        target_stage = init_rel.stage
        if target_stage == "familiar":
            familiar_count += 1
            assert init_rel.interaction_count == 6
            assert init_rel.familiarity == pytest.approx(0.38)
            assert init_rel.trust == pytest.approx(0.35)
            assert init_rel.affinity == pytest.approx(0.35)
            assert init_rel.comfort == pytest.approx(0.35)
        else:
            acquaintance_count += 1
            assert init_rel.interaction_count == 0
            assert init_rel.familiarity == pytest.approx(0.2)
            assert init_rel.trust == pytest.approx(0.2)
            assert init_rel.affinity == pytest.approx(0.25)
            assert init_rel.comfort == pytest.approx(0.2)

        cur_rel = init_rel
        cur_affect = parse_and_validate_initial_affect(init_st, now=_FIXED_TIME, scenario_id=s.id)
        for turn in s.turns:
            signal = _classify(turn.user_text)
            cur_affect = _reduce_affect(cur_affect, signal, _FIXED_TIME)
            cur_rel = _reduce_relationship(cur_rel, signal, char, _FIXED_TIME)
            assert cur_rel.stage == target_stage, (
                f"Scenario {s.id} turn {turn.turn_id} diverged from "
                f"{target_stage} to {cur_rel.stage}"
            )

    assert familiar_count == 7
    assert acquaintance_count == 5


@pytest.mark.asyncio
async def test_preflight_validates_all_scenarios_before_paid_calls_and_conditional_negative(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Preflight catches contradictory or invalid fixtures before ANY model call executes."""
    chars = CharacterService(DEFAULT_CHARACTERS_DIR)
    chars.start()
    char = chars.get("default")
    assert char is not None

    stream_call_count = 0

    class _CountingProvider:
        kind = "controlled"
        supports_tool_calling = False

        async def stream(self, request: LlmRequest):
            nonlocal stream_call_count
            stream_call_count += 1
            yield LlmTextDelta("reply")
            yield LlmResponseCompleted("stop")

    def _make_counting_provider(*_args: object, **_kwargs: object) -> object:
        return _CountingProvider()

    monkeypatch.setattr(
        "tools.evaluate_character_scenarios.ControlledEvaluatorProvider",
        _make_counting_provider,
    )

    # Create a fixture file with a valid scenario followed by an invalid contradictory scenario
    fixtures_file = tmp_path / "mixed_fixtures.json"
    scenarios_data = [
        {
            "id": "greeting_valid",
            "name": "有效问候",
            "description": "有效熟悉场景",
            "initial_state": {
                "relationship_stage": "familiar",
                "stage": "familiar",
                "interaction_count": 6,
                "count": 6,
                "familiarity": 0.38,
                "trust": 0.35,
                "affinity": 0.35,
                "comfort": 0.35,
                "recent_tension": 0.0,
                "valence": 0.2,
                "arousal": 0.3,
            },
            "synthetic_memory": [],
            "seed_history": [],
            "presentation_profile": "instant_message",
            "turns": [
                {
                    "turn_id": i,
                    "user_text": f"输入 {i}",
                    "expected_behavior": ["预期"],
                    "forbidden_behavior": ["禁止"],
                    "review_criteria": "标准",
                }
                for i in range(1, 5)
            ],
        },
        {
            "id": "invalid_contradictory",
            "name": "矛盾场景",
            "description": "声明 familiar 但缺少指标，属于旧缺陷 fixture",
            "initial_state": {
                "relationship_stage": "familiar",
                "valence": 0.2,
                "arousal": 0.3,
            },
            "synthetic_memory": [],
            "seed_history": [],
            "presentation_profile": "instant_message",
            "turns": [
                {
                    "turn_id": i,
                    "user_text": f"输入 {i}",
                    "expected_behavior": ["预期"],
                    "forbidden_behavior": ["禁止"],
                    "review_criteria": "标准",
                }
                for i in range(1, 5)
            ],
        },
    ]
    fixtures_file.write_text(json.dumps(scenarios_data, ensure_ascii=False), encoding="utf-8")

    output_dir = tmp_path / "eval_conditional_negative"
    runner = EvaluationRunner(
        fixtures_path=fixtures_file,
        output_dir=output_dir,
        provider="controlled",
        repeats=1,
    )

    persona = tmp_path / "persona.md"
    persona.write_text("Persona", encoding="utf-8")

    # Order: valid scenario first, invalid scenario second
    with pytest.raises(ValueError, match="contradicts character policy stage"):
        await runner.execute([("variant_a", persona)], ["greeting_valid", "invalid_contradictory"])

    # CRITICAL: Prove NO provider calls were made, even for the valid scenario!
    assert stream_call_count == 0

    # Also prove dry run catches it
    with pytest.raises(ValueError, match="contradicts character policy stage"):
        runner.estimate_dry_run(
            [("variant_a", persona)], ["greeting_valid", "invalid_contradictory"]
        )

    # Test legacy support for valid acquaintance without explicit metrics
    legacy_acq_st = {"relationship_stage": "acquaintance", "valence": 0.1, "arousal": 0.2}
    rel_acq = parse_and_validate_initial_relationship(
        legacy_acq_st, character=char, now=_FIXED_TIME
    )
    assert rel_acq.stage == "acquaintance"
    assert rel_acq.interaction_count == 0

    # Test invalid out-of-range metrics
    with pytest.raises(ValueError, match="invalid initial relationship state"):
        parse_and_validate_initial_relationship(
            {"familiarity": 1.5, "stage": "acquaintance"}, character=char, now=_FIXED_TIME
        )

    # Test conflicting count keys
    with pytest.raises(ValueError, match="conflicting count"):
        parse_and_validate_initial_relationship(
            {"count": 6, "interaction_count": 5, "stage": "familiar"},
            character=char,
            now=_FIXED_TIME,
        )

    # Test conflicting stage keys
    with pytest.raises(ValueError, match="conflicting stage"):
        parse_and_validate_initial_relationship(
            {"stage": "familiar", "relationship_stage": "acquaintance"},
            character=char,
            now=_FIXED_TIME,
        )


@pytest.mark.asyncio
async def test_input_snapshot_schema_type_version_and_serialization(tmp_path: Path) -> None:
    """Validate typed input_snapshot structure, versioning, and legacy deserialization."""
    output_dir = tmp_path / "eval_snapshot"
    runner = EvaluationRunner(
        fixtures_path=DEFAULT_FIXTURES_PATH,
        output_dir=output_dir,
        provider="demo",
        repeats=1,
    )
    persona = tmp_path / "persona.md"
    persona.write_text("测试角色设定", encoding="utf-8")

    samples = await runner.execute([("baseline", persona)], ["greeting"])
    assert len(samples) == 4

    for sample in samples:
        snap = sample.input_snapshot
        assert snap is not None
        assert snap["version"] == "1.0"
        assert snap["kernel"]["revision"] == sample.turn_id
        assert snap["kernel"]["character_id"] == "default"
        assert snap["kernel"]["user_scope"] == "local"

        rel = snap["kernel"]["relationship"]
        assert rel["stage"] == "familiar"
        assert rel["interaction_count"] == 6 + sample.turn_id
        assert rel["familiarity"] > 0.38
        assert rel["updated_at"] == "2026-01-01T00:00:00Z"

        plan = snap["plan"]
        assert plan["intent"]
        assert plan["tone"]
        assert plan["expression"]
        assert plan["rationale"]

    # Verify JSONL on-disk serialization roundtrip
    records = _read_completed_records(output_dir / "results.jsonl")
    for _key, rec in records.items():
        assert rec.input_snapshot is not None
        assert rec.input_snapshot["version"] == "1.0"
        assert rec.input_snapshot["kernel"]["relationship"]["stage"] == "familiar"

    # Test legacy row missing input_snapshot
    legacy_file = tmp_path / "legacy_results.jsonl"
    legacy_row: dict[str, Any] = {
        "sample_key": "greeting:r0:t1:baseline",
        "scenario_id": "greeting",
        "scenario_name": "普通问候",
        "turn_id": 1,
        "repeat_index": 0,
        "variant": "baseline",
        "persona_hash": "abcd",
        "provider": "demo",
        "model": "demo-model",
        "presentation_profile": "instant_message",
        "user_text": "你好",
        "raw_reply": "回复",
        "latency_ms": 10,
        "tokens_prompt": 100,
        "tokens_completion": 50,
        "finish_reason": "stop",
        "expected_behavior": [],
        "forbidden_behavior": [],
        "review_criteria": "",
        "timestamp": "2026-01-01T00:00:00Z",
    }
    legacy_file.write_text(json.dumps(legacy_row) + "\n", encoding="utf-8")
    loaded_legacy = _read_completed_records(legacy_file)
    assert "greeting:r0:t1:baseline" in loaded_legacy
    assert loaded_legacy["greeting:r0:t1:baseline"].input_snapshot is None

    valid_row = json.loads(
        (output_dir / "results.jsonl").read_text(encoding="utf-8").splitlines()[0]
    )
    for corruption in ("version", "missing_version", "count", "time", "plan", "extra"):
        malformed = deepcopy(valid_row)
        snapshot = malformed["input_snapshot"]
        if corruption == "version":
            snapshot["version"] = "2.0"
        elif corruption == "missing_version":
            del snapshot["version"]
        elif corruption == "count":
            snapshot["kernel"]["relationship"]["interaction_count"] = -1
        elif corruption == "time":
            snapshot["kernel"]["relationship"]["updated_at"] = "2026-01-01T00:00:00"
        elif corruption == "plan":
            snapshot["plan"]["intent"] = "invented"
        else:
            snapshot["extra"] = "unexpected"
        legacy_file.write_text(json.dumps(malformed) + "\n", encoding="utf-8")
        with pytest.raises(ValueError, match="Invalid recorded input_snapshot"):
            _read_completed_records(legacy_file)


@pytest.mark.parametrize("stage", [None, "", "friend", 0, False])
def test_explicit_invalid_stage_does_not_fall_back_to_acquaintance(stage: object) -> None:
    characters = CharacterService(DEFAULT_CHARACTERS_DIR)
    characters.start()
    character = characters.get("default")
    assert character is not None
    with pytest.raises(ValueError, match="invalid initial relationship state"):
        parse_and_validate_initial_relationship({"relationship_stage": stage}, character=character)


@pytest.mark.asyncio
async def test_snapshot_parity_uninterrupted_vs_resumed(tmp_path: Path) -> None:
    """Verify snapshot parity between uninterrupted run and resumed run."""
    dir_uninterrupted = tmp_path / "eval_uninterrupted"
    dir_resumed = tmp_path / "eval_resumed"
    persona = tmp_path / "persona.md"
    persona.write_text("人设文本", encoding="utf-8")

    # 1. Uninterrupted run (all 4 turns)
    runner_full = EvaluationRunner(
        fixtures_path=DEFAULT_FIXTURES_PATH,
        output_dir=dir_uninterrupted,
        provider="demo",
        repeats=1,
    )
    samples_full = await runner_full.execute([("v", persona)], ["greeting"])
    assert len(samples_full) == 4

    # 2. Interrupted run: only 2 requests allowed
    runner_part1 = EvaluationRunner(
        fixtures_path=DEFAULT_FIXTURES_PATH,
        output_dir=dir_resumed,
        provider="demo",
        repeats=1,
        max_requests=2,
    )
    samples_part1 = await runner_part1.execute([("v", persona)], ["greeting"])
    assert len(samples_part1) == 2

    # 3. Resume run: finish remaining 2 turns
    runner_part2 = EvaluationRunner(
        fixtures_path=DEFAULT_FIXTURES_PATH,
        output_dir=dir_resumed,
        provider="demo",
        repeats=1,
        resume=True,
    )
    samples_part2 = await runner_part2.execute([("v", persona)], ["greeting"])
    assert len(samples_part2) == 2

    records_full = _read_completed_records(dir_uninterrupted / "results.jsonl")
    records_resumed = _read_completed_records(dir_resumed / "results.jsonl")
    assert len(records_full) == 4
    assert len(records_resumed) == 4

    for turn_id in (1, 2, 3, 4):
        key = f"greeting:r0:t{turn_id}:v"
        full_snap = records_full[key].input_snapshot
        resumed_snap = records_resumed[key].input_snapshot
        assert full_snap == resumed_snap, f"Parity mismatch on turn {turn_id}"


@pytest.mark.asyncio
async def test_snapshot_and_plan_parity_ab_and_repeats(tmp_path: Path) -> None:
    """Verify state and plan parity across A/B variants and across repeat runs."""
    output_dir = tmp_path / "eval_parity"
    persona_a = tmp_path / "persona_a.md"
    persona_a.write_text("Persona Variant A 强调温柔", encoding="utf-8")
    persona_b = tmp_path / "persona_b.md"
    persona_b.write_text("Persona Variant B 强调内向害羞", encoding="utf-8")

    runner = EvaluationRunner(
        fixtures_path=DEFAULT_FIXTURES_PATH,
        output_dir=output_dir,
        provider="demo",
        repeats=3,
    )
    samples = await runner.execute(
        [("varA", persona_a), ("varB", persona_b)],
        ["greeting"],
    )
    # 4 turns * 3 repeats * 2 variants = 24
    assert len(samples) == 24

    records = _read_completed_records(output_dir / "results.jsonl")

    for turn_id in (1, 2, 3, 4):
        # A/B parity within repeat 0
        key_a = f"greeting:r0:t{turn_id}:varA"
        key_b = f"greeting:r0:t{turn_id}:varB"
        snap_a = records[key_a].input_snapshot
        snap_b = records[key_b].input_snapshot
        assert snap_a is not None and snap_b is not None

        # Relationship state metrics must be identical
        assert snap_a["kernel"]["relationship"] == snap_b["kernel"]["relationship"]
        # ResponsePlan must be identical
        assert snap_a["plan"] == snap_b["plan"]

        # Repeats parity (r0 vs r1 vs r2 for varA)
        key_r0 = f"greeting:r0:t{turn_id}:varA"
        key_r1 = f"greeting:r1:t{turn_id}:varA"
        key_r2 = f"greeting:r2:t{turn_id}:varA"
        snap_r0 = records[key_r0].input_snapshot
        snap_r1 = records[key_r1].input_snapshot
        snap_r2 = records[key_r2].input_snapshot
        assert snap_r0 is not None and snap_r1 is not None and snap_r2 is not None
        assert (
            snap_r0["kernel"]["relationship"]
            == snap_r1["kernel"]["relationship"]
            == snap_r2["kernel"]["relationship"]
        )
        assert snap_r0["plan"] == snap_r1["plan"] == snap_r2["plan"]


@pytest.mark.asyncio
async def test_resume_refuses_version_and_fixture_mismatch(tmp_path: Path) -> None:
    """Resume rejects old metadata version or modified fixture hash."""
    output_dir = tmp_path / "eval_mismatch"
    persona = tmp_path / "persona.md"
    persona.write_text("人设", encoding="utf-8")

    runner = EvaluationRunner(
        fixtures_path=DEFAULT_FIXTURES_PATH,
        output_dir=output_dir,
        provider="demo",
        repeats=1,
    )
    await runner.execute([("v", persona)], ["greeting"])

    metadata_path = output_dir / "metadata.json"
    meta = json.loads(metadata_path.read_text(encoding="utf-8"))

    # Case 1: Old version (e.g. 1.1.0)
    meta_old_version = dict(meta)
    meta_old_version["identity"] = dict(meta["identity"])
    meta_old_version["identity"]["version"] = "1.1.0"
    metadata_path.write_text(json.dumps(meta_old_version), encoding="utf-8")

    runner_res = EvaluationRunner(
        fixtures_path=DEFAULT_FIXTURES_PATH,
        output_dir=output_dir,
        provider="demo",
        repeats=1,
        resume=True,
    )
    with pytest.raises(ValueError, match="resume inputs or model configuration differ"):
        await runner_res.execute([("v", persona)], ["greeting"])

    # Case 2: Modified fixtures_hash
    meta_mod_hash = dict(meta)
    meta_mod_hash["identity"] = dict(meta["identity"])
    meta_mod_hash["identity"]["fixtures_hash"] = "altered_hash_123"
    metadata_path.write_text(json.dumps(meta_mod_hash), encoding="utf-8")

    with pytest.raises(ValueError, match="resume inputs or model configuration differ"):
        await runner_res.execute([("v", persona)], ["greeting"])


@pytest.mark.asyncio
async def test_blinded_review_template_includes_grounding_and_marks_historical_unknown(
    tmp_path: Path,
) -> None:
    """Blinded review template includes state grounding and handles missing snapshots cleanly."""
    output_dir = tmp_path / "eval_blinded_grounding"
    persona_a = tmp_path / "persona_a.md"
    persona_a.write_text("Persona A", encoding="utf-8")
    persona_b = tmp_path / "persona_b.md"
    persona_b.write_text("Persona B", encoding="utf-8")

    runner = EvaluationRunner(
        fixtures_path=DEFAULT_FIXTURES_PATH,
        output_dir=output_dir,
        provider="demo",
        repeats=1,
    )
    await runner.execute([("vA", persona_a), ("vB", persona_b)], ["greeting"])

    template_file = output_dir / "blinded_review_template.md"
    assert template_file.exists()
    content = template_file.read_text(encoding="utf-8")

    # Must contain input state grounding
    assert "输入状态基底 (Input State Grounding):" in content
    assert "关系状态 (Relationship): 阶段=`familiar`" in content
    assert "交互计数=`7`" in content
    assert "响应计划 (Response Plan): 意图=" in content

    # Candidate comparison must NOT leak variant names "vA" or "vB"
    # Find section under 候选者回复对比
    contrast_pos = content.find("#### 候选者回复对比:")
    assert contrast_pos != -1
    sub_content = content[contrast_pos:]
    assert "vA" not in sub_content
    assert "vB" not in sub_content

    # Now simulate legacy results where input_snapshot is missing
    output_dir_legacy = tmp_path / "eval_blinded_legacy"
    output_dir_legacy.mkdir()
    results_legacy = output_dir_legacy / "results.jsonl"
    rows: list[dict[str, Any]] = [
        {
            "sample_key": f"greeting:r0:t1:{v}",
            "scenario_id": "greeting",
            "scenario_name": "普通问候",
            "turn_id": 1,
            "repeat_index": 0,
            "variant": v,
            "persona_hash": "abcd",
            "provider": "demo",
            "model": "demo-model",
            "presentation_profile": "instant_message",
            "user_text": "你好",
            "raw_reply": f"{v} 回复",
            "latency_ms": 10,
            "tokens_prompt": 100,
            "tokens_completion": 50,
            "finish_reason": "stop",
            "expected_behavior": [],
            "forbidden_behavior": [],
            "review_criteria": "",
            "timestamp": "2026-01-01T00:00:00Z",
            # input_snapshot omitted!
        }
        for v in ("vA", "vB")
    ]
    results_legacy.write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")

    runner_legacy = EvaluationRunner(
        fixtures_path=DEFAULT_FIXTURES_PATH,
        output_dir=output_dir_legacy,
        provider="demo",
        repeats=1,
    )
    tmpl_legacy = runner_legacy.generate_blinded_review_template("vA", "vB")
    content_legacy = tmpl_legacy.read_text(encoding="utf-8")
    assert "unknown (历史记录未记录 input_snapshot)" in content_legacy
    assert "关系阶段: unknown" in content_legacy


@pytest.mark.asyncio
async def test_compiler_input_and_llm_request_matches_recorded_state(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Prove recorded input_snapshot grounds the actual LlmRequest and compiler prompt."""
    from uuid import NAMESPACE_URL, uuid5

    captured_requests: list[LlmRequest] = []
    compiler_inputs: list[dict[str, Any]] = []
    original_compile = PromptCompiler.compile

    async def capture_compile(self: PromptCompiler, **kwargs: Any) -> PromptCompilation:
        compiler_inputs.append(
            {
                "version": "1.0",
                "kernel": kwargs["kernel"].model_dump(mode="json"),
                "plan": kwargs["plan"].model_dump(mode="json"),
            }
        )
        return await original_compile(self, **kwargs)

    monkeypatch.setattr(PromptCompiler, "compile", capture_compile)

    class _CaptureProvider:
        kind = "controlled"
        supports_tool_calling = False

        async def stream(self, request: LlmRequest):
            captured_requests.append(request)
            yield LlmTextDelta("测试回复")
            yield LlmResponseCompleted("stop")

    output_dir = tmp_path / "eval_capture_verify"
    runner = EvaluationRunner(
        fixtures_path=DEFAULT_FIXTURES_PATH,
        output_dir=output_dir,
        provider="controlled",
        repeats=1,
    )

    persona = tmp_path / "persona.md"
    persona.write_text("绫地宁宁的人设文本", encoding="utf-8")

    # Run greeting turn 1
    # We monkeypatch ControlledEvaluatorProvider so execute uses our capturing provider
    def _make_capture_provider(*_args: object, **_kwargs: object) -> object:
        return _CaptureProvider()

    monkeypatch.setattr(
        "tools.evaluate_character_scenarios.ControlledEvaluatorProvider",
        _make_capture_provider,
    )

    samples = await runner.execute([("baseline", persona)], ["greeting"])
    assert len(samples) == 4
    assert len(captured_requests) == 4
    assert [sample.input_snapshot for sample in samples] == compiler_inputs

    for turn_idx, (sample, req) in enumerate(zip(samples, captured_requests, strict=True), start=1):
        snap = sample.input_snapshot
        assert snap is not None

        # 1. Generation ID parity with uuid5
        expected_gen_id = uuid5(NAMESPACE_URL, sample.sample_key)
        assert req.generation_id == expected_gen_id

        # 2. System prompt contains exact relationship stage and interaction count
        stage = snap["kernel"]["relationship"]["stage"]
        count = snap["kernel"]["relationship"]["interaction_count"]
        expected_rel_str = f"Stage: {stage}. Interactions: {count}."
        assert expected_rel_str in req.system_prompt, (
            f"Expected '{expected_rel_str}' in system prompt for turn {turn_idx}"
        )

        # 3. System prompt contains exact response plan
        plan = snap["plan"]
        expected_plan_str = (
            f"Intent {plan['intent']}; tone {plan['tone']}; "
            f"emotional expression {plan['expression']}"
        )
        assert expected_plan_str in req.system_prompt, (
            f"Expected '{expected_plan_str}' in system prompt for turn {turn_idx}"
        )
