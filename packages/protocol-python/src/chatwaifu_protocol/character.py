"""Provider- and renderer-independent Character Kernel contracts."""

from __future__ import annotations

import hashlib
import json
from typing import Literal
from uuid import UUID

from pydantic import AwareDatetime, Field, model_validator

from chatwaifu_protocol.base import ProtocolModel


class AffectState(ProtocolModel):
    valence: float = Field(default=0.15, ge=-1, le=1)
    arousal: float = Field(default=0.25, ge=0, le=1)
    energy: float = Field(default=0.65, ge=0, le=1)
    attention: float = Field(default=0.7, ge=0, le=1)
    embarrassment: float = Field(default=0.1, ge=0, le=1)
    tension: float = Field(default=0.05, ge=0, le=1)
    updated_at: AwareDatetime


class RelationshipState(ProtocolModel):
    familiarity: float = Field(default=0.2, ge=0, le=1)
    trust: float = Field(default=0.2, ge=0, le=1)
    affinity: float = Field(default=0.25, ge=0, le=1)
    comfort: float = Field(default=0.2, ge=0, le=1)
    recent_tension: float = Field(default=0, ge=0, le=1)
    interaction_count: int = Field(default=0, ge=0)
    stage: Literal["acquaintance", "familiar", "trusted", "close"] = "acquaintance"
    preferred_address: str | None = Field(default=None, max_length=80)
    updated_at: AwareDatetime


class CharacterKernelSnapshot(ProtocolModel):
    character_id: str = Field(min_length=1, max_length=128)
    user_scope: str = Field(min_length=1, max_length=128)
    revision: int = Field(ge=0)
    affect: AffectState
    relationship: RelationshipState


class ResponsePlan(ProtocolModel):
    intent: Literal["comfort", "answer", "celebrate", "reassure", "tease", "curious"]
    tone: Literal["gentle", "bright", "shy", "serious", "playful", "concerned"]
    expression: Literal["neutral", "happy", "sad", "angry", "surprised", "shy", "curious"]
    motion: Literal["headpat", "stare", "flustered", "sing"] | None = None
    response_length: Literal["short", "normal"] = "normal"
    rationale: str = Field(min_length=1, max_length=500)


class ModelContextBudget(ProtocolModel):
    """Operator-selected limits, not an assertion of provider capabilities."""

    version: Literal["1.0"] = "1.0"
    input_token_limit: int | None = Field(default=None, ge=1, le=2_000_000)
    output_token_limit: int | None = Field(default=None, ge=1, le=2_000_000)
    output_reserve_tokens: int = Field(default=900, ge=0, le=2_000_000)
    max_output_tokens: int | None = Field(default=None, ge=1, le=2_000_000)
    estimate_margin_ratio: float = Field(default=0, ge=0, le=1)
    section_policy: Literal["legacy", "scaled"] = "legacy"
    history_turn_limit: int = Field(default=16, ge=1, le=128)
    memory_candidate_limit: int = Field(default=12, ge=1, le=64)
    tool_result_max_bytes: int = Field(default=32_768, ge=1024, le=1_048_576)

    @model_validator(mode="after")
    def validate_output_plan(self) -> ModelContextBudget:
        if self.max_output_tokens is not None:
            if self.max_output_tokens > self.output_reserve_tokens:
                raise ValueError("requested output exceeds its reserved budget")
            if (
                self.output_token_limit is not None
                and self.max_output_tokens > self.output_token_limit
            ):
                raise ValueError("requested output exceeds the configured model output limit")
        return self


class PromptBudgetReport(ProtocolModel):
    model_role: Literal["chat", "memory_extraction", "memory_summary", "embedding"]
    budget: int = Field(ge=1)
    used: int = Field(ge=0)
    safety_tokens: int = Field(ge=0)
    persona_tokens: int = Field(ge=0)
    state_tokens: int = Field(ge=0)
    relationship_tokens: int = Field(ge=0)
    memory_tokens: int = Field(ge=0)
    scene_tokens: int = Field(ge=0)
    conversation_tokens: int = Field(ge=0)
    dropped_history_turns: int = Field(ge=0)
    estimator: Literal["character_half_v1"] = "character_half_v1"
    output_reserve_tokens: int = Field(default=900, ge=0)
    estimate_margin_ratio: float = Field(default=0, ge=0, le=1)
    section_budget_limits: dict[str, int] = Field(default_factory=dict)
    persona_omitted_characters: int = Field(default=0, ge=0)
    photo_omitted_characters: int = Field(default=0, ge=0)
    summary_omitted_characters: int = Field(default=0, ge=0)
    omitted_memory_ids: list[UUID] = Field(default_factory=list[UUID])


PROMPT_TEMPLATE_VERSION: str = "v12"


class NonsecretModelRoute(ProtocolModel):
    role: str = Field(min_length=1, max_length=64)
    provider: str = Field(min_length=1, max_length=64)
    model: str = Field(min_length=1, max_length=256)
    endpoint_digest: str | None = Field(default=None, min_length=64, max_length=64)
    context_window: int = Field(default=8192, ge=1024, le=2_000_000)
    budget: ModelContextBudget = Field(default_factory=ModelContextBudget)


def compute_prompt_context_identity_hash(
    *,
    character_id: str,
    character_package_hash: str,
    prompt_template_version: str,
    presentation_profile: str,
    chat_route: NonsecretModelRoute,
    memory_summary_route: NonsecretModelRoute,
    tools_digest: str,
) -> str:
    def route_payload(route: NonsecretModelRoute) -> dict[str, object]:
        data: dict[str, object] = {
            "role": route.role,
            "provider": route.provider,
            "model": route.model,
            "endpoint_digest": route.endpoint_digest,
            "context_window": route.context_window,
        }
        # Preserve identities of archived legacy routes. Non-default budget
        # settings are part of identity, never silently shared across models.
        if route.budget != ModelContextBudget():
            data["budget"] = route.budget.model_dump(mode="json")
        return data

    canonical = json.dumps(
        {
            "character_id": character_id,
            "character_package_hash": character_package_hash,
            "prompt_template_version": prompt_template_version,
            "presentation_profile": presentation_profile,
            "chat_route": route_payload(chat_route),
            "memory_summary_route": route_payload(memory_summary_route),
            "tools_digest": tools_digest,
        },
        sort_keys=True,
        ensure_ascii=False,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


class PromptContextIdentity(ProtocolModel):
    schema_version: Literal["1.0"] = "1.0"
    identity_hash: str = Field(min_length=16, max_length=64)
    character_id: str = Field(min_length=1, max_length=128)
    character_package_hash: str = Field(min_length=16, max_length=64)
    prompt_template_version: str = Field(min_length=1, max_length=64)
    presentation_profile: str = Field(default="default", max_length=64)
    chat_route: NonsecretModelRoute
    memory_summary_route: NonsecretModelRoute
    tools_digest: str = Field(min_length=16, max_length=64)

    @classmethod
    def create(
        cls,
        *,
        character_id: str,
        character_package_hash: str,
        prompt_template_version: str = PROMPT_TEMPLATE_VERSION,
        presentation_profile: str | None = None,
        chat_route: NonsecretModelRoute,
        memory_summary_route: NonsecretModelRoute,
        tools_digest: str,
    ) -> PromptContextIdentity:
        profile = presentation_profile or "default"
        identity_hash = compute_prompt_context_identity_hash(
            character_id=character_id,
            character_package_hash=character_package_hash,
            prompt_template_version=prompt_template_version,
            presentation_profile=profile,
            chat_route=chat_route,
            memory_summary_route=memory_summary_route,
            tools_digest=tools_digest,
        )
        return cls(
            schema_version="1.0",
            identity_hash=identity_hash,
            character_id=character_id,
            character_package_hash=character_package_hash,
            prompt_template_version=prompt_template_version,
            presentation_profile=profile,
            chat_route=chat_route,
            memory_summary_route=memory_summary_route,
            tools_digest=tools_digest,
        )


class CharacterPromptCompiledPayload(ProtocolModel):
    schema_version: Literal["1.0"] = "1.0"
    report: PromptBudgetReport
    identity: PromptContextIdentity | None = None
    selected_memory_ids: list[UUID] | None = None
