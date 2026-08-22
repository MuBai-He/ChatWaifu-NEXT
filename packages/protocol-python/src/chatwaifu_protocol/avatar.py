"""Renderer-independent avatar contracts."""

from typing import Literal
from uuid import UUID

from pydantic import Field

from chatwaifu_protocol.base import JsonObject, ProtocolModel


class AvatarCue(ProtocolModel):
    cue_id: UUID
    generation_id: UUID | None = None
    kind: Literal["state", "expression", "motion", "gaze", "speech", "override"]
    name: str = Field(min_length=1, max_length=120)
    intensity: float = Field(default=1.0, ge=0.0, le=1.0)
    start_anchor: str = "immediate"
    duration_ms: int | None = Field(default=None, ge=0)
    priority: int = Field(default=50, ge=0, le=100)
    interruptible: bool = True
    metadata: JsonObject = Field(default_factory=dict)


class AvatarCapabilityManifest(ProtocolModel):
    avatar_id: str = Field(min_length=1)
    renderer_kind: str = Field(min_length=1)
    states: list[str] = Field(default_factory=list)
    expressions: list[str] = Field(default_factory=list)
    motions: list[str] = Field(default_factory=list)
    gaze_targets: list[str] = Field(default_factory=list)
    hit_areas: list[str] = Field(default_factory=list)
    supports_lipsync: bool = False


class AvatarInteractionEvent(ProtocolModel):
    interaction_id: UUID
    avatar_id: str
    kind: Literal["pointer", "touch", "gaze", "drag", "system"]
    target: str | None = None
    x: float | None = None
    y: float | None = None
    metadata: JsonObject = Field(default_factory=dict)
