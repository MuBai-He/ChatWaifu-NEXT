"""Provider- and renderer-independent Character Kernel contracts."""

from typing import Literal

from pydantic import AwareDatetime, Field

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
