"""Context Synchronization, Patch Construction, and Cloud Egress Policy.

Implements Phase 13.3:
1. RealtimeContextPatchBuilder: Builds budgeted, privacy-filtered context patches from
   Runtime authority snapshots (persona, relationship, affect, active skills, memories).
2. CloudEgressGateway: Enforces allow/ask/deny policies with scoped EgressGrants,
   guaranteeing fail-closed durable auditing to EventStore before any network calls.
3. EgressReceipt: Structured audit receipts persisted without memory plaintext or API keys.
4. Error normalization for provider context updates.
"""

from __future__ import annotations

import hashlib
import logging
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Literal, cast
from uuid import UUID, uuid4

from chatwaifu_protocol.base import PrivacyLevel
from chatwaifu_protocol.character import CharacterKernelSnapshot
from chatwaifu_protocol.errors import StructuredError
from chatwaifu_protocol.events import (
    EgressBlockedEvent,
    EgressBlockedPayload,
    EgressReceiptEvent,
    EgressReceiptPayload,
    EventModel,
)
from chatwaifu_protocol.memory import MemoryRecord

from chatwaifu_runtime.characters.service import CharacterProfile
from chatwaifu_runtime.eventing.hub import EventHub
from chatwaifu_runtime.persistence.event_store import EventStore
from chatwaifu_runtime.realtime.cloud.contracts import (
    AuthorizedRealtimeSessionOpenRequest,
    CloudRealtimeBackend,
    CloudRealtimeSession,
    RealtimeContextComponent,
    RealtimeContextPatch,
    RealtimeSessionIntent,
    RealtimeSessionOpenRequest,
    RealtimeSkillCapability,
)
from chatwaifu_runtime.realtime.cloud.coordinator import CloudRealtimeCoordinator

_LOGGER = logging.getLogger(__name__)

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


@dataclass(slots=True)
class EgressGrant:
    """An explicit, scoped consent grant for cloud egress."""

    session_id: UUID
    backend_id: str = "fake_cloud_realtime"
    purpose: Literal["cloud_realtime"] = "cloud_realtime"
    allowed_component_kinds: frozenset[str] = frozenset(
        {"safety", "persona", "relationship", "affect", "memory", "skills"}
    )
    expires_at: datetime = field(default_factory=lambda: datetime.now(UTC) + timedelta(hours=1))
    remaining_uses: int = 100
    approved_by: str = "user"
    scope: str = "session"
    grant_id: UUID = field(default_factory=uuid4)


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
        skills: Sequence[RealtimeSkillCapability] | None = None,
    ) -> RealtimeContextPatch:
        """Constructs an immutable RealtimeContextPatch, pruning components if budget exceeded.

        Retention Priority (lower number = higher retention priority):
        0: Safety / output contract (never pruned)
        1: Persona summary
        2: Relationship summary
        3: Affect summary
        4: Active skill capabilities (strictly typed allowlist DTO)
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

        # 4. Active skills summary (strictly typed allowlist, no raw dicts or secrets)
        if skills:
            for s in skills:
                if not isinstance(s, RealtimeSkillCapability):  # pyright: ignore[reportUnnecessaryIsInstance]
                    raise TypeError(
                        f"Expected RealtimeSkillCapability instance, got {type(s).__name__}"
                    )
            sanitized_skills: list[str] = []
            for s in sorted(skills, key=lambda x: str(x.skill_id)):
                arg_list = ", ".join(s.allowed_argument_names)
                sanitized_skills.append(
                    f"- Skill '{s.skill_id}' ({s.display_name}): {s.description} "
                    f"(allowed_arguments: [{arg_list}])"
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
                if m.state != "active":
                    continue
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
                        source_record_ids=tuple(m.memory_id for m in valid_memories),
                        metadata={"record_count": len(valid_memories)},
                    )
                )

        # Prune according to budget
        retained = self._prune_to_budget(candidates)

        total_bytes = sum(c.byte_count for c in retained)
        estimated_tokens = sum(c.estimated_tokens for c in retained)
        patch_id = uuid4()
        content_hash = self._compute_content_hash(retained)

        return RealtimeContextPatch(
            patch_id=patch_id,
            components=tuple(retained),
            content_hash=content_hash,
            total_bytes=total_bytes,
            estimated_tokens=estimated_tokens,
            created_at=datetime.now(UTC),
        )

    def _build_component(
        self,
        kind: str,
        text: str,
        *,
        priority: int,
        source_record_ids: tuple[UUID, ...] = (),
        metadata: dict[str, str | int | float | bool] | None = None,
    ) -> RealtimeContextComponent:
        raw_bytes = text.encode("utf-8")
        byte_count = len(raw_bytes)
        estimated_tokens = max(1, byte_count // 3)
        return RealtimeContextComponent(
            kind=kind,
            text=text,
            byte_count=byte_count,
            estimated_tokens=estimated_tokens,
            priority=priority,
            source_record_ids=source_record_ids,
            metadata=metadata or {},
        )

    def _prune_to_budget(
        self, candidates: list[RealtimeContextComponent]
    ) -> list[RealtimeContextComponent]:
        retained = list(candidates)

        def exceeds_budget(comps: list[RealtimeContextComponent]) -> bool:
            t_bytes = sum(c.byte_count for c in comps)
            t_tokens = sum(c.estimated_tokens for c in comps)
            return t_bytes > self.max_bytes or t_tokens > self.max_tokens

        if not exceeds_budget(retained):
            return retained

        # Drop lowest priority components first, retaining priority 0 (safety)
        retained.sort(key=lambda c: c.priority)
        while exceeds_budget(retained) and len(retained) > 1:
            retained.pop()

        return retained

    def _compute_content_hash(self, components: list[RealtimeContextComponent]) -> str:
        hasher = hashlib.sha256()
        for c in components:
            hasher.update(c.kind.encode("utf-8"))
            hasher.update(c.text.encode("utf-8"))
        return hasher.hexdigest()


class CloudEgressGateway:
    """Authoritative gate for cloud realtime context egress and provider session initiation."""

    def __init__(
        self,
        *,
        policy_mode: Literal["allow", "ask", "deny"] = "ask",
        event_store: EventStore | None = None,
        event_hub: EventHub | None = None,
        patch_builder: RealtimeContextPatchBuilder | None = None,
    ) -> None:
        self.policy_mode: Literal["allow", "ask", "deny"] = policy_mode
        self._event_store = event_store
        self._event_hub = event_hub
        self._patch_builder = patch_builder or RealtimeContextPatchBuilder()
        self._grants: dict[UUID, EgressGrant] = {}
        self.audit_receipts: list[EgressReceiptPayload] = []

    def grant_consent(self, grant: EgressGrant) -> None:
        """Record an explicit consent grant for a session."""
        self._grants[grant.session_id] = grant

    def grant(self, grant: EgressGrant) -> None:
        """Alias for grant_consent."""
        self.grant_consent(grant)

    def revoke_consent(self, session_id: UUID) -> None:
        """Revoke any existing consent grant for a session."""
        self._grants.pop(session_id, None)

    async def evaluate_and_open_session(
        self,
        backend: CloudRealtimeBackend,
        intent_or_request: RealtimeSessionIntent | RealtimeSessionOpenRequest,
        *,
        safety_contract: str | None = None,
        character_profile: CharacterProfile | None = None,
        kernel_snapshot: CharacterKernelSnapshot | None = None,
        memories: Sequence[MemoryRecord] | None = None,
        skills: Sequence[RealtimeSkillCapability] | None = None,
    ) -> CloudRealtimeSession:
        """Alias for open_session."""
        return await self.open_session(
            backend,
            intent_or_request,
            safety_contract=safety_contract,
            character_profile=character_profile,
            kernel_snapshot=kernel_snapshot,
            memories=memories,
            skills=skills,
        )

    async def open_session(
        self,
        backend: CloudRealtimeBackend,
        intent_or_request: RealtimeSessionIntent | RealtimeSessionOpenRequest,
        *,
        safety_contract: str | None = None,
        character_profile: CharacterProfile | None = None,
        kernel_snapshot: CharacterKernelSnapshot | None = None,
        memories: Sequence[MemoryRecord] | None = None,
        skills: Sequence[RealtimeSkillCapability] | None = None,
    ) -> CloudRealtimeSession:
        """Enforce policy, build context patch, write durable audit, and open provider session.

        Fail-closed invariant: If audit persistence fails, zero provider calls are made.
        """
        if isinstance(intent_or_request, RealtimeSessionIntent):
            intent = intent_or_request
        else:
            intent = RealtimeSessionIntent(
                session_id=intent_or_request.session_id,
                character_id=intent_or_request.character_id,
                voice_id=intent_or_request.voice_id,
                model=intent_or_request.model,
                sample_rate=intent_or_request.sample_rate,
                channels=intent_or_request.channels,
            )

        session_id = intent.session_id
        backend_id = backend.backend_id

        # 1. Deny mode -> strictly 0 backend calls
        if self.policy_mode == "deny":
            await self._record_blocked(
                session_id=session_id,
                backend_id=backend_id,
                decision="deny",
                reason="Cloud egress denied by policy ('deny')",
            )
            raise PolicyDeniedError("Cloud egress denied by policy ('deny')")

        # 2. Ask mode -> verify scoped grant before proceeding
        approved_by: str | None = None
        scope: str | None = None
        grant: EgressGrant | None = None
        if self.policy_mode == "ask":
            grant = self._grants.get(session_id)
            if (
                grant is None
                or grant.backend_id != backend_id
                or grant.purpose != "cloud_realtime"
                or grant.expires_at <= datetime.now(UTC)
                or grant.remaining_uses <= 0
            ):
                await self._record_blocked(
                    session_id=session_id,
                    backend_id=backend_id,
                    decision="consent_required",
                    reason="Explicit user consent required for cloud egress ('ask')",
                )
                raise ConsentRequiredError(
                    "Explicit user consent required for cloud egress ('ask')"
                )
            decision = "ask_approved"
            approved_by = grant.approved_by
            scope = f"uses_remaining:{grant.remaining_uses}"
        else:
            decision = "allow"

        # 3. Build context patch through builder (no bypass allowed)
        patch = self._patch_builder.build_patch(
            safety_contract=safety_contract,
            character_profile=character_profile,
            kernel_snapshot=kernel_snapshot,
            memories=memories,
            skills=skills,
        )

        # In ask mode, check that all components are within allowed_component_kinds
        if grant is not None:
            component_kinds = {c.kind for c in patch.components}
            if not component_kinds.issubset(grant.allowed_component_kinds):
                await self._record_blocked(
                    session_id=session_id,
                    backend_id=backend_id,
                    decision="consent_required",
                    reason=(
                        f"Patch component kinds {component_kinds} "
                        f"exceed grant allowed {grant.allowed_component_kinds}"
                    ),
                )
                raise ConsentRequiredError(
                    "Context patch components exceed granted component kinds"
                )
            grant.remaining_uses -= 1

        # Extract only memory IDs present in the final retained patch
        retained_memory_ids = [
            mid for c in patch.components if c.kind == "memory" for mid in c.source_record_ids
        ]

        # 4. Durable audit write FIRST (Fail closed)
        receipt = EgressReceiptPayload(
            provider_backend_id=backend_id,
            patch_id=patch.patch_id,
            component_kinds=[c.kind for c in patch.components],
            memory_record_ids=retained_memory_ids,
            byte_count=patch.total_bytes,
            estimated_tokens=patch.estimated_tokens,
            policy_decision=decision,
            approved_by=approved_by,
            scope=scope,
            occurred_at=datetime.now(UTC),
        )
        self.audit_receipts.append(receipt)

        receipt_event = EgressReceiptEvent(
            event_id=uuid4(),
            session_id=session_id,
            occurred_at=datetime.now(UTC),
            source="cloud_egress_policy",
            payload=receipt,
        )

        persisted_receipt = None
        if self._event_store is not None:
            try:
                persisted_receipt = await self._event_store.append(cast(EventModel, receipt_event))
            except Exception as exc:
                _LOGGER.error("Failed to durably persist egress receipt; failing closed: %s", exc)
                raise RuntimeError(f"Egress audit persistence failed: {exc}") from exc

        if self._event_hub is not None:
            try:
                payload = cast(dict[str, object], receipt_event.model_dump(mode="json"))
                await self._event_hub.publish(payload)
            except Exception as exc:
                _LOGGER.warning("Failed to publish egress receipt to EventHub: %s", exc)
            else:
                if self._event_store is not None and persisted_receipt is not None:
                    try:
                        await self._event_store.mark_published(persisted_receipt.event_id)
                    except Exception as exc:
                        _LOGGER.warning("Failed to mark egress receipt published: %s", exc)

        # 5. Open provider session with authorized request
        auth_request = AuthorizedRealtimeSessionOpenRequest(
            intent=intent,
            context_patch=patch,
            authorization_id=receipt_event.event_id,
        )
        return await backend.open_session(auth_request)

    async def update_context(
        self,
        session: CloudRealtimeSession,
        backend_id: str,
        *,
        safety_contract: str | None = None,
        character_profile: CharacterProfile | None = None,
        kernel_snapshot: CharacterKernelSnapshot | None = None,
        memories: Sequence[MemoryRecord] | None = None,
        skills: Sequence[RealtimeSkillCapability] | None = None,
        coordinator: CloudRealtimeCoordinator | None = None,
    ) -> None:
        """Enforce policy, build context patch, write durable audit, and update active session."""
        session_id = session.lineage.session_id

        if self.policy_mode == "deny":
            await self._record_blocked(
                session_id=session_id,
                backend_id=backend_id,
                decision="deny",
                reason="Cloud context update denied by policy ('deny')",
            )
            raise PolicyDeniedError("Cloud context update denied by policy ('deny')")

        approved_by: str | None = None
        scope: str | None = None
        grant: EgressGrant | None = None
        if self.policy_mode == "ask":
            grant = self._grants.get(session_id)
            if (
                grant is None
                or grant.backend_id != backend_id
                or grant.purpose != "cloud_realtime"
                or grant.expires_at <= datetime.now(UTC)
                or grant.remaining_uses <= 0
            ):
                await self._record_blocked(
                    session_id=session_id,
                    backend_id=backend_id,
                    decision="consent_required",
                    reason="Explicit user consent required for cloud context update ('ask')",
                )
                raise ConsentRequiredError(
                    "Explicit user consent required for cloud context update ('ask')"
                )
            decision = "ask_approved"
            approved_by = grant.approved_by
            scope = f"uses_remaining:{grant.remaining_uses}"
        else:
            decision = "allow"

        patch = self._patch_builder.build_patch(
            safety_contract=safety_contract,
            character_profile=character_profile,
            kernel_snapshot=kernel_snapshot,
            memories=memories,
            skills=skills,
        )

        if grant is not None:
            component_kinds = {c.kind for c in patch.components}
            if not component_kinds.issubset(grant.allowed_component_kinds):
                await self._record_blocked(
                    session_id=session_id,
                    backend_id=backend_id,
                    decision="consent_required",
                    reason=(
                        f"Patch component kinds {component_kinds} "
                        f"exceed grant allowed {grant.allowed_component_kinds}"
                    ),
                )
                raise ConsentRequiredError(
                    "Context patch components exceed granted component kinds"
                )
            grant.remaining_uses -= 1

        retained_memory_ids = [
            mid for c in patch.components if c.kind == "memory" for mid in c.source_record_ids
        ]

        receipt = EgressReceiptPayload(
            provider_backend_id=backend_id,
            patch_id=patch.patch_id,
            component_kinds=[c.kind for c in patch.components],
            memory_record_ids=retained_memory_ids,
            byte_count=patch.total_bytes,
            estimated_tokens=patch.estimated_tokens,
            policy_decision=decision,
            approved_by=approved_by,
            scope=scope,
            occurred_at=datetime.now(UTC),
        )
        self.audit_receipts.append(receipt)

        receipt_event = EgressReceiptEvent(
            event_id=uuid4(),
            session_id=session_id,
            occurred_at=datetime.now(UTC),
            source="cloud_egress_policy",
            payload=receipt,
        )

        persisted_receipt = None
        if self._event_store is not None:
            try:
                persisted_receipt = await self._event_store.append(cast(EventModel, receipt_event))
            except Exception as exc:
                _LOGGER.error("Failed to durably persist receipt; failing closed: %s", exc)
                raise RuntimeError(f"Egress audit persistence failed: {exc}") from exc

        if self._event_hub is not None:
            try:
                payload = cast(dict[str, object], receipt_event.model_dump(mode="json"))
                await self._event_hub.publish(payload)
            except Exception as exc:
                _LOGGER.warning("Failed to publish egress receipt to EventHub: %s", exc)
            else:
                if self._event_store is not None and persisted_receipt is not None:
                    try:
                        await self._event_store.mark_published(persisted_receipt.event_id)
                    except Exception as exc:
                        _LOGGER.warning("Failed to mark egress receipt published: %s", exc)

        try:
            await session.update_context(patch)
        except Exception as exc:
            _LOGGER.warning(
                "Provider context update failed on session %s: %s",
                session_id,
                exc,
            )
            error = StructuredError(
                code="provider_context_update_failed",
                message=f"Failed to update cloud realtime context: {exc}",
                component="cloud_realtime",
                retryable=True,
            )
            if coordinator is not None:
                await coordinator.domain_sink.provider_error(session_id, error)
            raise

    async def _record_blocked(
        self,
        *,
        session_id: UUID,
        backend_id: str,
        decision: str,
        reason: str,
    ) -> None:
        receipt = EgressReceiptPayload(
            provider_backend_id=backend_id,
            patch_id=uuid4(),
            component_kinds=[],
            byte_count=0,
            estimated_tokens=0,
            policy_decision=decision,
            occurred_at=datetime.now(UTC),
        )
        self.audit_receipts.append(receipt)
        if self._event_store is not None:
            blocked_payload = EgressBlockedPayload(
                provider_backend_id=backend_id,
                policy_decision=decision,
                reason=reason,
                occurred_at=datetime.now(UTC),
            )
            blocked_event = EgressBlockedEvent(
                event_id=uuid4(),
                session_id=session_id,
                occurred_at=datetime.now(UTC),
                source="cloud_egress_policy",
                payload=blocked_payload,
            )
            persisted_blocked = None
            try:
                persisted_blocked = await self._event_store.append(cast(EventModel, blocked_event))
            except Exception as exc:
                _LOGGER.warning("Failed to persist egress blocked event: %s", exc)

            if self._event_hub is not None:
                try:
                    payload = cast(dict[str, object], blocked_event.model_dump(mode="json"))
                    await self._event_hub.publish(payload)
                except Exception as exc:
                    _LOGGER.warning("Failed to publish egress blocked event to EventHub: %s", exc)
                else:
                    if persisted_blocked is not None:
                        try:
                            await self._event_store.mark_published(persisted_blocked.event_id)
                        except Exception as exc:
                            _LOGGER.warning("Failed to mark blocked event published: %s", exc)


# Alias for backward compatibility
CloudEgressPolicy = CloudEgressGateway
