"""Conversation coordination values shared by domain collaborators."""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal, cast
from uuid import UUID

from chatwaifu_protocol.session import GenerationState

from chatwaifu_runtime.providers.contracts import LlmInputImage

type ConversationOrigin = Literal["local_text", "voice", "proactive", "external_channel"]
type ConversationOutputMode = Literal["text", "audio", "avatar"]
type ConversationChatType = Literal["direct", "group"]


@dataclass(frozen=True, slots=True)
class ConversationSourceContext:
    """Durable origin metadata for cross-surface character continuity.

    Stable keys identify the route and sender. Optional labels are display-only
    data supplied by an external network and must never participate in access
    control, idempotency, or prompt instructions.
    """

    provider_id: str
    connection_id: UUID
    account_key: str | None
    principal_scope: str
    chat_type: ConversationChatType
    conversation_key: str
    sender_key: str
    received_at: datetime | None = None
    conversation_label: str | None = None
    sender_display_name: str | None = None

    def as_dict(self) -> dict[str, object]:
        return {
            "provider_id": self.provider_id,
            "connection_id": str(self.connection_id),
            "account_key": self.account_key,
            "principal_scope": self.principal_scope,
            "chat_type": self.chat_type,
            "conversation_key": self.conversation_key,
            "sender_key": self.sender_key,
            "received_at": self.received_at.isoformat() if self.received_at is not None else None,
            "conversation_label": self.conversation_label,
            "sender_display_name": self.sender_display_name,
        }

    def to_json(self) -> str:
        return json.dumps(self.as_dict(), ensure_ascii=False, separators=(",", ":"))

    @classmethod
    def from_json(cls, value: str) -> ConversationSourceContext:
        raw_payload: object = json.loads(value)
        if not isinstance(raw_payload, dict):
            raise ValueError("conversation source context must be an object")
        payload = cast(dict[str, object], raw_payload)
        chat_type = str(payload["chat_type"])
        if chat_type not in {"direct", "group"}:
            raise ValueError("unsupported conversation chat type")
        return cls(
            provider_id=str(payload["provider_id"]),
            connection_id=UUID(str(payload["connection_id"])),
            account_key=(
                str(payload["account_key"]) if payload.get("account_key") is not None else None
            ),
            # V1 sessions have one local owner scope. Keeping this fallback
            # makes source rows written before attribution v1 readable; future
            # multi-principal sessions must persist their scope explicitly.
            principal_scope=str(payload.get("principal_scope", "local")),
            chat_type=cast(ConversationChatType, chat_type),
            conversation_key=str(payload["conversation_key"]),
            sender_key=str(payload["sender_key"]),
            received_at=(
                datetime.fromisoformat(str(payload["received_at"]))
                if payload.get("received_at") is not None
                else None
            ),
            conversation_label=(
                str(payload["conversation_label"])
                if payload.get("conversation_label") is not None
                else None
            ),
            sender_display_name=(
                str(payload["sender_display_name"])
                if payload.get("sender_display_name") is not None
                else None
            ),
        )


@dataclass(frozen=True, slots=True)
class ConversationUserInputContext:
    """Bounded committed input, with at most one adjacent input from the same route."""

    user_text: str
    previous_user_text: str | None = None


@dataclass(frozen=True, slots=True)
class ConversationHistoryEntry:
    role: str
    text: str
    source_context: ConversationSourceContext | None = None
    generation_id: UUID | None = None


@dataclass(frozen=True, slots=True)
class ConversationTurnOptions:
    """Surface-neutral controls for one submitted conversation turn.

    The conversation domain owns generation. Callers may negotiate which output
    surfaces are meaningful without teaching the pipeline about Web, desktop, or
    any particular external messaging network.
    """

    origin: ConversationOrigin = "local_text"
    output_modes: frozenset[ConversationOutputMode] = frozenset({"text", "audio", "avatar"})
    allow_tools: bool = True
    source_context: ConversationSourceContext | None = None
    presentation_profile: str | None = None
    failure_recovery_text: str | None = None
    image_loader: Callable[[], Awaitable[LlmInputImage]] | None = field(
        default=None, repr=False, compare=False
    )

    def emits(self, mode: ConversationOutputMode) -> bool:
        return mode in self.output_modes


EXTERNAL_TEXT_TURN_OPTIONS = ConversationTurnOptions(
    origin="external_channel",
    output_modes=frozenset({"text"}),
    # External channels do not yet have a safe confirmation surface. Keeping
    # tools disabled avoids a remote message silently triggering side effects.
    allow_tools=False,
)


@dataclass(frozen=True, slots=True)
class GenerationAccepted:
    session_id: UUID
    turn_id: UUID
    generation_id: UUID
    audio_stream_id: UUID
    state: GenerationState


@dataclass(frozen=True, slots=True)
class SessionDataReset:
    session_id: UUID
    character_id: str
    user_scope: str
    turns_deleted: int
    events_deleted: int
    memories_deleted: int
    audio_assets_deleted: int
    audio_assets_pending_cleanup: int
    audio_cleanup_complete: bool
