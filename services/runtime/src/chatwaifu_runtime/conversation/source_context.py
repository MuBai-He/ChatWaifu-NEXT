"""Read-only, versioned public-source receipts for eligible conversation history."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal, Protocol
from uuid import UUID

from chatwaifu_protocol.skills import SkillRunSnapshot

if TYPE_CHECKING:
    from chatwaifu_runtime.conversation.models import (
        ConversationHistoryEntry,
        ConversationSourceContext,
    )

MAX_SOURCE_GENERATIONS = 8
MAX_SOURCE_RECEIPTS = 32


@dataclass(frozen=True, slots=True)
class SourceContextReceipt:
    run: SkillRunSnapshot
    original_result_available: bool


@dataclass(frozen=True, slots=True)
class SourceContextPacket:
    receipts: tuple[SourceContextReceipt, ...] = ()
    truncated: bool = False
    schema_version: Literal["1.0"] = "1.0"


class SourceContextPort(Protocol):
    async def load_source_context(
        self, session_id: UUID, generation_ids: tuple[UUID, ...]
    ) -> SourceContextPacket: ...


def _route(source: ConversationSourceContext | None) -> tuple[object, ...] | None:
    if source is None:
        return None
    return (
        source.provider_id,
        source.connection_id,
        source.account_key,
        source.principal_scope,
        source.chat_type,
        source.conversation_key,
        source.sender_key,
        tuple(sorted(source.audience_ids)),
    )


def source_generation_ids(
    history: tuple[ConversationHistoryEntry, ...],
    source_context: ConversationSourceContext | None,
) -> tuple[UUID, ...]:
    """Labels/time never grant access; redacted or other-route history is ineligible."""
    from chatwaifu_runtime.conversation.models import REDACTED_ASSISTANT_PLACEHOLDER

    selected: list[UUID] = []
    for entry in reversed(history):
        if (
            entry.role == "assistant"
            and entry.generation_id is not None
            and entry.text != REDACTED_ASSISTANT_PLACEHOLDER
            and _route(entry.source_context) == _route(source_context)
            and entry.generation_id not in selected
        ):
            selected.append(entry.generation_id)
            if len(selected) == MAX_SOURCE_GENERATIONS:
                break
    return tuple(selected)
