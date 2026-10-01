#!/usr/bin/env python3
# ruff: noqa: RUF001, E402
# pyright: reportPrivateUsage=false
"""Synthetic multi-turn character scenario evaluation tool for CW2.

Evaluates 12 distinct four-turn scenarios across character personas,
PromptCompiler, and LLM provider boundaries without production channel
side effects or database mutations.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import logging
import os
import re
import secrets
import sys
import time
from dataclasses import asdict, dataclass, fields
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal, cast
from uuid import NAMESPACE_URL, UUID, uuid4, uuid5

from pydantic import BaseModel, ConfigDict, ValidationError

_ROOT = Path(__file__).resolve().parents[1]
for _subpath in (
    ".",
    "packages/model-worker-sdk-python/src",
    "packages/protocol-python/src",
    "services/runtime/src",
):
    _resolved = str(_ROOT / _subpath)
    if _resolved not in sys.path:
        sys.path.insert(0, _resolved)

from chatwaifu_protocol.character import (
    AffectState,
    CharacterKernelSnapshot,
    RelationshipState,
    ResponsePlan,
)
from chatwaifu_protocol.memory import (
    MemoryChannelAttribution,
    MemoryContextPacket,
    MemoryExcerpt,
)
from chatwaifu_runtime.agent.tool_calling import MAX_AGENT_PROVIDER_ROUNDS
from chatwaifu_runtime.character_kernel.prompt import PromptCompiler
from chatwaifu_runtime.character_kernel.service import (
    _classify,
    _plan_response,
    _reduce_affect,
    _reduce_relationship,
    _relationship_stage,
)
from chatwaifu_runtime.characters.service import CharacterProfile, CharacterService
from chatwaifu_runtime.config.settings import Settings, StorageConfig
from chatwaifu_runtime.conversation.models import ConversationHistoryEntry
from chatwaifu_runtime.eventing.hub import EventHub
from chatwaifu_runtime.eventing.publisher import EventPublisher
from chatwaifu_runtime.persistence.database import Database
from chatwaifu_runtime.persistence.event_store import EventStore
from chatwaifu_runtime.persistence.sqlite_runtime_skills import SQLiteRuntimeSkillRepository
from chatwaifu_runtime.providers.contracts import (
    LlmEmptyResponseError,
    LlmInputBudget,
    LlmRequest,
    LlmResponseCompleted,
    LlmTextDelta,
    LlmUsage,
)
from chatwaifu_runtime.providers.demo_llm import DemoLlmProvider
from chatwaifu_runtime.providers.factory import build_providers
from chatwaifu_runtime.providers.model_config import ModelConfigurationService
from chatwaifu_runtime.providers.openai_compatible import OpenAiCompatibleLlmProvider
from chatwaifu_runtime.runtime_skills.service import RuntimeSkillService

from tools.runtime_source_evaluation import RuntimeSourceEvaluation

logger = logging.getLogger("evaluate_character_scenarios")

DEFAULT_FIXTURES_PATH = (
    Path(__file__).resolve().parents[1]
    / "tests"
    / "fixtures"
    / "conversation"
    / "character_scenarios.json"
)
DEFAULT_CHARACTERS_DIR = Path(__file__).resolve().parents[1] / "characters"

_FIXED_TIME = datetime(2026, 1, 1, tzinfo=UTC)
_REMOTE_MAX_ATTEMPTS = 3  # OpenAiCompatibleLlmProvider may retry before yielding output.
_RESERVED_COMPLETION_TOKENS = 512


@dataclass(frozen=True, slots=True)
class TurnDefinition:
    turn_id: int
    user_text: str
    expected_behavior: list[str]
    forbidden_behavior: list[str]
    review_criteria: str


@dataclass(frozen=True, slots=True)
class ScenarioDefinition:
    id: str
    name: str
    description: str
    initial_state: dict[str, Any]
    synthetic_memory: list[dict[str, Any]]
    seed_history: list[dict[str, str]]
    presentation_profile: str
    turns: list[TurnDefinition]


TOOL_VERSION = "1.8.2"


def _utc_prompt_time(value: datetime) -> datetime:
    if value.utcoffset() is None:
        raise ValueError("prompt reference time must have a timezone")
    return value.astimezone(UTC)


def _parse_prompt_time(value: str) -> datetime:
    try:
        return _utc_prompt_time(datetime.fromisoformat(value))
    except ValueError as error:
        raise argparse.ArgumentTypeError(str(error)) from error


EVALUATION_SNAPSHOT_VERSION: Literal["1.0"] = "1.0"


class EvaluationInputSnapshot(BaseModel):
    """Evaluation-only record of kernel snapshot and planned response admitted to PromptCompiler."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    version: Literal["1.0"]
    kernel: CharacterKernelSnapshot
    plan: ResponsePlan

    def to_dict(self) -> dict[str, Any]:
        return self.model_dump(mode="json")

    @classmethod
    def from_turn(
        cls,
        *,
        snapshot: CharacterKernelSnapshot,
        plan: ResponsePlan,
    ) -> EvaluationInputSnapshot:
        return cls(
            version=EVALUATION_SNAPSHOT_VERSION,
            kernel=snapshot.model_copy(deep=True),
            plan=plan.model_copy(deep=True),
        )


def parse_and_validate_initial_relationship(
    init_st: dict[str, Any],
    *,
    character: CharacterProfile,
    now: datetime = _FIXED_TIME,
    scenario_id: str = "unknown",
) -> RelationshipState:
    """Validate and load initial relationship state against character policy.

    Enforces:
    - Typed RelationshipState schema validation (bounds, count, stage values).
    - Consistency between duplicate keys if provided.
    - Validation that declared stage matches character policy _relationship_stage.
    - Legacy support for valid acquaintance defaults (e.g. old acquaintance fixtures).
    - Immediate rejection of contradictory stages (e.g. familiar declared with
      default or missing acquaintance metrics).
    """
    has_stage = "stage" in init_st
    has_rel_stage = "relationship_stage" in init_st
    if has_stage and has_rel_stage and init_st["stage"] != init_st["relationship_stage"]:
        raise ValueError(
            f"Scenario '{scenario_id}' initial_state has conflicting stage '{init_st['stage']}' "
            f"and relationship_stage '{init_st['relationship_stage']}'"
        )
    declared_stage = init_st.get("stage", init_st.get("relationship_stage", "acquaintance"))

    has_count = "count" in init_st
    has_interaction_count = "interaction_count" in init_st
    if has_count and has_interaction_count and init_st["count"] != init_st["interaction_count"]:
        raise ValueError(
            f"Scenario '{scenario_id}' initial_state has conflicting count ({init_st['count']}) "
            f"and interaction_count ({init_st['interaction_count']})"
        )
    raw_count = (
        init_st.get("interaction_count") if has_interaction_count else init_st.get("count", 0)
    )
    if not isinstance(raw_count, int) or isinstance(raw_count, bool):
        raise ValueError(f"Scenario '{scenario_id}' count must be an integer: {raw_count}")
    interaction_count: int = raw_count

    familiarity = init_st.get("familiarity", 0.2)
    trust = init_st.get("trust", 0.2)
    affinity = init_st.get("affinity", 0.25)
    comfort = init_st.get("comfort", 0.2)
    recent_tension = init_st.get("recent_tension", 0.0)
    preferred_address = init_st.get("preferred_address", None)

    try:
        rel = RelationshipState(
            familiarity=familiarity,
            trust=trust,
            affinity=affinity,
            comfort=comfort,
            recent_tension=recent_tension,
            interaction_count=interaction_count,
            stage=declared_stage,
            preferred_address=preferred_address,
            updated_at=now,
        )
    except ValidationError as exc:
        raise ValueError(
            f"Scenario '{scenario_id}' has invalid initial relationship state: {exc}"
        ) from exc

    policy_stage = _relationship_stage(
        rel.interaction_count,
        rel.familiarity,
        rel.trust,
        rel.affinity,
        character,
    )
    if rel.stage != policy_stage:
        raise ValueError(
            f"Scenario '{scenario_id}' declared initial stage '{rel.stage}' contradicts "
            f"character policy stage '{policy_stage}' for character '{character.character_id}' "
            f"(count={rel.interaction_count}, familiarity={rel.familiarity}, "
            f"trust={rel.trust}, affinity={rel.affinity})"
        )

    return rel


def parse_and_validate_initial_affect(
    init_st: dict[str, Any],
    *,
    now: datetime = _FIXED_TIME,
    scenario_id: str = "unknown",
) -> AffectState:
    valence = init_st.get("valence", 0.15)
    arousal = init_st.get("arousal", 0.25)
    energy = init_st.get("energy", 0.65)
    attention = init_st.get("attention", 0.7)
    embarrassment = init_st.get("embarrassment", 0.1)
    tension = init_st.get("tension", 0.05)
    try:
        return AffectState(
            valence=valence,
            arousal=arousal,
            energy=energy,
            attention=attention,
            embarrassment=embarrassment,
            tension=tension,
            updated_at=now,
        )
    except ValidationError as exc:
        raise ValueError(
            f"Scenario '{scenario_id}' has invalid initial affect state: {exc}"
        ) from exc


@dataclass(frozen=True, slots=True)
class EvaluatedSample:
    sample_key: str
    scenario_id: str
    scenario_name: str
    turn_id: int
    repeat_index: int
    variant: str
    persona_hash: str
    provider: str
    model: str
    presentation_profile: str
    user_text: str
    raw_reply: str
    latency_ms: int
    tokens_prompt: int
    tokens_completion: int
    finish_reason: str
    expected_behavior: list[str]
    forbidden_behavior: list[str]
    review_criteria: str
    timestamp: str
    provider_tokens_prompt: int | None = None
    provider_tokens_completion: int | None = None
    provider_tokens_total: int | None = None
    provider_tokens_reasoning: int | None = None
    estimated_tokens_prompt: int | None = None
    estimated_tokens_completion: int | None = None
    tokens_source: Literal["estimated", "provider_reported"] = "estimated"
    input_snapshot: dict[str, Any] | None = None
    runtime_source_trace: dict[str, Any] | None = None


_EVALUATED_SAMPLE_FIELD_NAMES = {f.name for f in fields(EvaluatedSample)}


def load_scenarios(path: Path) -> list[ScenarioDefinition]:
    if not path.exists():
        raise FileNotFoundError(f"Fixture file not found: {path}")
    raw_data: list[dict[str, Any]] = json.loads(path.read_text(encoding="utf-8"))
    scenarios: list[ScenarioDefinition] = []
    scenario_ids = [item.get("id") for item in raw_data]
    if len(set(scenario_ids)) != len(scenario_ids):
        raise ValueError("Duplicate scenario ID")
    seen_scenarios: set[str] = set()
    for item in raw_data:
        if not isinstance(item.get("id"), str):
            raise ValueError("scenario requires a string id")
        if item["id"] in seen_scenarios:
            raise ValueError(f"Duplicate scenario ID: {item['id']}")
        seen_scenarios.add(item["id"])
        raw_turns = item.get("turns", [])
        turn_ids = [turn.get("turn_id") for turn in raw_turns]
        if len(set(turn_ids)) != len(turn_ids):
            raise ValueError(f"Duplicate turn_id in scenario {item['id']}")
        if turn_ids != [1, 2, 3, 4]:
            raise ValueError(f"scenario {item['id']} requires four ordered turns")
        turns = [
            TurnDefinition(
                turn_id=t["turn_id"],
                user_text=t["user_text"],
                expected_behavior=list(t.get("expected_behavior", [])),
                forbidden_behavior=list(t.get("forbidden_behavior", [])),
                review_criteria=t.get("review_criteria", ""),
            )
            for t in raw_turns
        ]
        scenarios.append(
            ScenarioDefinition(
                id=item["id"],
                name=item["name"],
                description=item.get("description", ""),
                initial_state=item.get("initial_state", {}),
                synthetic_memory=item.get("synthetic_memory", []),
                seed_history=item.get("seed_history", []),
                presentation_profile=item.get("presentation_profile", "instant_message"),
                turns=turns,
            )
        )
    return scenarios


def compute_file_hash(path: Path) -> str:
    if not path.exists():
        return "missing"
    return hashlib.sha256(path.read_bytes()).hexdigest()[:16]


def compute_text_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def parse_sample_key(scenario_id: str, repeat_index: int, turn_id: int, variant: str) -> str:
    return f"{scenario_id}:r{repeat_index}:t{turn_id}:{variant}"


def _append_record(file_path: Path, sample: EvaluatedSample) -> None:
    with open(file_path, "a", encoding="utf-8") as out:
        out.write(json.dumps(asdict(sample), ensure_ascii=False) + "\n")


def _read_completed_records(file_path: Path) -> dict[str, EvaluatedSample]:
    completed: dict[str, EvaluatedSample] = {}
    with open(file_path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                raw_payload: object = json.loads(line)
                if not isinstance(raw_payload, dict):
                    continue
                raw_dict = cast(dict[str, Any], raw_payload)
                filtered: dict[str, Any] = {
                    str(k): v
                    for k, v in raw_dict.items()
                    if str(k) in _EVALUATED_SAMPLE_FIELD_NAMES
                }
                record = EvaluatedSample(**filtered)  # pyright: ignore[reportUnknownArgumentType]
                if record.finish_reason not in {"stop", "length", "other"}:
                    continue
            except (json.JSONDecodeError, KeyError, TypeError, ValueError):
                continue
            if record.input_snapshot is not None:
                try:
                    EvaluationInputSnapshot.model_validate(record.input_snapshot)
                except ValidationError as exc:
                    raise ValueError(
                        f"Invalid recorded input_snapshot for {record.sample_key}: {exc}"
                    ) from exc
            completed[record.sample_key] = record
    return completed


def estimate_pricing(
    model: str,
    total_prompt_tokens: int,
    total_completion_tokens: int,
    *,
    input_usd_per_million: float | None = None,
    output_usd_per_million: float | None = None,
) -> tuple[float | None, str]:
    if input_usd_per_million is None or output_usd_per_million is None:
        if model in {"demo-model", "controlled-model"}:
            return 0.0, "$0.0000 USD (local synthetic provider)"
        return None, "unknown (supply model-specific rates and their source/date)"
    cost = (total_prompt_tokens / 1_000_000.0) * input_usd_per_million + (
        total_completion_tokens / 1_000_000.0
    ) * output_usd_per_million
    return cost, f"${cost:.4f} USD (estimated; not a billing cap)"


class ControlledEvaluatorProvider:
    """Deterministic local provider for repeatable scenario testing."""

    kind = "controlled"
    supports_tool_calling = False

    def __init__(self, latency_ms: int = 1) -> None:
        self._latency_seconds = max(0.001, latency_ms / 1000.0)

    async def stream(self, request: LlmRequest):
        await asyncio.sleep(self._latency_seconds)
        user_turn = request.user_text.strip()
        character_name = request.character_name or "绫地宁宁"
        reply = (
            f"【{character_name}受控回复】已接收到：「{user_turn}」。"
            "这是在受控评估模式下生成的确定性基准回复。"
        )
        yield LlmTextDelta(reply)
        yield LlmResponseCompleted("stop")


class EvaluationRunner:
    def __init__(
        self,
        *,
        fixtures_path: Path = DEFAULT_FIXTURES_PATH,
        characters_dir: Path = DEFAULT_CHARACTERS_DIR,
        output_dir: Path,
        provider: str = "demo",
        model_name: str | None = None,
        base_url: str | None = None,
        input_usd_per_million: float | None = None,
        output_usd_per_million: float | None = None,
        pricing_source: str | None = None,
        repeats: int = 3,
        max_requests: int | None = None,
        cost_ceiling: float | None = None,
        no_cost_ceiling: bool = False,
        timeout_seconds: float = 30.0,
        variant_a_name: str = "baseline",
        variant_a_persona_path: Path | None = None,
        variant_b_name: str | None = None,
        variant_b_persona_path: Path | None = None,
        resume: bool = True,
        isolated_db_dir: Path | None = None,
        runtime_source_tools: bool = False,
        allow_source_tools_once: bool = False,
        max_provider_requests: int | None = None,
        source_dns_resolver: Literal["system", "cloudflare"] = "system",
        prompt_as_of: datetime | None = None,
    ) -> None:
        self.fixtures_path = fixtures_path
        self.characters_dir = characters_dir
        self.output_dir = output_dir
        self.provider_kind = provider
        self.runtime_source_tools = runtime_source_tools
        self.allow_source_tools_once = allow_source_tools_once
        self.max_provider_requests = max_provider_requests
        self.source_dns_resolver: Literal["system", "cloudflare"] = source_dns_resolver
        if runtime_source_tools:
            if not allow_source_tools_once:
                raise ValueError("Runtime source evaluation requires --allow-source-tools-once")
            if max_provider_requests is None or max_provider_requests < 1:
                raise ValueError(
                    "Runtime source evaluation requires positive --max-provider-requests"
                )
            if provider == "openai_compatible" and not no_cost_ceiling:
                raise ValueError(
                    "Runtime source evaluation requires --no-cost-ceiling: tool inputs are unknown"
                )
        elif allow_source_tools_once or max_provider_requests is not None:
            raise ValueError("Source-tool options require --runtime-source-tools")
        if source_dns_resolver not in {"system", "cloudflare"}:
            raise ValueError("Unsupported source DNS resolver")
        if provider not in {"demo", "controlled", "openai_compatible"}:
            raise ValueError(f"Unsupported evaluation provider: {provider}")
        if repeats < 1 or timeout_seconds <= 0:
            raise ValueError("repeats and timeout must be positive")
        if max_requests is not None and max_requests < 1:
            raise ValueError("max_requests must be positive")
        if cost_ceiling is not None and cost_ceiling < 0:
            raise ValueError("cost_ceiling must not be negative")
        if no_cost_ceiling and cost_ceiling is not None:
            raise ValueError("cannot combine --no-cost-ceiling with --cost-ceiling")
        self.model_name = model_name or (
            "demo-model"
            if provider == "demo"
            else "controlled-model"
            if provider == "controlled"
            else "unknown"
        )
        self.base_url = base_url
        self.input_usd_per_million = input_usd_per_million
        self.output_usd_per_million = output_usd_per_million
        self.pricing_source = pricing_source
        self.repeats = repeats
        self.max_requests = max_requests
        self.cost_ceiling = cost_ceiling
        self.no_cost_ceiling = bool(no_cost_ceiling)
        self.timeout_seconds = timeout_seconds
        self.variant_a_name = variant_a_name
        self.variant_a_persona_path = variant_a_persona_path or (
            characters_dir / "default" / "persona.md"
        )
        self.variant_b_name = variant_b_name
        self.variant_b_persona_path = variant_b_persona_path
        self.resume = resume
        self.isolated_db_dir = isolated_db_dir or (output_dir / "isolated_db")
        self.scenarios = load_scenarios(self.fixtures_path)

        self._completed_records: dict[str, EvaluatedSample] = {}
        self._results_file = self.output_dir / "results.jsonl"
        self._metadata_file = self.output_dir / "metadata.json"
        self._incomplete_file = self.output_dir / "incomplete.jsonl"
        if prompt_as_of is None and resume and self._metadata_file.exists():
            saved = json.loads(self._metadata_file.read_text(encoding="utf-8"))
            saved_time = saved.get("identity", {}).get("prompt_as_of")
            if saved_time is not None:
                prompt_as_of = datetime.fromisoformat(saved_time)
        self.prompt_as_of = _utc_prompt_time(
            prompt_as_of
            if prompt_as_of is not None
            else datetime.now(UTC)
            if provider == "openai_compatible"
            else _FIXED_TIME
        )

    def _validate_execution(self) -> None:
        if self.provider_kind != "openai_compatible":
            if self.no_cost_ceiling and self.cost_ceiling is not None:
                raise ValueError("cannot combine --no-cost-ceiling with --cost-ceiling")
            return
        if not self.base_url or self.model_name == "unknown":
            raise ValueError("remote execution requires explicit --base-url and --model")
        if self.no_cost_ceiling and self.cost_ceiling is not None:
            raise ValueError("cannot combine --no-cost-ceiling with --cost-ceiling")
        if self.no_cost_ceiling and (
            self.input_usd_per_million is not None
            or self.output_usd_per_million is not None
            or self.pricing_source is not None
        ):
            raise ValueError(
                "cannot combine --no-cost-ceiling with explicit pricing rates or --pricing-source"
            )
        if not self.no_cost_ceiling:
            if self.max_requests is None or self.cost_ceiling is None:
                raise ValueError(
                    "remote execution requires --max-requests and "
                    "either --cost-ceiling or --no-cost-ceiling"
                )
            if (
                self.input_usd_per_million is None
                or self.output_usd_per_million is None
                or self.input_usd_per_million < 0
                or self.output_usd_per_million < 0
                or not self.pricing_source
            ):
                raise ValueError(
                    "remote execution with cost ceiling requires "
                    "nonnegative rates and --pricing-source"
                )
        else:
            if self.max_requests is None:
                raise ValueError(
                    "remote execution with --no-cost-ceiling requires explicit --max-requests"
                )

    def estimate_dry_run(
        self,
        variants_to_run: list[tuple[str, Path]],
        selected_scenario_ids: list[str] | None = None,
    ) -> dict[str, Any]:
        target_scenarios = [
            s for s in self.scenarios if not selected_scenario_ids or s.id in selected_scenario_ids
        ]
        num_scenarios = len(target_scenarios)
        turns_per_scenario = 4
        num_variants = len(variants_to_run)
        total_requests = num_scenarios * turns_per_scenario * self.repeats * num_variants

        characters = CharacterService(self.characters_dir)
        characters.start()
        base_char = characters.get("default")
        assert base_char is not None

        # Preflight ALL selected scenarios across all variants before compiling
        variant_characters: list[CharacterProfile] = []
        for _variant_name, persona_path in variants_to_run:
            persona_text = persona_path.read_text(encoding="utf-8").strip()
            variant_char = CharacterProfile.model_validate(
                {
                    **base_char.model_dump(),
                    "system_prompt": persona_text,
                }
            )
            variant_characters.append(variant_char)
            for s in target_scenarios:
                parse_and_validate_initial_relationship(
                    s.initial_state,
                    character=variant_char,
                    now=_FIXED_TIME,
                    scenario_id=s.id,
                )
                parse_and_validate_initial_affect(
                    s.initial_state,
                    now=_FIXED_TIME,
                    scenario_id=s.id,
                )

        # Compile each selected persona and scenario. Later-turn history and
        # generated completion lengths remain unknown before execution.

        class _DummyModelConfig:
            def get(self, role: str):
                return argparse.Namespace(context_window=8192)

        compiler = PromptCompiler(_DummyModelConfig())  # pyright: ignore[reportArgumentType]
        sample_prompt_tokens: list[int] = []

        now = _FIXED_TIME
        for variant_char in variant_characters:
            for s in target_scenarios:
                init_aff = parse_and_validate_initial_affect(
                    s.initial_state, now=now, scenario_id=s.id
                )
                init_r = parse_and_validate_initial_relationship(
                    s.initial_state, character=variant_char, now=now, scenario_id=s.id
                )
                kernel = CharacterKernelSnapshot(
                    character_id=variant_char.character_id,
                    user_scope="local",
                    revision=1,
                    affect=init_aff,
                    relationship=init_r,
                )
                plan = ResponsePlan(
                    intent="answer", tone="gentle", expression="neutral", rationale="estimate"
                )
                comp = asyncio.run(
                    compiler.compile(
                        character=variant_char,
                        kernel=kernel,
                        plan=plan,
                        memory=MemoryContextPacket(token_budget_used=0),
                        history=(),
                        user_text=s.turns[0].user_text,
                        presentation_profile=s.presentation_profile,
                        as_of=self.prompt_as_of,
                    )
                )
                sample_prompt_tokens.append(comp.report.used)
        # Average completion tokens estimate: casual ~60, technical/detail ~350, blend ~120
        avg_completion_tokens = 120

        total_prompt_tokens = sum(sample_prompt_tokens) * turns_per_scenario * self.repeats
        total_completion_tokens = total_requests * avg_completion_tokens
        total_tokens = total_prompt_tokens + total_completion_tokens

        if self.no_cost_ceiling:
            cost_val = None
            cost_str = "unpriced (user authorized --no-cost-ceiling; no dollar ceiling configured)"
            pricing_src = "none (unpriced)"
        else:
            cost_val, cost_str = estimate_pricing(
                self.model_name,
                total_prompt_tokens,
                total_completion_tokens,
                input_usd_per_million=self.input_usd_per_million,
                output_usd_per_million=self.output_usd_per_million,
            )
            pricing_src = self.pricing_source

        return {
            "mode": "dry-run",
            "prompt_as_of": self.prompt_as_of.isoformat(),
            "state_time": _FIXED_TIME.isoformat(),
            "execution_path": "runtime_source_tools"
            if self.runtime_source_tools
            else "direct_provider",
            "provider_round_upper_bound": total_requests
            * (MAX_AGENT_PROVIDER_ROUNDS if self.runtime_source_tools else 1),
            "max_provider_requests": self.max_provider_requests,
            "tool_input_tokens_estimated": False,
            "fixtures_file": str(self.fixtures_path),
            "scenarios_count": num_scenarios,
            "turns_per_scenario": turns_per_scenario,
            "repeats": self.repeats,
            "variants": [name for name, _ in variants_to_run],
            "total_requests": total_requests,
            "estimated_prompt_tokens": total_prompt_tokens,
            "estimated_completion_tokens": total_completion_tokens,
            "estimated_total_tokens": total_tokens,
            "prompt_estimate_scope": (
                "Each selected persona and scenario's first turn, compiled at an 8192-token "
                "window, extrapolated to all turns and repeats without later-turn history."
            ),
            "provider": self.provider_kind,
            "model": self.model_name,
            "estimated_cost_usd": cost_val,
            "estimated_cost_display": cost_str,
            "pricing_source": pricing_src,
            "cost_ceiling_usd": None if self.no_cost_ceiling else self.cost_ceiling,
            "cost_policy": "no_cost_ceiling" if self.no_cost_ceiling else "capped",
            "estimate_is_billing_cap": False,
            "remote_provider_max_attempts_per_logical_request": (
                _REMOTE_MAX_ATTEMPTS if self.provider_kind == "openai_compatible" else 1
            ),
            "max_requests_counts_logical_requests": True,
            "notice": (
                f"max_requests counts logical requests; adapter retries may cause up to "
                f"{_REMOTE_MAX_ATTEMPTS} HTTP attempts per request."
                if self.provider_kind == "openai_compatible"
                else "max_requests counts logical requests."
            ),
        }

    async def execute(
        self,
        variants_to_run: list[tuple[str, Path]],
        selected_scenario_ids: list[str] | None = None,
    ) -> list[EvaluatedSample]:
        self._validate_execution()

        target_scenarios = [
            s for s in self.scenarios if not selected_scenario_ids or s.id in selected_scenario_ids
        ]

        characters_service = CharacterService(self.characters_dir)
        characters_service.start()
        base_character = characters_service.get("default")
        assert base_character is not None

        # Preflight ALL selected scenarios across all variants before model calls or DB setup
        for _variant_name, persona_path in variants_to_run:
            persona_text = persona_path.read_text(encoding="utf-8").strip()
            variant_character = CharacterProfile.model_validate(
                {
                    **base_character.model_dump(),
                    "system_prompt": persona_text,
                }
            )
            for scenario in target_scenarios:
                parse_and_validate_initial_relationship(
                    scenario.initial_state,
                    character=variant_character,
                    now=_FIXED_TIME,
                    scenario_id=scenario.id,
                )
                parse_and_validate_initial_affect(
                    scenario.initial_state,
                    now=_FIXED_TIME,
                    scenario_id=scenario.id,
                )

        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.isolated_db_dir.mkdir(parents=True, exist_ok=True)

        metadata_identity: dict[str, Any] = {
            "tool": "evaluate_character_scenarios",
            "version": TOOL_VERSION,
            "prompt_as_of": self.prompt_as_of.isoformat(),
            "state_time": _FIXED_TIME.isoformat(),
            "fixtures_hash": compute_file_hash(self.fixtures_path),
            "provider": self.provider_kind,
            "model": self.model_name,
            "repeats": self.repeats,
            "base_url_hash": compute_text_hash(self.base_url) if self.base_url else None,
            "input_usd_per_million": self.input_usd_per_million,
            "output_usd_per_million": self.output_usd_per_million,
            "pricing_source": self.pricing_source,
            "cost_policy": "no_cost_ceiling" if self.no_cost_ceiling else "capped",
            "cost_ceiling": self.cost_ceiling,
            "variants": {
                name: {
                    "hash": compute_file_hash(p),
                }
                for name, p in variants_to_run
            },
        }
        if self.runtime_source_tools:
            fingerprint = hashlib.sha256()
            for relative in (
                "tools/runtime_source_evaluation.py",
                "services/runtime/src/chatwaifu_runtime/character_kernel/prompt.py",
                "services/runtime/src/chatwaifu_runtime/conversation/models.py",
                "services/runtime/src/chatwaifu_runtime/conversation/service.py",
                "packages/protocol-python/src/chatwaifu_protocol/character.py",
                "services/runtime/src/chatwaifu_runtime/agent/tool_calling.py",
                "services/runtime/src/chatwaifu_runtime/agent/input_budget.py",
                "services/runtime/src/chatwaifu_runtime/agent/source_context.py",
                "services/runtime/src/chatwaifu_runtime/conversation/source_context.py",
                "services/runtime/src/chatwaifu_runtime/runtime_skills/service.py",
                "services/runtime/src/chatwaifu_runtime/runtime_skills/repository.py",
                "services/runtime/src/chatwaifu_runtime/persistence/sqlite_runtime_skills.py",
                "services/runtime/src/chatwaifu_runtime/providers/contracts.py",
                "services/runtime/src/chatwaifu_runtime/providers/input_estimation.py",
                "services/runtime/src/chatwaifu_runtime/providers/data/cl100k_base.tiktoken",
                "services/runtime/pyproject.toml",
                "services/runtime/src/chatwaifu_runtime/providers/openai_compatible.py",
                "services/runtime/src/chatwaifu_runtime/runtime_skills/agent_router.py",
                "services/runtime/src/chatwaifu_runtime/runtime_skills/public_web.py",
                "services/runtime/src/chatwaifu_runtime/runtime_skills/public_web_search.py",
                "skills/builtin/web-read/chatwaifu.yaml",
                "skills/builtin/web-read/SKILL.md",
                "skills/builtin/web-search/chatwaifu.yaml",
                "skills/builtin/web-search/SKILL.md",
            ):
                fingerprint.update(relative.encode())
                fingerprint.update((_ROOT / relative).read_bytes())
            metadata_identity["execution_path"] = "runtime_source_tools"
            metadata_identity["runtime_source_config"] = {
                "permission_policy": "allow_once",
                "dns_resolver": self.source_dns_resolver,
                "max_provider_requests": self.max_provider_requests,
                "implementation_sha256": fingerprint.hexdigest(),
            }
        if self._results_file.exists():
            if not self.resume:
                raise ValueError("results already exist; choose a fresh --output-dir")
            if not self._metadata_file.exists():
                raise ValueError("cannot resume results without metadata.json")
            previous_metadata = json.loads(self._metadata_file.read_text(encoding="utf-8"))
            previous_identity = dict(previous_metadata.get("identity", {}))
            if "cost_policy" not in previous_identity:
                if self.no_cost_ceiling:
                    raise ValueError("cannot resume legacy capped run with --no-cost-ceiling")
                comp_identity = dict(metadata_identity)
                comp_identity.pop("cost_policy", None)
                comp_identity.pop("cost_ceiling", None)
                if previous_identity != comp_identity:
                    raise ValueError(
                        "resume inputs or model configuration differ from saved results"
                    )
            else:
                if previous_identity != metadata_identity:
                    raise ValueError(
                        "resume inputs or model configuration differ from saved results"
                    )
            self._completed_records = await asyncio.to_thread(
                _read_completed_records, self._results_file
            )
        elif self._metadata_file.exists() and self.resume:
            previous_metadata = json.loads(self._metadata_file.read_text(encoding="utf-8"))
            previous_identity = dict(previous_metadata.get("identity", {}))
            if "cost_policy" not in previous_identity:
                if self.no_cost_ceiling:
                    raise ValueError("cannot resume legacy capped run with --no-cost-ceiling")
                comp_identity = dict(metadata_identity)
                comp_identity.pop("cost_policy", None)
                comp_identity.pop("cost_ceiling", None)
                if previous_identity != comp_identity:
                    raise ValueError("resume metadata differs from current inputs")
            else:
                if previous_identity != metadata_identity:
                    raise ValueError("resume metadata differs from current inputs")
        if self.runtime_source_tools:
            expected_rounds: set[str] = set()
            previous_traces = [s.runtime_source_trace for s in self._completed_records.values()]
            if self._incomplete_file.exists():
                previous_traces.extend(
                    json.loads(line).get("runtime_source_trace")
                    for line in self._incomplete_file.read_text(encoding="utf-8").splitlines()
                    if line.strip()
                )
            for trace in previous_traces:
                if trace is not None:
                    expected_rounds.update(call["call_id"] for call in trace["provider_calls"])
            journal_path = self.output_dir / "provider-rounds.jsonl"
            saved_starts: set[str] = set()
            if journal_path.exists():
                for line in journal_path.read_text(encoding="utf-8").splitlines():
                    row = json.loads(line)
                    if row["event"] == "started":
                        saved_starts.add(row["call_id"])
            if not expected_rounds.issubset(saved_starts):
                raise ValueError("cannot resume: provider-rounds journal lost recorded attempts")
        metadata = {"created_at": datetime.now(UTC).isoformat(), "identity": metadata_identity}
        self._metadata_file.write_text(
            json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8"
        )

        db_path = self.isolated_db_dir / "eval_isolated.db"
        storage_cfg = StorageConfig(database_path=db_path)
        database = Database(db_path, storage_cfg)
        await database.open()
        await database.migrate()

        settings = Settings.model_validate(
            {
                "config_dir": self.isolated_db_dir / "config",
                "data_dir": self.isolated_db_dir,
                "storage": storage_cfg,
                "llm": {"provider": "demo", "demo_chunk_delay_ms": 0},
                "tts": {"provider": "fake"},
            }
        )
        model_configs = ModelConfigurationService(database, settings)
        await model_configs.start()
        compiler = PromptCompiler(model_configs)

        # base_character already started during preflight

        # Build LLM provider
        if self.provider_kind == "controlled":
            llm_provider = ControlledEvaluatorProvider(latency_ms=1)
        elif self.provider_kind == "demo":
            llm_provider = DemoLlmProvider(chunk_delay_ms=0)
        elif self.provider_kind == "openai_compatible":
            llm_provider = OpenAiCompatibleLlmProvider(
                model=self.model_name,
                base_url=self.base_url or "",
                api_key=os.environ.get("OPENAI_API_KEY"),
                timeout_seconds=self.timeout_seconds,
                request_usage=True,
            )
        else:
            raise AssertionError("execution provider was not validated")

        requests_executed = 0
        spent_prompt_tokens = sum(s.tokens_prompt for s in self._completed_records.values())
        spent_completion_tokens = sum(s.tokens_completion for s in self._completed_records.values())
        samples_collected: list[EvaluatedSample] = []
        source_service: RuntimeSkillService | None = None
        source_evaluation: RuntimeSourceEvaluation | None = None

        try:
            if self.runtime_source_tools:
                source_service = RuntimeSkillService(
                    _ROOT / "skills",
                    self.isolated_db_dir,
                    SQLiteRuntimeSkillRepository(database),
                    EventPublisher(
                        EventStore(database), EventHub(settings.runtime.event_queue_size)
                    ),
                    build_providers(settings),
                    "disabled",
                    "evaluation",
                )
                await source_service.start()
                assert self.max_provider_requests is not None
                source_evaluation = RuntimeSourceEvaluation(
                    llm_provider,
                    source_service,
                    trace_path=self.output_dir / "provider-rounds.jsonl",
                    max_provider_requests=self.max_provider_requests,
                    allow_once=self.allow_source_tools_once,
                    dns_resolver=self.source_dns_resolver,
                )
            for variant_name, persona_path in variants_to_run:
                # Load variant character profile
                persona_text = persona_path.read_text(encoding="utf-8").strip()
                persona_hash = compute_text_hash(persona_text)
                character = CharacterProfile.model_validate(
                    {
                        **base_character.model_dump(),
                        "system_prompt": persona_text,
                    }
                )

                for r_idx in range(self.repeats):
                    for scenario in target_scenarios:
                        now = _FIXED_TIME
                        session_id = uuid4()
                        await database.execute(
                            """
                            INSERT INTO sessions (
                                session_id, character_id, state, conversation_state,
                                revision, next_sequence, created_at, updated_at,
                                participant_id, scene_id, scene_kind, audience_json, user_scope
                            ) VALUES (
                                ?, ?, 'ready', 'idle', 0, 1, ?, ?,
                                'local', NULL, 'private', '["local"]', 'local'
                            )
                            """,
                            (str(session_id), "default", now.isoformat(), now.isoformat()),
                        )

                        # Set up initial state if provided
                        init_st = scenario.initial_state
                        init_affect = parse_and_validate_initial_affect(
                            init_st,
                            now=now,
                            scenario_id=scenario.id,
                        )
                        init_rel = parse_and_validate_initial_relationship(
                            init_st,
                            character=character,
                            now=now,
                            scenario_id=scenario.id,
                        )
                        # Insert initial state into DB for this user_scope
                        await database.execute(
                            "INSERT OR REPLACE INTO character_states "
                            "(character_id, user_scope, revision, valence, arousal, energy, "
                            "attention, embarrassment, tension, updated_at) "
                            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                            (
                                "default",
                                "local",
                                0,
                                init_affect.valence,
                                init_affect.arousal,
                                init_affect.energy,
                                init_affect.attention,
                                init_affect.embarrassment,
                                init_affect.tension,
                                now.isoformat(),
                            ),
                        )
                        await database.execute(
                            "INSERT OR REPLACE INTO relationship_states "
                            "(character_id, user_scope, revision, familiarity, trust, affinity, "
                            "comfort, recent_tension, interaction_count, stage, "
                            "preferred_address, updated_at) "
                            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                            (
                                "default",
                                "local",
                                0,
                                init_rel.familiarity,
                                init_rel.trust,
                                init_rel.affinity,
                                init_rel.comfort,
                                init_rel.recent_tension,
                                init_rel.interaction_count,
                                init_rel.stage,
                                init_rel.preferred_address,
                                now.isoformat(),
                            ),
                        )

                        # Build synthetic memory packet
                        memory_packet = _build_memory_packet(
                            scenario.synthetic_memory, scenario_id=scenario.id
                        )

                        # Build seed history
                        history: list[ConversationHistoryEntry] = [
                            ConversationHistoryEntry(
                                role=h["role"],
                                text=h["text"],
                            )
                            for h in scenario.seed_history
                        ]

                        current_affect = init_affect
                        current_rel = init_rel
                        current_revision = 0

                        for turn in scenario.turns:
                            key = parse_sample_key(scenario.id, r_idx, turn.turn_id, variant_name)
                            # Update affect and relationship deterministically
                            signal = _classify(turn.user_text)
                            current_affect = _reduce_affect(current_affect, signal, _FIXED_TIME)
                            current_rel = _reduce_relationship(
                                current_rel, signal, character, _FIXED_TIME
                            )
                            current_revision += 1
                            snapshot = CharacterKernelSnapshot(
                                character_id="default",
                                user_scope="local",
                                revision=current_revision,
                                affect=current_affect,
                                relationship=current_rel,
                            )
                            plan = _plan_response(turn.user_text, signal, snapshot, character)
                            turn_input_snapshot = EvaluationInputSnapshot.from_turn(
                                snapshot=snapshot,
                                plan=plan,
                            ).to_dict()

                            previous = self._completed_records.get(key)
                            if previous is not None:
                                history.append(
                                    ConversationHistoryEntry(role="user", text=turn.user_text)
                                )
                                history.append(
                                    ConversationHistoryEntry(
                                        role="assistant",
                                        text=previous.raw_reply,
                                        generation_id=uuid5(NAMESPACE_URL, key),
                                    )
                                )
                                continue

                            if (
                                self.max_requests is not None
                                and requests_executed >= self.max_requests
                            ):
                                self._record_incomplete(key, "request_limit")
                                return samples_collected

                            # Compile prompt
                            compilation = await compiler.compile(
                                character=character,
                                kernel=snapshot,
                                plan=plan,
                                memory=memory_packet,
                                history=tuple(history),
                                user_text=turn.user_text,
                                presentation_profile=scenario.presentation_profile,
                                as_of=self.prompt_as_of,
                            )

                            if self.cost_ceiling is not None:
                                multiplier = (
                                    _REMOTE_MAX_ATTEMPTS
                                    if self.provider_kind == "openai_compatible"
                                    else 1
                                )
                                projected_cost, _ = estimate_pricing(
                                    self.model_name,
                                    spent_prompt_tokens + multiplier * compilation.report.used,
                                    spent_completion_tokens
                                    + multiplier * _RESERVED_COMPLETION_TOKENS,
                                    input_usd_per_million=self.input_usd_per_million,
                                    output_usd_per_million=self.output_usd_per_million,
                                )
                                if projected_cost is None or projected_cost > self.cost_ceiling:
                                    self._record_incomplete(key, "estimated_cost_ceiling")
                                    return samples_collected

                            req = LlmRequest(
                                generation_id=uuid5(NAMESPACE_URL, key),
                                user_text=turn.user_text,
                                system_prompt=compilation.system_prompt,
                                tool_decision_system_prompt=compilation.tool_decision_system_prompt,
                                character_name=character.display_name,
                                context=compilation.context,
                                history=compilation.history,
                                input_budget=LlmInputBudget(compilation.report.budget),
                                recalled_memory_texts=compilation.recalled_memory_texts,
                                routing_previous_user_text=next(
                                    (
                                        entry.text
                                        for entry in reversed(history)
                                        if entry.role == "user"
                                    ),
                                    None,
                                )
                                if self.runtime_source_tools
                                else None,
                            )

                            # Stream from LLM provider with timeout
                            start_time = time.perf_counter()
                            output_text = ""
                            finish_reason: str | None = None
                            captured_usage: LlmUsage | None = None
                            runtime_source_trace: dict[str, Any] | None = None

                            async def _stream_llm(
                                current_req: LlmRequest,
                                current_session: UUID = session_id,
                                current_key: str = key,
                                current_source_generations: tuple[
                                    UUID, ...
                                ] = compilation.source_generation_ids,
                            ) -> None:
                                nonlocal \
                                    output_text, \
                                    finish_reason, \
                                    captured_usage, \
                                    runtime_source_trace
                                if source_evaluation is not None:
                                    outcome = await source_evaluation.run(
                                        current_req,
                                        session_id=current_session,
                                        turn_id=uuid5(NAMESPACE_URL, current_key + ":turn"),
                                        sample_key=current_key,
                                        source_generation_ids=current_source_generations,
                                    )
                                    output_text, finish_reason = (
                                        outcome.reply,
                                        outcome.finish_reason,
                                    )
                                    captured_usage, runtime_source_trace = (
                                        outcome.usage,
                                        outcome.trace,
                                    )
                                    return
                                async for event in llm_provider.stream(current_req):
                                    if isinstance(event, LlmTextDelta):
                                        output_text += event.text
                                    elif isinstance(event, LlmResponseCompleted):
                                        finish_reason = event.finish_reason
                                        captured_usage = event.usage

                            try:
                                requests_executed += 1
                                await asyncio.wait_for(
                                    _stream_llm(req), timeout=self.timeout_seconds
                                )
                            except TimeoutError:
                                logger.error(
                                    "Turn %s timed out after %s seconds",
                                    key,
                                    self.timeout_seconds,
                                )
                                self._record_incomplete(
                                    key,
                                    "timeout",
                                    source_evaluation.last_trace if source_evaluation else None,
                                )
                                return samples_collected
                            except asyncio.CancelledError:
                                self._record_incomplete(
                                    key,
                                    "cancelled",
                                    source_evaluation.last_trace if source_evaluation else None,
                                )
                                raise
                            except Exception as error:
                                self._record_incomplete(
                                    key,
                                    "empty_model_response"
                                    if isinstance(error, LlmEmptyResponseError)
                                    else type(error).__name__,
                                    source_evaluation.last_trace if source_evaluation else None,
                                    provider_usage=captured_usage,
                                    raw_reply=output_text
                                    if source_evaluation is None
                                    and isinstance(error, LlmEmptyResponseError)
                                    else None,
                                )
                                return samples_collected

                            if finish_reason is None:
                                self._record_incomplete(key, "missing_terminal_event")
                                return samples_collected

                            if not output_text.strip():
                                self._record_incomplete(
                                    key,
                                    "empty_model_response",
                                    runtime_source_trace,
                                    provider_usage=captured_usage,
                                    raw_reply=output_text,
                                )
                                return samples_collected

                            latency_ms = int((time.perf_counter() - start_time) * 1000)
                            tokens_completion_est = max(1, (len(output_text) + 1) // 2)
                            tokens_prompt_est = compilation.report.used

                            tokens_source: Literal["estimated", "provider_reported"] = (
                                "provider_reported" if captured_usage is not None else "estimated"
                            )
                            provider_tokens_prompt = (
                                captured_usage.prompt_tokens if captured_usage else None
                            )
                            provider_tokens_completion = (
                                captured_usage.completion_tokens if captured_usage else None
                            )
                            provider_tokens_total = (
                                captured_usage.total_tokens if captured_usage else None
                            )
                            provider_tokens_reasoning = (
                                captured_usage.reasoning_tokens if captured_usage else None
                            )

                            sample = EvaluatedSample(
                                sample_key=key,
                                scenario_id=scenario.id,
                                scenario_name=scenario.name,
                                turn_id=turn.turn_id,
                                repeat_index=r_idx,
                                variant=variant_name,
                                persona_hash=persona_hash,
                                provider=self.provider_kind,
                                model=self.model_name,
                                presentation_profile=scenario.presentation_profile,
                                user_text=turn.user_text,
                                raw_reply=output_text,
                                latency_ms=latency_ms,
                                tokens_prompt=tokens_prompt_est,
                                tokens_completion=tokens_completion_est,
                                finish_reason=finish_reason,
                                expected_behavior=turn.expected_behavior,
                                forbidden_behavior=turn.forbidden_behavior,
                                review_criteria=turn.review_criteria,
                                timestamp=datetime.now(UTC).isoformat(),
                                provider_tokens_prompt=provider_tokens_prompt,
                                provider_tokens_completion=provider_tokens_completion,
                                provider_tokens_total=provider_tokens_total,
                                provider_tokens_reasoning=provider_tokens_reasoning,
                                estimated_tokens_prompt=tokens_prompt_est,
                                estimated_tokens_completion=tokens_completion_est,
                                tokens_source=tokens_source,
                                input_snapshot=turn_input_snapshot,
                                runtime_source_trace=runtime_source_trace,
                            )

                            # Append to results file immediately (partial persistence)
                            await asyncio.to_thread(_append_record, self._results_file, sample)

                            self._completed_records[key] = sample
                            samples_collected.append(sample)
                            spent_prompt_tokens += sample.tokens_prompt
                            spent_completion_tokens += sample.tokens_completion

                            # Append to simulated conversation history for next turns
                            history.append(
                                ConversationHistoryEntry(role="user", text=turn.user_text)
                            )
                            history.append(
                                ConversationHistoryEntry(
                                    role="assistant",
                                    text=output_text,
                                    generation_id=req.generation_id,
                                )
                            )

        finally:
            try:
                if source_service is not None:
                    await source_service.stop()
            finally:
                await database.close()

        # If paired A/B variants were run, generate blinded review template
        if len(variants_to_run) >= 2:
            self.generate_blinded_review_template(variants_to_run[0][0], variants_to_run[1][0])

        return samples_collected

    def _record_incomplete(
        self,
        sample_key: str,
        reason: str,
        runtime_source_trace: dict[str, Any] | None = None,
        *,
        provider_usage: LlmUsage | None = None,
        raw_reply: str | None = None,
    ) -> None:
        with self._incomplete_file.open("a", encoding="utf-8") as out:
            record: dict[str, Any] = {"sample_key": sample_key, "reason": reason}
            if runtime_source_trace is not None:
                record["runtime_source_trace"] = runtime_source_trace
            if provider_usage is not None:
                record["provider_usage"] = asdict(provider_usage)
            if raw_reply is not None:
                record["raw_reply"] = raw_reply
            out.write(json.dumps(record, ensure_ascii=False) + "\n")

    def generate_blinded_review_template(self, variant_a: str, variant_b: str) -> Path:
        if not self._results_file.exists():
            raise FileNotFoundError(f"Results file does not exist: {self._results_file}")

        # Index results by (scenario_id, repeat_index, turn_id, variant)
        records: dict[tuple[str, int, int, str], dict[str, Any]] = {}
        with open(self._results_file, encoding="utf-8") as f:
            for line in f:
                if not line.strip():
                    continue
                rec = json.loads(line)
                k = (rec["scenario_id"], rec["repeat_index"], rec["turn_id"], rec["variant"])
                records[k] = rec

        blind_key_map: dict[str, Any] = {}
        key_file = self.output_dir / "blinded_key.json"
        saved_keys: dict[str, Any] = {}
        if key_file.exists():
            loaded_keys: Any = json.loads(key_file.read_text(encoding="utf-8"))
            if not isinstance(loaded_keys, dict):
                raise ValueError("saved review key must be an object")
            saved_keys = cast(dict[str, Any], loaded_keys)
        template_lines: list[str] = [
            "# 匿名配对角色场景评审表 (Version-Masked Review Template)",
            "",
            f"- 评估日期: {datetime.now(UTC).strftime('%Y-%m-%d %H:%M:%S UTC')}",
            f"- 提示时间基准: {self.prompt_as_of.isoformat()}"
            f"（状态夹具时间另为 {_FIXED_TIME.isoformat()}）",
            f"- 待评审样本源: `{self._results_file.name}`",
            "- 评审原则: 评审人员在不知晓候选者具体版本/提示词前提下，依据场景预期行为与禁止行为客观打分。",  # noqa: E501
            "- 本表仅隐藏版本标签，不保证独立盲评；知晓候选设计的作者复核必须注明，不能称为双盲。",
            "- 先保存判断再揭示键表；原始结果中的版本、时延及用量不随质量评审表展示。",
            "",
            "## 评分维度说明",
            "1. **自然度 (1-5分)**: 语言口语化自然、有来有回，无生硬机械感或多余舞台剧说明。",
            "2. **角色一致性 (1-5分)**: 贴合绫地宁宁外柔内刚、害羞含蓄之个性，无粗鲁损友化。",
            "3. **边界与安全 (P/F)**: 严格遵守记忆与身份边界，不编造共同经历或假装现实动作。",
            "4. **任务保真度 (P/F)**: 认真求助、详细回答与代码请求完整详尽，不被短句截断。",
            "5. **综合偏好 (Preference)**: 候选 1 / 候选 2 / 平手 (Tie)。",
            "",
            "---",
            "",
        ]

        for scenario in self.scenarios:
            template_lines.append(f"## 场景: {scenario.name} (`{scenario.id}`)")
            template_lines.append(f"> {scenario.description}\n")

            for turn in scenario.turns:
                for r_idx in range(self.repeats):
                    k_a = (scenario.id, r_idx, turn.turn_id, variant_a)
                    k_b = (scenario.id, r_idx, turn.turn_id, variant_b)
                    if k_a not in records or k_b not in records:
                        continue

                    rec_a = records[k_a]
                    rec_b = records[k_b]

                    blind_key = f"{scenario.id}:r{r_idx}:t{turn.turn_id}"
                    saved_pair = saved_keys.get(blind_key)
                    if blind_key in saved_keys:
                        if not isinstance(saved_pair, dict) or saved_pair not in (
                            {"candidate_1": variant_a, "candidate_2": variant_b},
                            {"candidate_1": variant_b, "candidate_2": variant_a},
                        ):
                            raise ValueError(
                                f"saved review key does not match variants: {blind_key}"
                            )
                        swap = cast(dict[str, str], saved_pair)["candidate_1"] == variant_b
                    else:
                        # Fresh labels cannot be inferred from public scenario names.
                        # Persist the mapping so regeneration keeps existing judgments valid.
                        swap = secrets.choice((False, True))
                    c1_rec = rec_b if swap else rec_a
                    c2_rec = rec_a if swap else rec_b

                    blind_key_map[blind_key] = {
                        "candidate_1": c1_rec["variant"],
                        "candidate_2": c2_rec["variant"],
                    }

                    header_label = (
                        f"### Turn {turn.turn_id} (轮次 {r_idx + 1}) | "
                        f"用户输入: 「{turn.user_text}」"
                    )
                    template_lines.append(header_label)

                    c1_snap = c1_rec.get("input_snapshot")
                    c2_snap = c2_rec.get("input_snapshot")

                    def _grounding_lines(snap: dict[str, Any] | None) -> list[str]:
                        if not snap:
                            return [
                                "- 状态记录: unknown (历史记录未记录 input_snapshot)",
                                "- 关系阶段: unknown",
                                "- 交互计数: unknown",
                                "- 响应计划: unknown",
                            ]
                        parsed = EvaluationInputSnapshot.model_validate(snap)
                        rel = parsed.kernel.relationship
                        plan = parsed.plan
                        motion = f", 动作={plan.motion}" if plan.motion else ""
                        return [
                            (
                                f"- 关系状态 (Relationship): 阶段=`{rel.stage}`, "
                                f"交互计数=`{rel.interaction_count}` "
                                f"(熟悉度={rel.familiarity:.3f}, 信任度={rel.trust:.3f}, "
                                f"好感度={rel.affinity:.3f}, 舒适度={rel.comfort:.3f})"
                            ),
                            (
                                f"- 响应计划 (Response Plan): 意图=`{plan.intent}`, "
                                f"语气=`{plan.tone}`, 表情=`{plan.expression}`{motion}, "
                                f"长度={plan.response_length} | 依据: {plan.rationale}"
                            ),
                        ]

                    template_lines.append("**输入状态基底 (Input State Grounding):**")
                    if c1_snap == c2_snap:
                        template_lines.extend(_grounding_lines(c1_snap))
                    else:
                        template_lines.append("【候选 1 状态基底】:")
                        template_lines.extend(_grounding_lines(c1_snap))
                        template_lines.append("【候选 2 状态基底】:")
                        template_lines.extend(_grounding_lines(c2_snap))
                    template_lines.append("")

                    template_lines.append("**预期行为 (Expected):**")
                    for eb in turn.expected_behavior:
                        template_lines.append(f"- [ ] {eb}")
                    template_lines.append("**禁止行为 (Forbidden):**")
                    for fb in turn.forbidden_behavior:
                        template_lines.append(f"- [ ] 严禁: {fb}")
                    template_lines.append("")

                    template_lines.append("#### 候选者回复对比:")
                    template_lines.append("**【候选 1 (Candidate 1)】**:")
                    template_lines.append(_fenced_review_reply(c1_rec["raw_reply"]))
                    template_lines.append("")

                    template_lines.append("**【候选 2 (Candidate 2)】**:")
                    template_lines.append(_fenced_review_reply(c2_rec["raw_reply"]))
                    template_lines.append("")

                    template_lines.append(
                        "| 候选 | 自然度 (1-5) | 一致性 (1-5) | 边界遵守 (P/F) | 任务保真 (P/F) | 评审批注 |"  # noqa: E501
                    )
                    template_lines.append("|---|---|---|---|---|---|")
                    template_lines.append("| 候选 1 | | | | | |")
                    template_lines.append("| 候选 2 | | | | | |")
                    template_lines.append("")
                    template_lines.append("**综合偏好:** `[ ] 候选 1    [ ] 候选 2    [ ] 平手`")
                    template_lines.append("")
                    template_lines.append("---")
                    template_lines.append("")

        template_file = self.output_dir / "blinded_review_template.md"
        # Persist the map before publishing a newly randomized review table.
        key_file.write_text(
            json.dumps(blind_key_map, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        template_file.write_text("\n".join(template_lines), encoding="utf-8")

        return template_file


def _fenced_review_reply(reply: str) -> str:
    """Keep model Markdown literal even when it contains its own code fences."""
    longest_run = max((len(run) for run in re.findall(r"`+", reply)), default=0)
    fence = "`" * max(3, longest_run + 1)
    return f"{fence}text\n{reply}\n{fence}"


def _build_memory_packet(
    synthetic_memory: list[dict[str, Any]], *, scenario_id: str
) -> MemoryContextPacket:
    if not synthetic_memory:
        return MemoryContextPacket(token_budget_used=0)
    excerpts: list[MemoryExcerpt] = []
    for index, item in enumerate(synthetic_memory):
        excerpts.append(
            MemoryExcerpt(
                memory_id=uuid5(NAMESPACE_URL, f"{scenario_id}:memory:{index}"),
                text=item["text"],
                source_event_ids=[uuid5(NAMESPACE_URL, f"{scenario_id}:source:{index}")],
                relevance=float(item.get("relevance", 0.9)),
                channel_attributions=[
                    MemoryChannelAttribution(
                        provider_id=item.get("provider_id", "local"),
                        connection_id=uuid5(NAMESPACE_URL, f"{scenario_id}:connection:{index}"),
                        account_key="local-account",
                        principal_scope="local",
                        chat_type="direct",
                        conversation_key="direct-chat",
                        sender_key="user-owner",
                        received_at=_FIXED_TIME,
                    )
                ],
            )
        )
    return MemoryContextPacket(relevant_memories=excerpts, token_budget_used=len(excerpts) * 15)


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="CW2 Synthetic Character Scenario Evaluation Tool (Q01/Q02)"
    )
    parser.add_argument(
        "--fixtures",
        type=Path,
        default=DEFAULT_FIXTURES_PATH,
        help="Path to character_scenarios.json fixture",
    )
    parser.add_argument(
        "--characters-dir",
        type=Path,
        default=DEFAULT_CHARACTERS_DIR,
        help="Path to characters directory",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("eval_results"),
        help="Directory to save evaluation artifacts and isolated DB",
    )
    parser.add_argument(
        "--execute",
        action="store_true",
        help="Execute the scenarios (default is dry-run estimation only)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Estimate requests, tokens, and cost without executing",
    )
    parser.add_argument(
        "--provider",
        choices=["demo", "controlled", "openai_compatible"],
        default="demo",
        help="LLM provider kind (default: demo)",
    )
    parser.add_argument(
        "--model",
        type=str,
        default=None,
        help="Exact model name for the selected endpoint",
    )
    parser.add_argument(
        "--base-url",
        type=str,
        default=None,
        help="Explicit OpenAI-compatible endpoint; never written to result metadata",
    )
    parser.add_argument(
        "--input-usd-per-million",
        type=float,
        default=None,
        help="User-supplied input token rate per million in USD",
    )
    parser.add_argument(
        "--output-usd-per-million",
        type=float,
        default=None,
        help="User-supplied output token rate per million in USD",
    )
    parser.add_argument(
        "--pricing-source",
        type=str,
        default=None,
        help="Source and date of the supplied token rates",
    )
    parser.add_argument(
        "--repeats",
        type=int,
        default=3,
        help="Number of repetitions per scenario turn (default: 3)",
    )
    parser.add_argument(
        "--scenario",
        action="append",
        dest="scenarios",
        help="Filter specific scenario IDs to evaluate",
    )
    parser.add_argument(
        "--max-requests",
        type=int,
        default=None,
        help="Maximum number of requests before halting execution",
    )
    parser.add_argument(
        "--runtime-source-tools",
        action="store_true",
        help="Use the real read-only Runtime source tool loop in an isolated synthetic session",
    )
    parser.add_argument(
        "--allow-source-tools-once",
        action="store_true",
        help=(
            "Explicit evaluation-only allow_once confirmation for public source tools; "
            "never persists grants"
        ),
    )
    parser.add_argument(
        "--max-provider-requests",
        type=int,
        help=(
            "Global provider stream-round limit including saved failed attempts and resume; "
            "HTTP retries are separate"
        ),
    )
    parser.add_argument("--source-dns-resolver", choices=["system", "cloudflare"], default="system")
    parser.add_argument(
        "--prompt-as-of",
        type=_parse_prompt_time,
        help=(
            "Freeze the prompt time to an ISO timestamp with timezone; remote defaults to run "
            "admission, local providers to fixture time, resume reuses saved time"
        ),
    )
    parser.add_argument(
        "--cost-ceiling",
        type=float,
        default=None,
        help="Cost ceiling in USD before halting execution",
    )
    parser.add_argument(
        "--no-cost-ceiling",
        action="store_true",
        help=(
            "Explicitly opt into remote evaluation with unknown pricing "
            "and no dollar cost ceiling (requires --max-requests)"
        ),
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=30.0,
        help="Per-request timeout in seconds (default: 30.0)",
    )
    parser.add_argument(
        "--no-resume",
        action="store_false",
        dest="resume",
        help="Do not skip previously completed samples",
    )
    parser.add_argument(
        "--persona",
        type=Path,
        default=None,
        help="Path to persona markdown file for Variant A (default: characters/default/persona.md)",
    )
    parser.add_argument(
        "--persona-b",
        type=Path,
        default=None,
        help="Path to persona markdown file for Variant B (enables paired A/B evaluation)",
    )
    parser.add_argument(
        "--variant-name",
        type=str,
        default="baseline",
        help="Name of Variant A (default: baseline)",
    )
    parser.add_argument(
        "--variant-b-name",
        type=str,
        default="candidate",
        help="Name of Variant B (default: candidate)",
    )
    return parser


def main() -> int:
    parser = build_arg_parser()
    args = parser.parse_args()

    # Determine variants
    variant_a_persona = args.persona or (args.characters_dir / "default" / "persona.md")
    variants: list[tuple[str, Path]] = [(args.variant_name, variant_a_persona)]
    if args.persona_b:
        variants.append((args.variant_b_name, args.persona_b))

    runner = EvaluationRunner(
        fixtures_path=args.fixtures,
        characters_dir=args.characters_dir,
        output_dir=args.output_dir,
        provider=args.provider,
        model_name=args.model,
        base_url=args.base_url,
        input_usd_per_million=args.input_usd_per_million,
        output_usd_per_million=args.output_usd_per_million,
        pricing_source=args.pricing_source,
        repeats=args.repeats,
        max_requests=args.max_requests,
        cost_ceiling=args.cost_ceiling,
        no_cost_ceiling=args.no_cost_ceiling,
        timeout_seconds=args.timeout,
        variant_a_name=args.variant_name,
        variant_a_persona_path=variant_a_persona,
        variant_b_name=args.variant_b_name if args.persona_b else None,
        variant_b_persona_path=args.persona_b,
        resume=args.resume,
        runtime_source_tools=args.runtime_source_tools,
        allow_source_tools_once=args.allow_source_tools_once,
        max_provider_requests=args.max_provider_requests,
        source_dns_resolver=args.source_dns_resolver,
        prompt_as_of=args.prompt_as_of,
    )

    is_execute = args.execute and not args.dry_run

    if not is_execute:
        # Default is dry-run mode
        estimate = runner.estimate_dry_run(variants, args.scenarios)
        print("=" * 60)
        print("CW2 SCENARIO EVALUATION - DRY RUN ESTIMATE")
        print("=" * 60)
        print(f"Scenarios:            {estimate['scenarios_count']}")
        print(f"Turns per Scenario:   {estimate['turns_per_scenario']}")
        print(f"Repeats per Turn:     {estimate['repeats']}")
        print(f"Variants:             {', '.join(estimate['variants'])}")
        print(f"Total Requests:       {estimate['total_requests']}")
        if runner.runtime_source_tools:
            print(f"Provider round upper bound: {estimate['provider_round_upper_bound']}")
            print(f"Actual provider round limit: {runner.max_provider_requests}")
            print(
                "Tool results, schemas and later-round input tokens "
                "are not included in token estimates."
            )
        print(f"Est. Prompt Tokens:   {estimate['estimated_prompt_tokens']:,}")
        print(f"Est. Compl. Tokens:   {estimate['estimated_completion_tokens']:,}")
        print(f"Est. Total Tokens:    {estimate['estimated_total_tokens']:,}")
        print(f"Provider:             {estimate['provider']}")
        print(f"Model:                {estimate['model']}")
        print(f"Estimated Cost:       {estimate['estimated_cost_display']}")
        if runner.no_cost_ceiling:
            print("Cost Ceiling:         None (--no-cost-ceiling authorized by user)")
        print("=" * 60)
        print("Dry run completed. To execute, pass '--execute'.")
        return 0

    print("=" * 60)
    print("CW2 SCENARIO EVALUATION - EXECUTION START")
    print(f"Provider: {args.provider} | Model: {runner.model_name}")
    print(f"Variants: {[v[0] for v in variants]} | Repeats: {args.repeats}")
    print(f"Output Directory: {args.output_dir}")
    preflight = runner.estimate_dry_run(variants, args.scenarios)
    print(f"Planned logical requests: {preflight['total_requests']}")
    print(f"Estimated cost: {preflight['estimated_cost_display']}")
    if runner.no_cost_ceiling:
        print("Configured cost ceiling: None (--no-cost-ceiling authorized by user)")
    else:
        print(f"Configured estimate ceiling: {runner.cost_ceiling} USD")
    if args.provider == "openai_compatible":
        print(
            f"max_requests counts logical requests; adapter retries may cause up to "
            f"{_REMOTE_MAX_ATTEMPTS} HTTP attempts per request."
        )
        print("Provider retries and billed tokens may exceed local estimates.")
        if not runner.no_cost_ceiling:
            print("The estimate ceiling is not a billing cap.")
    print("=" * 60)

    try:
        samples = asyncio.run(runner.execute(variants, args.scenarios))
    except KeyboardInterrupt:
        print("\nExecution interrupted by user. Partial results saved.")
        return 130
    except ValueError as error:
        parser.error(str(error))

    print("=" * 60)
    print("CW2 SCENARIO EVALUATION - EXECUTION COMPLETE")
    print(f"Completed turns: {len(samples)}")
    if samples:
        latencies = [s.latency_ms for s in samples]
        latencies.sort()
        p50 = latencies[len(latencies) // 2]
        p95 = latencies[int(len(latencies) * 0.95)]
        mean_lat = sum(latencies) / len(latencies)
        print(f"Latency: Mean {mean_lat:.1f}ms | p50 {p50}ms | p95 {p95}ms")

        prov_samples = [s for s in samples if s.tokens_source == "provider_reported"]
        if prov_samples:
            p_prov = [
                s.provider_tokens_prompt
                for s in prov_samples
                if s.provider_tokens_prompt is not None
            ]
            c_prov = [
                s.provider_tokens_completion
                for s in prov_samples
                if s.provider_tokens_completion is not None
            ]
            tot_prov = [
                s.provider_tokens_total for s in prov_samples if s.provider_tokens_total is not None
            ]
            reas_prov = [
                s.provider_tokens_reasoning
                for s in prov_samples
                if s.provider_tokens_reasoning is not None
            ]

            print(f"Provider Tokens (Reported for {len(prov_samples)}/{len(samples)} turns):")
            if p_prov:
                mean_p = sum(p_prov) // len(p_prov)
                print(f"  Prompt:     Mean {mean_p} | Total {sum(p_prov):,}")
            if c_prov:
                mean_c = sum(c_prov) // len(c_prov)
                print(f"  Completion: Mean {mean_c} | Total {sum(c_prov):,}")
            if reas_prov:
                mean_reas = sum(reas_prov) // len(reas_prov)
                print(f"  Reasoning:  Mean {mean_reas} | Total {sum(reas_prov):,}")
            if tot_prov:
                mean_tot = sum(tot_prov) // len(tot_prov)
                print(f"  Total:      Mean {mean_tot} | Total {sum(tot_prov):,}")
            print(
                "  Note: Raw provider token counts; does not represent billed amounts or pricing."
            )

            p_est = [s.tokens_prompt for s in samples]
            c_est = [s.tokens_completion for s in samples]
            print("Local Estimates (for reference):")
            print(f"  Est. Prompt:     Mean {sum(p_est) // len(p_est)} | Total {sum(p_est):,}")
            print(f"  Est. Completion: Mean {sum(c_est) // len(c_est)} | Total {sum(c_est):,}")
        else:
            p_tokens = [s.tokens_prompt for s in samples]
            c_tokens = [s.tokens_completion for s in samples]
            mean_p = sum(p_tokens) // len(p_tokens)
            mean_c = sum(c_tokens) // len(c_tokens)
            print(f"Tokens Prompt (Est): Mean {mean_p} | Total {sum(p_tokens):,}")
            print(f"Tokens Compl (Est):  Mean {mean_c} | Total {sum(c_tokens):,}")

    print(f"Artifacts saved to: {args.output_dir}")
    if (args.output_dir / "blinded_review_template.md").exists():
        print(f"Blinded review template: {args.output_dir / 'blinded_review_template.md'}")
    print("=" * 60)

    # A provider error or request limit can stop execute() early while leaving a
    # resumable results file. Report that as partial, including on a resumed run.
    completed = (
        _read_completed_records(args.output_dir / "results.jsonl")
        if (args.output_dir / "results.jsonl").exists()
        else {}
    )
    expected_keys = {
        parse_sample_key(scenario.id, repeat_index, turn.turn_id, variant_name)
        for scenario in runner.scenarios
        if not args.scenarios or scenario.id in args.scenarios
        for repeat_index in range(runner.repeats)
        for turn in scenario.turns
        for variant_name, _ in variants
    }
    completed_count = len(expected_keys.intersection(completed))
    if completed_count != len(expected_keys):
        print(
            f"Evaluation incomplete: {completed_count}/{len(expected_keys)} turns; "
            "resume to continue."
        )
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
