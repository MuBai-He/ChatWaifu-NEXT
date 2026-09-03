"""Context Synchronization, Patch Construction, and Cloud Egress Policy.

Implements Phase 13.3:
1. RealtimeContextPatchBuilder: Builds budgeted, privacy-filtered context patches from
   Runtime authority snapshots (persona, relationship, affect, active skills, memories).
2. CloudEgressPolicy: Enforces allow/ask/deny policies, requiring explicit consent grants
   for 'ask' mode, ensuring 0 backend calls on rejection.
3. EgressReceipt: Structured audit receipts persisted without memory plaintext or API keys.
4. Error normalization for provider context updates.
"""

from __future__ import annotations

import hashlib
import json
import logging
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Literal, cast
from uuid import UUID, uuid4

from chatwaifu_protocol.base import PrivacyLevel, ProtocolModel
from chatwaifu_protocol.character import CharacterKernelSnapshot
from chatwaifu_protocol.errors import StructuredError
from chatwaifu_protocol.events import EventEnvelope, EventModel
from chatwaifu_protocol.memory import MemoryRecord
from pydantic import AwareDatetime, ConfigDict, Field

from chatwaifu_runtime.characters.service import CharacterProfile
from chatwaifu_runtime.persistence.event_store import EventStore
from chatwaifu_runtime.realtime.cloud.contracts import (
    CloudRealtimeBackend,
    CloudRealtimeSession,
    RealtimeContextComponent,
    RealtimeContextPatch,
    RealtimeSessionOpenRequest,
)
from chatwaifu_runtime.realtime.cloud.coordinator import CloudRealtimeCoordinator

_LOGGER = logging.getLogger(__name__)

# Keys stripped from skill definitions to guarantee zero secret leakage
_SECRET_KEY_SUBSTRINGS = (
    "key",
    "secret",
    "token",
    "password",
    "credential",
    "auth",
    "cert",
    "private",
)

_EXCLUDED_MEMORY_SENSITIVITIES = (
    PrivacyLevel.SENSITIVE,
    PrivacyLevel.LOCAL,
    "sensitive",
    "local",
    "restricted",
    "high",
    "secret",
)


class PolicyDeniedError(Exception):
    """Raised when cloud egress is completely forbidden by policy ('deny')."""

    def __init__(self, message: str = "Cloud egress denied by policy") -> None:
        super().__init__(message)
        self.message = message


class ConsentRequiredError(Exception):
    """Raised when cloud egress requires explicit user consent ('ask') but none was provided."""

    def __init__(self, message: str = "Explicit user consent required for cloud egress") -> None:
        super().__init__(message)
        self.message = message


@dataclass(frozen=True, slots=True)
class EgressGrant:
    """An explicit, scoped consent grant for cloud egress."""

    session_id: UUID
    approved_by: str = "user"
    scope: str = "session"
    granted_at: datetime = field(default_factory=lambda: datetime.now(UTC))


class EgressReceiptPayload(ProtocolModel):
    """Audit payload for cloud context egress, strictly omitting secrets and raw memory."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    provider_backend_id: str
    patch_id: UUID
    component_kinds: list[str]
    memory_record_ids: list[UUID] = Field(default_factory=lambda: list[UUID]())
    byte_count: int
    estimated_tokens: int
    policy_decision: str
    approved_by: str | None = None
    scope: str | None = None
    occurred_at: AwareDatetime = Field(default_factory=lambda: datetime.now(UTC))


class EgressReceiptEvent(EventEnvelope[Literal["cloud.egress_receipt"], EgressReceiptPayload]):
    """Domain event persisted in EventStore for egress auditing."""

    event_type: Literal["cloud.egress_receipt"] = "cloud.egress_receipt"


class EgressBlockedPayload(ProtocolModel):
    """Audit payload for blocked cloud egress attempts."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    provider_backend_id: str
    policy_decision: str
    reason: str
    occurred_at: AwareDatetime = Field(default_factory=lambda: datetime.now(UTC))


class EgressBlockedEvent(EventEnvelope[Literal["cloud.egress_blocked"], EgressBlockedPayload]):
    """Domain event persisted in EventStore for blocked egress auditing."""

    event_type: Literal["cloud.egress_blocked"] = "cloud.egress_blocked"


class RealtimeContextPatchBuilder:
    """Constructs deterministic, budgeted context patches from runtime authority."""

    def __init__(
        self,
        *,
        max_tokens: int = 4000,
        max_bytes: int = 16384,
    ) -> None:
        self.max_tokens = max_tokens
        self.max_bytes = max_bytes

    def build_patch(
        self,
        *,
        safety_contract: str | None = None,
        character_profile: CharacterProfile | None = None,
        kernel_snapshot: CharacterKernelSnapshot | None = None,
        memories: Sequence[MemoryRecord] | None = None,
        skills: Sequence[dict[str, Any]] | None = None,
    ) -> RealtimeContextPatch:
        """Constructs an immutable RealtimeContextPatch, pruning components if budget exceeded.

        Retention Priority (lower number = higher retention priority):
        0: Safety / output contract (never pruned)
        1: Persona summary
        2: Relationship summary
        3: Affect summary
        4: Active skill capabilities (stripped of secrets)
        5: Selected memory excerpts (filtered for sensitivity and tombstoning)
        """
        candidates: list[RealtimeContextComponent] = []

        # 0. Safety / output contract
        safety_text = safety_contract or (
            "Safety Contract: Galgame visual novel conversational companion. "
            "Remain strictly in character. Do not break role or output meta instructions."
        )
        candidates.append(self._build_component("safety", safety_text, priority=0))

        # 1. Persona summary
        if character_profile is not None:
            persona_lines = [
                f"Name: {character_profile.display_name}",
                f"Tagline: {character_profile.tagline}",
                f"Persona: {character_profile.system_prompt}",
            ]
            if character_profile.lexicon:
                lexicon_items = [f"{k}: {v}" for k, v in sorted(character_profile.lexicon.items())]
                persona_lines.append(f"Lexicon: {', '.join(lexicon_items)}")
            candidates.append(
                self._build_component("persona", "\n".join(persona_lines), priority=1)
            )

        # 2. Relationship summary
        if kernel_snapshot is not None:
            rel = kernel_snapshot.relationship
            rel_lines = [
                f"Relationship Stage: {rel.stage}",
                f"Affinity: {rel.affinity:.2f}",
                f"Trust: {rel.trust:.2f}",
                f"Familiarity: {rel.familiarity:.2f}",
                f"Comfort: {rel.comfort:.2f}",
            ]
            if rel.preferred_address:
                rel_lines.append(f"Preferred Address: {rel.preferred_address}")
            candidates.append(
                self._build_component("relationship", "\n".join(rel_lines), priority=2)
            )

        # 3. Affect summary
        if kernel_snapshot is not None:
            aff = kernel_snapshot.affect
            aff_lines = [
                f"Valence: {aff.valence:.2f}",
                f"Arousal: {aff.arousal:.2f}",
                f"Energy: {aff.energy:.2f}",
                f"Attention: {aff.attention:.2f}",
                f"Embarrassment: {aff.embarrassment:.2f}",
                f"Tension: {aff.tension:.2f}",
            ]
            candidates.append(self._build_component("affect", "\n".join(aff_lines), priority=3))

        # 4. Active skills summary (stripped of credentials/tokens)
        if skills:
            sanitized_skills: list[str] = []
            for s in sorted(skills, key=lambda x: str(x.get("name", ""))):
                name = str(s.get("name", "unnamed"))
                desc = str(s.get("description", ""))
                # Sanitize parameters/metadata to ensure zero secrets
                safe_params = {
                    k: v
                    for k, v in s.items()
                    if not any(sub in k.lower() for sub in _SECRET_KEY_SUBSTRINGS)
                    and k not in ("name", "description")
                }
                sanitized_skills.append(
                    f"- Skill '{name}': {desc} ({json.dumps(safe_params, sort_keys=True)})"
                )
            if sanitized_skills:
                candidates.append(
                    self._build_component(
                        "skills",
                        "Available Capabilities:\n" + "\n".join(sanitized_skills),
                        priority=4,
                    )
                )

        # 5. Selected memory excerpts (privacy & state filtered)
        if memories:
            valid_memories: list[MemoryRecord] = []
            for m in sorted(memories, key=lambda x: str(x.memory_id)):
                # Exclude tombstoned or inactive memories
                if m.state != "active":
                    continue
                # Exclude restricted, sensitive, or local-only memories from cloud egress
                if m.sensitivity in _EXCLUDED_MEMORY_SENSITIVITIES:
                    continue
                valid_memories.append(m)

            if valid_memories:
                memory_lines = [f"- {m.text}" for m in valid_memories]
                candidates.append(
                    self._build_component(
                        "memory",
                        "Relevant Context:\n" + "\n".join(memory_lines),
                        priority=5,
                        metadata={"record_count": len(valid_memories)},
                    )
                )

        # Budget enforcement: prune whole components in descending priority order (5 down to 1)
        active_components = list(candidates)
        while active_components:
            total_bytes = sum(c.byte_count for c in active_components)
            total_tokens = sum(c.estimated_tokens for c in active_components)

            if total_bytes <= self.max_bytes and total_tokens <= self.max_tokens:
                break

            # Find the highest priority integer (> 0) to drop
            droppable = [c for c in active_components if c.priority > 0]
            if not droppable:
                # Only priority 0 (safety) remains; stop pruning
                break

            highest_prio_comp = max(droppable, key=lambda c: c.priority)
            active_components.remove(highest_prio_comp)
            _LOGGER.debug(
                "Pruned context component '%s' (priority %d) to meet budget",
                highest_prio_comp.kind,
                highest_prio_comp.priority,
            )

        # Sort components by priority ascending (0, 1, 2, 3, 4, 5)
        active_components.sort(key=lambda c: c.priority)

        # Compute deterministic content hash across sorted components
        hash_payload = "\n---\n".join(
            f"[{c.kind}|{c.priority}]\n{c.text}" for c in active_components
        )
        content_hash = hashlib.sha256(hash_payload.encode("utf-8")).hexdigest()

        final_bytes = sum(c.byte_count for c in active_components)
        final_tokens = sum(c.estimated_tokens for c in active_components)

        return RealtimeContextPatch(
            patch_id=uuid4(),
            components=tuple(active_components),
            content_hash=content_hash,
            total_bytes=final_bytes,
            estimated_tokens=final_tokens,
            created_at=datetime.now(UTC),
        )

    def _build_component(
        self,
        kind: str,
        text: str,
        priority: int,
        metadata: dict[str, str | int | float | bool] | None = None,
    ) -> RealtimeContextComponent:
        text_bytes = len(text.encode("utf-8"))
        estimated_tokens = max(1, text_bytes // 3)
        return RealtimeContextComponent(
            kind=kind,
            text=text,
            byte_count=text_bytes,
            estimated_tokens=estimated_tokens,
            priority=priority,
            metadata=metadata or {},
        )


class CloudEgressPolicy:
    """Enforces privacy boundary and records audit receipts for cloud realtime connections."""

    def __init__(
        self,
        policy_mode: Literal["allow", "ask", "deny"] = "ask",
        *,
        event_store: EventStore | None = None,
        patch_builder: RealtimeContextPatchBuilder | None = None,
    ) -> None:
        self.policy_mode: Literal["allow", "ask", "deny"] = policy_mode
        self._event_store = event_store
        self._patch_builder = patch_builder or RealtimeContextPatchBuilder()
        self._grants: dict[UUID, EgressGrant] = {}
        self.audit_receipts: list[EgressReceiptPayload] = []

    def grant(self, grant: EgressGrant) -> None:
        """Register an explicit consent grant for a session."""
        self._grants[grant.session_id] = grant

    def revoke(self, session_id: UUID) -> None:
        """Revoke a consent grant for a session."""
        self._grants.pop(session_id, None)

    def has_grant(self, session_id: UUID) -> bool:
        """Check if an active consent grant exists for the session."""
        return session_id in self._grants

    async def evaluate_and_open_session(
        self,
        backend: CloudRealtimeBackend,
        request: RealtimeSessionOpenRequest,
        *,
        character_profile: CharacterProfile | None = None,
        kernel_snapshot: CharacterKernelSnapshot | None = None,
        memories: Sequence[MemoryRecord] | None = None,
        skills: Sequence[dict[str, Any]] | None = None,
        safety_contract: str | None = None,
    ) -> CloudRealtimeSession:
        """Evaluates policy and opens a cloud session if allowed.

        Guarantees:
        - If 'deny': 0 calls to backend.open_session, raises PolicyDeniedError.
        - If 'ask' and not granted: 0 calls to backend.open_session, raises ConsentRequiredError.
        - If allowed or granted: compiles ContextPatch, emits audit receipt, opens session.
        """
        backend_id = backend.backend_id
        session_id = request.session_id

        # 1. Deny mode
        if self.policy_mode == "deny":
            receipt = EgressReceiptPayload(
                provider_backend_id=backend_id,
                patch_id=uuid4(),
                component_kinds=[],
                byte_count=0,
                estimated_tokens=0,
                policy_decision="deny",
            )
            self.audit_receipts.append(receipt)
            if self._event_store is not None:
                blocked_payload = EgressBlockedPayload(
                    provider_backend_id=backend_id,
                    policy_decision="deny",
                    reason="Cloud egress denied by policy ('deny')",
                )
                blocked_event = EgressBlockedEvent(
                    event_id=uuid4(),
                    session_id=session_id,
                    occurred_at=datetime.now(UTC),
                    source="cloud_egress_policy",
                    payload=blocked_payload,
                )
                try:
                    await self._event_store.append(cast(EventModel, blocked_event))
                except Exception as exc:
                    _LOGGER.warning("Failed to persist egress blocked event: %s", exc)

            raise PolicyDeniedError("Cloud egress denied by policy ('deny')")

        # 2. Ask mode
        approved_by: str | None = None
        scope: str | None = None
        if self.policy_mode == "ask":
            grant = self._grants.get(session_id)
            if grant is None:
                receipt = EgressReceiptPayload(
                    provider_backend_id=backend_id,
                    patch_id=uuid4(),
                    component_kinds=[],
                    byte_count=0,
                    estimated_tokens=0,
                    policy_decision="consent_required",
                )
                self.audit_receipts.append(receipt)
                if self._event_store is not None:
                    blocked_payload = EgressBlockedPayload(
                        provider_backend_id=backend_id,
                        policy_decision="consent_required",
                        reason="Explicit user consent required for cloud egress ('ask')",
                    )
                    blocked_event = EgressBlockedEvent(
                        event_id=uuid4(),
                        session_id=session_id,
                        occurred_at=datetime.now(UTC),
                        source="cloud_egress_policy",
                        payload=blocked_payload,
                    )
                    try:
                        await self._event_store.append(cast(EventModel, blocked_event))
                    except Exception as exc:
                        _LOGGER.warning("Failed to persist egress blocked event: %s", exc)

                raise ConsentRequiredError(
                    "Explicit user consent required for cloud egress ('ask')"
                )
            decision = "ask_approved"
            approved_by = grant.approved_by
            scope = grant.scope
        else:
            decision = "allow"

        # 3. Authorized ('allow' or 'ask_approved'): Build Context Patch
        patch = request.initial_context or self._patch_builder.build_patch(
            safety_contract=safety_contract,
            character_profile=character_profile,
            kernel_snapshot=kernel_snapshot,
            memories=memories,
            skills=skills,
        )

        # Extract memory IDs (IDs only, never raw plaintext)
        memory_ids: list[UUID] = []
        if memories:
            memory_ids = [
                m.memory_id
                for m in memories
                if m.state == "active" and m.sensitivity not in _EXCLUDED_MEMORY_SENSITIVITIES
            ]

        receipt = EgressReceiptPayload(
            provider_backend_id=backend_id,
            patch_id=patch.patch_id,
            component_kinds=[c.kind for c in patch.components],
            memory_record_ids=memory_ids,
            byte_count=patch.total_bytes,
            estimated_tokens=patch.estimated_tokens,
            policy_decision=decision,
            approved_by=approved_by,
            scope=scope,
        )
        self.audit_receipts.append(receipt)

        if self._event_store is not None:
            receipt_event = EgressReceiptEvent(
                event_id=uuid4(),
                session_id=session_id,
                occurred_at=datetime.now(UTC),
                source="cloud_egress_policy",
                payload=receipt,
            )
            try:
                await self._event_store.append(cast(EventModel, receipt_event))
            except Exception as exc:
                _LOGGER.warning("Failed to persist egress receipt event: %s", exc)

        # 4. Open provider session with compiled patch
        request_with_patch = RealtimeSessionOpenRequest(
            session_id=request.session_id,
            character_id=request.character_id,
            turn_id=request.turn_id,
            generation_id=request.generation_id,
            initial_context=patch,
            voice_id=request.voice_id,
            model=request.model,
            sample_rate=request.sample_rate,
            channels=request.channels,
        )
        return await backend.open_session(request_with_patch)


async def update_session_context(
    session: CloudRealtimeSession,
    patch: RealtimeContextPatch,
    coordinator: CloudRealtimeCoordinator | None = None,
) -> None:
    """Pushes an updated context patch to an active session, normalizing failures."""
    try:
        await session.update_context(patch)
    except Exception as exc:
        _LOGGER.warning(
            "Provider context update failed on session %s: %s",
            session.lineage.session_id,
            exc,
        )
        error = StructuredError(
            code="provider_context_update_failed",
            message=f"Failed to update cloud realtime context: {exc}",
            component="cloud_realtime",
            retryable=True,
        )
        if coordinator is not None:
            await coordinator.domain_sink.provider_error(session.lineage.session_id, error)
        raise
