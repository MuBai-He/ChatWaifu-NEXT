"""Versioned, provider-neutral contracts for external messaging channels.

Channel adapters translate provider SDK payloads into these DTOs. Provider-only
transport state (for example provider tokens, sync cursors, or reply context
tokens) deliberately stays inside the adapter. The local settings client sees
only the provider-neutral authorization lifecycle declared below.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Annotated, Literal
from uuid import UUID

from pydantic import AwareDatetime, Field, model_validator

from chatwaifu_protocol.base import ProtocolModel
from chatwaifu_protocol.errors import StructuredError


class ChannelVersionedModel(ProtocolModel):
    """Base for the External Channel Gateway v1 HTTP boundary."""

    schema_version: Literal["1.0"] = "1.0"


class ChannelChatType(StrEnum):
    """Conversation shapes understood by this schema major."""

    DIRECT = "direct"
    GROUP = "group"


class ChannelMessageKind(StrEnum):
    """Message payload kinds understood by this schema major."""

    TEXT = "text"


class ChannelConnectionStatus(StrEnum):
    UNTESTED = "untested"
    READY = "ready"
    DEGRADED = "degraded"
    ERROR = "error"
    DISABLED = "disabled"


class ChannelGatewayStatus(StrEnum):
    READY = "ready"
    DEGRADED = "degraded"
    ERROR = "error"


class ChannelAuthorizationMethod(StrEnum):
    """Interactive authorization methods exposed to trusted local clients."""

    QR_CODE = "qr_code"


class ChannelAuthorizationStatus(StrEnum):
    """Provider-neutral state for one short-lived authorization session."""

    PENDING = "pending"
    SCANNED = "scanned"
    VERIFICATION_REQUIRED = "verification_required"
    CONFIRMED = "confirmed"
    EXPIRED = "expired"
    CANCELLED = "cancelled"
    FAILED = "failed"


class ChannelTurnStatus(StrEnum):
    ACCEPTED = "accepted"
    PROCESSING = "processing"
    COMPLETED = "completed"
    CANCELLING = "cancelling"
    CANCELLED = "cancelled"
    FAILED = "failed"
    TIMED_OUT = "timed_out"


class ChannelDeliveryStatus(StrEnum):
    PENDING = "pending"
    SENDING = "sending"
    DELIVERED = "delivered"
    FAILED = "failed"
    CANCELLED = "cancelled"


def _empty_authorization_methods() -> list[ChannelAuthorizationMethod]:
    return []


class ChannelProviderCapabilities(ProtocolModel):
    """Transport behavior advertised by one provider adapter.

    The first provider advertises only direct text by default. ``group`` is a
    protocol capability for future adapters, but a Runtime must reject it until
    its provider registration and local policy explicitly enable group chat.
    """

    chat_types: list[ChannelChatType] = Field(
        default_factory=lambda: [ChannelChatType.DIRECT], min_length=1
    )
    inbound_message_kinds: list[ChannelMessageKind] = Field(
        default_factory=lambda: [ChannelMessageKind.TEXT], min_length=1
    )
    outbound_message_kinds: list[ChannelMessageKind] = Field(
        default_factory=lambda: [ChannelMessageKind.TEXT], min_length=1
    )
    authorization_methods: list[ChannelAuthorizationMethod] = Field(
        default_factory=_empty_authorization_methods,
        max_length=8,
        description=(
            "Interactive authorization methods implemented by the local adapter; "
            "an empty list means connection provisioning is external"
        ),
    )
    supports_typing: bool = False
    supports_partial_replies: bool = False
    supports_delivery_ack: bool = True
    supports_cancellation: bool = True
    supports_proactive_messages: bool = False
    max_text_chars: int = Field(default=20_000, ge=1, le=1_000_000)


class ChannelProviderRegistration(ChannelVersionedModel):
    """Discoverable definition for a provider-neutral channel adapter."""

    provider_id: str = Field(pattern=r"^[a-z0-9][a-z0-9._-]{1,127}$")
    version: str = Field(pattern=r"^\d+\.\d+\.\d+(?:[-+][0-9A-Za-z.-]+)?$")
    name: str = Field(min_length=1, max_length=128)
    description: str = Field(min_length=1, max_length=2_000)
    capabilities: ChannelProviderCapabilities = Field(default_factory=ChannelProviderCapabilities)


class ChannelConnectionConfiguration(ChannelVersionedModel):
    """Non-secret Runtime configuration for one external channel account.

    ``principal_scope`` is the Runtime-owned privacy, relationship, and memory
    isolation key. It is not a provider user id and must never be derived from
    message text. Provider credentials and login state are intentionally absent.
    """

    connection_id: UUID
    provider_id: str = Field(pattern=r"^[a-z0-9][a-z0-9._-]{1,127}$")
    name: str = Field(min_length=1, max_length=128)
    character_id: str = Field(min_length=1, max_length=256)
    principal_scope: str = Field(
        min_length=1,
        max_length=256,
        description=(
            "Runtime-owned stable isolation key for persona relationship and memory state; "
            "it is not a provider identity and must match the configured connection"
        ),
    )
    account_key: str | None = Field(
        default=None,
        min_length=1,
        max_length=512,
        description="Opaque provider-scoped account identity; never a credential",
    )
    allowed_sender_keys: list[Annotated[str, Field(min_length=1, max_length=512)]] = Field(
        default_factory=list,
        max_length=64,
        description=(
            "Opaque provider-scoped sender identities admitted by this connection; "
            "an empty list admits no senders"
        ),
    )
    enabled: bool = True
    timeout_seconds: float = Field(
        default=120,
        gt=0,
        le=600,
        description=(
            "Recent authenticated adapter activity window used by local health presentation; "
            "it is not a generation or provider-delivery deadline"
        ),
    )


class ChannelConnectionSnapshot(ChannelVersionedModel):
    """Persisted connection state safe to expose to local settings clients.

    ``account_key`` and ``allowed_sender_keys`` remain local, non-secret,
    provider-scoped identifiers. They are never credentials and should not be
    forwarded to a conversation peer.
    """

    configuration: ChannelConnectionConfiguration
    revision: int = Field(ge=1)
    status: ChannelConnectionStatus = ChannelConnectionStatus.UNTESTED
    capabilities: ChannelProviderCapabilities = Field(default_factory=ChannelProviderCapabilities)
    last_error: StructuredError | None = None
    last_seen_at: AwareDatetime | None = None
    created_at: AwareDatetime
    updated_at: AwareDatetime


class ChannelAuthorizationStartRequest(ChannelVersionedModel):
    """Start one local, short-lived provider authorization session.

    Provider credentials and account identities must never be accepted from the
    browser. A successful adapter authorization derives them and returns only a
    sanitized ``ChannelConnectionSnapshot``.
    """

    provider_id: str = Field(pattern=r"^[a-z0-9][a-z0-9._-]{1,127}$")
    method: ChannelAuthorizationMethod = ChannelAuthorizationMethod.QR_CODE
    character_id: str = Field(min_length=1, max_length=256)
    connection_name: str | None = Field(default=None, min_length=1, max_length=128)
    principal_scope: str = Field(
        default="local",
        min_length=1,
        max_length=256,
        description="Runtime-owned relationship and memory scope, never a provider identity",
    )


class ChannelAuthorizationVerificationRequest(ChannelVersionedModel):
    """Submit a short pairing code requested by the provider authorization flow."""

    verification_code: str = Field(
        min_length=1,
        max_length=32,
        pattern=r"^[0-9A-Za-z-]+$",
    )


class ChannelAuthorizationSnapshot(ChannelVersionedModel):
    """Sanitized state of a short-lived provider authorization session."""

    auth_session_id: UUID
    provider_id: str = Field(pattern=r"^[a-z0-9][a-z0-9._-]{1,127}$")
    method: ChannelAuthorizationMethod = ChannelAuthorizationMethod.QR_CODE
    status: ChannelAuthorizationStatus
    qr_code_content: str | None = Field(
        default=None,
        min_length=1,
        max_length=8_192,
        description="Opaque content rendered as a QR code by the trusted local settings client",
    )
    verification_required: bool = False
    connection: ChannelConnectionSnapshot | None = None
    error: StructuredError | None = None
    status_message: str | None = Field(default=None, min_length=1, max_length=1_000)
    poll_after_ms: int | None = Field(default=None, ge=0, le=60_000)
    expires_at: AwareDatetime
    created_at: AwareDatetime
    updated_at: AwareDatetime

    @model_validator(mode="after")
    def validate_authorization_state(self) -> ChannelAuthorizationSnapshot:
        active_statuses = {
            ChannelAuthorizationStatus.PENDING,
            ChannelAuthorizationStatus.SCANNED,
            ChannelAuthorizationStatus.VERIFICATION_REQUIRED,
        }
        if self.status in active_statuses and self.qr_code_content is None:
            raise ValueError("active QR authorization snapshots require qr_code_content")
        if self.verification_required != (
            self.status == ChannelAuthorizationStatus.VERIFICATION_REQUIRED
        ):
            raise ValueError("verification_required must match the verification_required status")
        if self.status == ChannelAuthorizationStatus.CONFIRMED and self.connection is None:
            raise ValueError("confirmed authorization snapshots require a connection")
        if self.status == ChannelAuthorizationStatus.FAILED and self.error is None:
            raise ValueError("failed authorization snapshots require an error")
        if self.status != ChannelAuthorizationStatus.FAILED and self.error is not None:
            raise ValueError("only failed authorization snapshots may include an error")
        return self


class ChannelGatewayStatusSnapshot(ChannelVersionedModel):
    """Aggregate health for the provider-neutral channel gateway."""

    status: ChannelGatewayStatus
    provider_count: int = Field(ge=0)
    enabled_connection_count: int = Field(ge=0)
    checked_at: AwareDatetime


class ChannelInboundTextMessage(ChannelVersionedModel):
    """Normalized text message admitted by a trusted adapter.

    Identity fields are opaque and provider-scoped:

    * ``external_message_id`` identifies one inbound message and is the
      idempotency key within a connection. It must remain stable across retries.
    * ``conversation_key`` identifies the provider conversation, not a Runtime
      session id.
    * ``sender_key`` identifies the provider peer, not a display name.
    * ``principal_scope`` is assigned by Runtime connection policy and must
      equal the configured connection scope; adapters must not accept it from
      untrusted user content.

    ``conversation_label`` and ``sender_display_name`` are untrusted display
    hints only. They must never be used for identity, authorization, memory
    scope, prompt instructions, or idempotency.
    """

    connection_id: UUID
    account_key: str | None = Field(
        default=None,
        min_length=1,
        max_length=512,
        description=(
            "Opaque provider-scoped account identity copied from the connection; "
            "the gateway must reject a mismatch"
        ),
    )
    external_message_id: str = Field(
        min_length=1,
        max_length=512,
        description=(
            "Opaque provider-scoped inbound message identity used for idempotency; "
            "stable across retries within the connection"
        ),
    )
    conversation_key: str = Field(
        min_length=1,
        max_length=512,
        description="Opaque provider-scoped direct-conversation identity",
    )
    sender_key: str = Field(
        min_length=1,
        max_length=512,
        description="Opaque provider-scoped sender identity; never a display name",
    )
    principal_scope: str = Field(
        min_length=1,
        max_length=256,
        description=(
            "Runtime-owned privacy and memory isolation key copied from the connection; "
            "the gateway must reject a mismatch"
        ),
    )
    chat_type: ChannelChatType = ChannelChatType.DIRECT
    kind: Literal[ChannelMessageKind.TEXT] = ChannelMessageKind.TEXT
    text: str = Field(min_length=1, max_length=20_000)
    conversation_label: str | None = Field(
        default=None,
        min_length=1,
        max_length=256,
        description="Untrusted display label; never identity, authorization, or instruction",
    )
    sender_display_name: str | None = Field(
        default=None,
        min_length=1,
        max_length=256,
        description="Untrusted display label; never identity, authorization, or instruction",
    )
    received_at: AwareDatetime
    reply_to_external_message_id: str | None = Field(default=None, min_length=1, max_length=512)


class ChannelTurnReceipt(ChannelVersionedModel):
    """Admission receipt returned before or while a channel turn is processed."""

    channel_turn_id: UUID
    connection_id: UUID
    account_key: str | None = Field(default=None, min_length=1, max_length=512)
    external_message_id: str = Field(min_length=1, max_length=512)
    conversation_key: str = Field(min_length=1, max_length=512)
    sender_key: str = Field(min_length=1, max_length=512)
    principal_scope: str = Field(min_length=1, max_length=256)
    chat_type: ChannelChatType = ChannelChatType.DIRECT
    conversation_label: str | None = Field(
        default=None, min_length=1, max_length=256, description="Untrusted display label only"
    )
    sender_display_name: str | None = Field(
        default=None, min_length=1, max_length=256, description="Untrusted display label only"
    )
    session_id: UUID
    turn_id: UUID
    generation_id: UUID
    status: ChannelTurnStatus
    duplicate: bool = False
    revision: int = Field(ge=0)
    accepted_at: AwareDatetime
    poll_after_ms: int | None = Field(default=None, ge=0, le=60_000)


class ChannelTurnSnapshot(ChannelVersionedModel):
    """Current durable result for one normalized inbound message."""

    channel_turn_id: UUID
    connection_id: UUID
    account_key: str | None = Field(default=None, min_length=1, max_length=512)
    external_message_id: str = Field(min_length=1, max_length=512)
    conversation_key: str = Field(min_length=1, max_length=512)
    sender_key: str = Field(min_length=1, max_length=512)
    principal_scope: str = Field(min_length=1, max_length=256)
    chat_type: ChannelChatType = ChannelChatType.DIRECT
    conversation_label: str | None = Field(
        default=None, min_length=1, max_length=256, description="Untrusted display label only"
    )
    sender_display_name: str | None = Field(
        default=None, min_length=1, max_length=256, description="Untrusted display label only"
    )
    session_id: UUID
    turn_id: UUID
    generation_id: UUID
    status: ChannelTurnStatus
    reply_text: str | None = Field(default=None, max_length=100_000)
    delivery_id: UUID | None = None
    delivery_status: ChannelDeliveryStatus | None = None
    error: StructuredError | None = None
    revision: int = Field(ge=0)
    created_at: AwareDatetime
    updated_at: AwareDatetime
    completed_at: AwareDatetime | None = None


class ChannelDeliveryClaimRequest(ChannelVersionedModel):
    """Acquire a short server-timed lease before invoking a provider send API."""

    delivery_id: UUID
    channel_turn_id: UUID
    lease_id: UUID
    lease_seconds: int = Field(default=60, ge=5, le=300)


class ChannelDeliveryAcknowledgement(ChannelVersionedModel):
    """Idempotent adapter acknowledgement for one outbound delivery."""

    delivery_id: UUID
    channel_turn_id: UUID
    lease_id: UUID
    status: Literal[
        ChannelDeliveryStatus.DELIVERED,
        ChannelDeliveryStatus.FAILED,
        ChannelDeliveryStatus.CANCELLED,
    ]
    provider_message_id: str | None = Field(default=None, min_length=1, max_length=512)
    error: StructuredError | None = None
    acknowledged_at: AwareDatetime

    @model_validator(mode="after")
    def validate_delivery_outcome(self) -> ChannelDeliveryAcknowledgement:
        if self.status == ChannelDeliveryStatus.DELIVERED and self.error is not None:
            raise ValueError("delivered acknowledgements cannot include an error")
        if self.status == ChannelDeliveryStatus.FAILED and self.error is None:
            raise ValueError("failed acknowledgements require an error")
        return self


class ChannelDeliverySnapshot(ChannelVersionedModel):
    delivery_id: UUID
    channel_turn_id: UUID
    connection_id: UUID
    status: ChannelDeliveryStatus
    attempt: int = Field(default=1, ge=1)
    lease_id: UUID | None = None
    lease_expires_at: AwareDatetime | None = None
    provider_message_id: str | None = Field(default=None, min_length=1, max_length=512)
    last_error: StructuredError | None = None
    plan_version: int = Field(default=1, ge=1)
    part_count: int = Field(default=1, ge=1)
    delivered_part_count: int = Field(default=0, ge=0)
    cancel_requested_at: AwareDatetime | None = None
    created_at: AwareDatetime
    updated_at: AwareDatetime
    delivered_at: AwareDatetime | None = None

    @model_validator(mode="after")
    def require_sending_lease(self) -> ChannelDeliverySnapshot:
        if self.status == ChannelDeliveryStatus.SENDING and (
            self.lease_id is None or self.lease_expires_at is None
        ):
            raise ValueError("sending delivery snapshots require an active lease")
        return self


class ChannelDeliveryPartKind(StrEnum):
    TEXT = "text"
    IMAGE = "image"


class ChannelDeliveryPartStatus(StrEnum):
    PENDING = "pending"
    SENDING = "sending"
    DELIVERED = "delivered"
    FAILED = "failed"
    CANCELLED = "cancelled"
    SKIPPED = "skipped"


class ChannelTextDeliveryPartPayload(ChannelVersionedModel):
    kind: Literal[ChannelDeliveryPartKind.TEXT] = ChannelDeliveryPartKind.TEXT
    text: str = Field(min_length=1, max_length=20_000)


ChannelDeliveryPartPayload = Annotated[
    ChannelTextDeliveryPartPayload,
    Field(discriminator="kind"),
]


class ChannelDeliveryPartSnapshot(ChannelVersionedModel):
    part_id: UUID
    delivery_id: UUID
    ordinal: int = Field(ge=0)
    kind: ChannelDeliveryPartKind = ChannelDeliveryPartKind.TEXT
    payload: ChannelDeliveryPartPayload
    required: bool = True
    status: ChannelDeliveryPartStatus
    delay_after_ms: int = Field(default=0, ge=0, le=60_000)
    not_before_at: AwareDatetime | None = None
    attempt: int = Field(default=0, ge=0)
    lease_id: UUID | None = None
    lease_expires_at: AwareDatetime | None = None
    provider_client_id: str = Field(min_length=1, max_length=512)
    provider_message_id: str | None = Field(default=None, min_length=1, max_length=512)
    last_error: StructuredError | None = None
    created_at: AwareDatetime
    updated_at: AwareDatetime
    delivered_at: AwareDatetime | None = None

    @model_validator(mode="after")
    def require_sending_lease(self) -> ChannelDeliveryPartSnapshot:
        if self.status == ChannelDeliveryPartStatus.SENDING and (
            self.lease_id is None or self.lease_expires_at is None
        ):
            raise ValueError("sending delivery part snapshots require an active lease")
        return self


class ChannelDeliveryPartDraft(ChannelVersionedModel):
    ordinal: int = Field(ge=0)
    kind: ChannelDeliveryPartKind = ChannelDeliveryPartKind.TEXT
    payload: ChannelDeliveryPartPayload
    required: bool = True
    delay_after_ms: int = Field(default=0, ge=0, le=60_000)
    not_before_at: AwareDatetime | None = None


class ChannelDeliveryPlanSnapshot(ChannelVersionedModel):
    delivery_id: UUID
    channel_turn_id: UUID
    connection_id: UUID
    status: ChannelDeliveryStatus
    plan_version: int = Field(default=1, ge=1)
    part_count: int = Field(ge=1)
    delivered_part_count: int = Field(default=0, ge=0)
    next_pending_ordinal: int | None = Field(default=None, ge=0)
    cancel_requested_at: AwareDatetime | None = None
    parts: list[ChannelDeliveryPartSnapshot] = Field(default_factory=list[ChannelDeliveryPartSnapshot])
    created_at: AwareDatetime
    updated_at: AwareDatetime
    delivered_at: AwareDatetime | None = None


class ChannelDeliveryPartClaimRequest(ChannelVersionedModel):
    delivery_id: UUID
    part_id: UUID | None = None
    lease_id: UUID
    lease_seconds: int = Field(default=60, ge=5, le=300)


class ChannelDeliveryPartAcknowledgement(ChannelVersionedModel):
    delivery_id: UUID
    part_id: UUID
    lease_id: UUID
    status: Literal[
        ChannelDeliveryPartStatus.DELIVERED,
        ChannelDeliveryPartStatus.FAILED,
        ChannelDeliveryPartStatus.CANCELLED,
    ]
    provider_message_id: str | None = Field(default=None, min_length=1, max_length=512)
    error: StructuredError | None = None
    acknowledged_at: AwareDatetime

    @model_validator(mode="after")
    def validate_delivery_outcome(self) -> ChannelDeliveryPartAcknowledgement:
        if self.status == ChannelDeliveryPartStatus.DELIVERED and self.error is not None:
            raise ValueError("delivered acknowledgements cannot include an error")
        if self.status == ChannelDeliveryPartStatus.FAILED and self.error is None:
            raise ValueError("failed acknowledgements require an error")
        return self


class ChannelDeliveryPartsCancelRequest(ChannelVersionedModel):
    reason: str = Field(min_length=1, max_length=1_000)
    requested_at: AwareDatetime


class ChannelTurnCancelRequest(ChannelVersionedModel):
    reason: str = Field(min_length=1, max_length=1_000)
    requested_at: AwareDatetime


class ChannelTurnCancelReceipt(ChannelVersionedModel):
    channel_turn_id: UUID
    accepted: bool
    status: ChannelTurnStatus
    revision: int = Field(ge=0)
    acknowledged_at: AwareDatetime


class ChannelErrorResponse(ChannelVersionedModel):
    """Normalized gateway failure safe for HTTP and plugin boundaries."""

    error: StructuredError
    channel_turn_id: UUID | None = None
    external_message_id: str | None = Field(default=None, min_length=1, max_length=512)
    retry_after_ms: int | None = Field(default=None, ge=0, le=600_000)
