"""Conversation coordination values shared by domain collaborators."""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import datetime
from typing import TYPE_CHECKING, Literal, cast
from uuid import UUID

from chatwaifu_protocol.character import PromptContextIdentity
from chatwaifu_protocol.session import GenerationState

from chatwaifu_runtime.providers.contracts import LlmInputImage, LlmProvider
from chatwaifu_runtime.providers.model_config import ModelRoleConfig

if TYPE_CHECKING:
    from chatwaifu_runtime.agent.tool_calling import ProjectedAgentTool
    from chatwaifu_runtime.sessions.identity import TrustedConversationIdentity

type ConversationOrigin = Literal["local_text", "voice", "proactive", "external_channel"]
type ConversationOutputMode = Literal["text", "audio", "avatar"]
type ConversationChatType = Literal["direct", "group"]

REDACTED_ASSISTANT_PLACEHOLDER: str = "[Photo response omitted]"


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
    audience_ids: tuple[str, ...] = ()
    reply_to_external_message_id: str | None = None
    outbound_intent_id: UUID | None = None
    source_event_key: str | None = None
    policy_revision: int | None = None
    route_revision: int | None = None
    group_route_id: UUID | None = None
    participant_id: str | None = None
    scene_id: str | None = None

    def __post_init__(self) -> None:
        if self.group_route_id is not None:
            if (
                not isinstance(self.group_route_id, UUID)
                or self.chat_type != "group"
                or not isinstance(self.participant_id, str)
                or not self.participant_id
                or not isinstance(self.scene_id, str)
                or not self.scene_id
                or self.principal_scope != f"scene:{self.scene_id}"
                or self.participant_id not in self.audience_ids
                or type(self.route_revision) is not int
                or self.route_revision < 0
                or self.outbound_intent_id is not None
                or self.source_event_key is not None
                or self.policy_revision is not None
                or self.reply_to_external_message_id is not None
            ):
                raise ValueError("group source requires complete trusted identity and route")
            return
        if self.participant_id is not None or self.scene_id is not None:
            raise ValueError("member identity requires a fixed group route")
        metadata = (
            self.outbound_intent_id,
            self.source_event_key,
            self.policy_revision,
            self.route_revision,
        )
        if all(value is None for value in metadata):
            return
        if (
            not isinstance(self.outbound_intent_id, UUID)
            or not isinstance(self.source_event_key, str)
            or not 1 <= len(self.source_event_key) <= 256
            or any(ord(char) < 32 for char in self.source_event_key)
            or type(self.policy_revision) is not int
            or self.policy_revision < 0
            or type(self.route_revision) is not int
            or self.route_revision < 0
            or self.received_at is not None
            or self.reply_to_external_message_id is not None
        ):
            raise ValueError("outbound source requires complete trusted lineage metadata")

    def as_dict(self) -> dict[str, object]:
        result: dict[str, object] = {
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
            "audience_ids": list(self.audience_ids),
            "reply_to_external_message_id": self.reply_to_external_message_id,
        }
        if self.outbound_intent_id is not None:
            result.update(
                outbound_intent_id=str(self.outbound_intent_id),
                source_event_key=self.source_event_key,
                policy_revision=self.policy_revision,
                route_revision=self.route_revision,
            )
        if self.group_route_id is not None:
            result.update(
                group_route_id=str(self.group_route_id),
                route_revision=self.route_revision,
                participant_id=self.participant_id,
                scene_id=self.scene_id,
            )
        return result

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
            group_route_id=(
                UUID(str(payload["group_route_id"]))
                if payload.get("group_route_id") is not None
                else None
            ),
            participant_id=cast(str | None, payload.get("participant_id")),
            scene_id=cast(str | None, payload.get("scene_id")),
            outbound_intent_id=(
                UUID(str(payload["outbound_intent_id"]))
                if payload.get("outbound_intent_id") is not None
                else None
            ),
            source_event_key=(
                str(payload["source_event_key"])
                if payload.get("source_event_key") is not None
                else None
            ),
            policy_revision=cast(int | None, payload.get("policy_revision")),
            route_revision=cast(int | None, payload.get("route_revision")),
            reply_to_external_message_id=(
                str(payload["reply_to_external_message_id"])
                if payload.get("reply_to_external_message_id") is not None
                else None
            ),
            audience_ids=tuple(
                str(item) for item in cast(list[object], payload.get("audience_ids", []))
            ),
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
class ConfirmedConversationTurn:
    """Typed snapshot of a finalized user turn or confirmed spoken assistant turn.

    Only finalized user text and confirmed spoken assistant text (supported by
    a durable presentation fact) are eligible. No uncommitted user text,
    unheard assistant output, tool calls, or raw PCM is ever included.
    """

    turn_id: UUID
    role: Literal["user", "assistant"]
    text: str
    generation_id: UUID | None = None
    created_at: datetime | None = None
    is_redacted: bool = False


@dataclass(frozen=True, slots=True)
class ConversationQuotedMessage:
    """Permissioned historical text, never a fresh instruction or tool authorization."""

    role: Literal["user", "assistant"]
    text: str
    source_generation_id: UUID

    def __post_init__(self) -> None:
        if self.role not in {"user", "assistant"} or not 1 <= len(self.text) <= 2000:
            raise ValueError("quoted message requires a supported role and bounded text")


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
    allowed_skill_ids: frozenset[str] | None = None
    contextual_skill_ids: frozenset[str] = frozenset()
    source_context: ConversationSourceContext | None = None
    trusted_identity: TrustedConversationIdentity | None = None
    presentation_profile: str | None = None
    failure_recovery_text: str | None = None
    image_loader: Callable[[], Awaitable[LlmInputImage | tuple[LlmInputImage, ...]]] | None = field(
        default=None, repr=False, compare=False
    )
    quoted_message_loader: Callable[[], Awaitable[ConversationQuotedMessage | None]] | None = field(
        default=None, repr=False, compare=False
    )
    before_generation: Callable[[], Awaitable[bool]] | None = field(
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


@dataclass(frozen=True, slots=True)
class GenerationContextSnapshot:
    """Immutable per-generation configuration and context snapshot captured at admission."""

    chat_config: ModelRoleConfig
    memory_summary_config: ModelRoleConfig
    chat_provider: LlmProvider
    visible_tools: tuple[ProjectedAgentTool, ...]
    identity: PromptContextIdentity
    admitted_at: datetime
