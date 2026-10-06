"""Immutable conversation identity resolved only from a persisted Runtime session."""

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class TrustedConversationIdentity:
    participant_id: str
    scene_id: str | None
    audience_ids: tuple[str, ...]
    memory_scope: str
    state_scope: str
