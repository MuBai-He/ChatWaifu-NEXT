"""Session mirror and generation fence for cloud realtime sessions.

Maintains bidirectional mapping between Runtime domain identities (session_id,
turn_id, generation_id) and opaque provider identities (provider_session_id,
provider_response_id), while enforcing strict cancellation tombstones to prevent
stale frames or out-of-order completions from corrupting domain state.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from uuid import UUID

from chatwaifu_runtime.realtime.cloud.contracts import RealtimeSessionLineage


@dataclass(slots=True)
class GenerationBinding:
    """Active binding of a runtime generation to a provider response."""

    generation_id: UUID
    turn_id: UUID
    provider_response_id: str | None = None
    accumulated_delta_text: list[str] = field(default_factory=lambda: list[str]())
    authoritative_final_text: str | None = None
    is_completed: bool = False
    is_cancelled: bool = False
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    @property
    def accumulated_text(self) -> list[str]:
        return self.accumulated_delta_text


class RealtimeSessionMirror:
    """Tracks session and generation lineage, deduplicates events, and fences tombstones."""

    def __init__(
        self,
        session_id: UUID,
        *,
        backend_id: str,
        provider_session_id: str = "",
    ) -> None:
        self.session_id: UUID = session_id
        self.backend_id: str = backend_id
        self.provider_session_id: str = provider_session_id

        self._active_generation_id: UUID | None = None
        self._active_turn_id: UUID | None = None
        self._bindings: dict[UUID, GenerationBinding] = {}
        self._response_to_generation: dict[str, UUID] = {}
        self._tombstones: set[UUID] = set()
        self._seen_event_keys: set[str] = set()

    @property
    def active_generation_id(self) -> UUID | None:
        return self._active_generation_id

    @property
    def active_turn_id(self) -> UUID | None:
        return self._active_turn_id

    def set_provider_session_id(self, provider_session_id: str) -> None:
        self.provider_session_id = provider_session_id

    def register_generation(
        self,
        generation_id: UUID,
        turn_id: UUID,
        *,
        provider_response_id: str | None = None,
    ) -> GenerationBinding:
        """Register a new runtime generation as active."""
        binding = GenerationBinding(
            generation_id=generation_id,
            turn_id=turn_id,
            provider_response_id=provider_response_id,
        )
        self._bindings[generation_id] = binding
        self._active_generation_id = generation_id
        self._active_turn_id = turn_id
        if provider_response_id:
            self._response_to_generation[provider_response_id] = generation_id
        return binding

    def bind_provider_response(
        self,
        provider_response_id: str,
        generation_id: UUID | None = None,
    ) -> GenerationBinding | None:
        """Associate an opaque provider response id with a runtime generation."""
        target_gen_id = generation_id or self._active_generation_id
        if target_gen_id is None:
            return None

        binding = self._bindings.get(target_gen_id)
        if binding is None:
            return None

        binding.provider_response_id = provider_response_id
        self._response_to_generation[provider_response_id] = target_gen_id
        return binding

    def resolve_generation_id(
        self,
        *,
        generation_id: UUID | None = None,
        provider_response_id: str | None = None,
    ) -> UUID | None:
        """Resolve the runtime generation id from either a direct id or provider response id."""
        if generation_id is not None:
            return generation_id
        if provider_response_id is not None:
            return self._response_to_generation.get(provider_response_id)
        return self._active_generation_id

    def get_turn_id(self, generation_id: UUID) -> UUID | None:
        binding = self._bindings.get(generation_id)
        return binding.turn_id if binding else self._active_turn_id

    def append_text(self, generation_id: UUID, delta: str) -> None:
        self.append_delta_text(generation_id, delta)

    def append_delta_text(self, generation_id: UUID, delta: str) -> None:
        binding = self._bindings.get(generation_id)
        if binding is not None:
            binding.accumulated_delta_text.append(delta)

    def set_authoritative_final_text(self, generation_id: UUID, text: str) -> None:
        binding = self._bindings.get(generation_id)
        if binding is not None:
            binding.authoritative_final_text = text

    def get_accumulated_text(self, generation_id: UUID) -> str:
        binding = self._bindings.get(generation_id)
        if binding is None:
            return ""
        return "".join(binding.accumulated_delta_text)

    def get_completed_text(self, generation_id: UUID, event_final_text: str | None = None) -> str:
        """Determine completed text using priority:
        event.final_text > authoritative_final > accumulated_delta.
        """
        if event_final_text is not None:
            return event_final_text
        binding = self._bindings.get(generation_id)
        if binding is None:
            return ""
        if binding.authoritative_final_text is not None:
            return binding.authoritative_final_text
        return "".join(binding.accumulated_delta_text)

    def is_active(self, generation_id: UUID) -> bool:
        """Return True only if the generation is currently active and not tombstoned."""
        return generation_id == self._active_generation_id and generation_id not in self._tombstones

    def is_tombstoned(self, generation_id: UUID) -> bool:
        """Return True if the generation was cancelled or finished."""
        return generation_id in self._tombstones

    def cancel_generation(self, generation_id: UUID) -> None:
        """Tombstone a generation, invalidating all subsequent late frames."""
        self._tombstones.add(generation_id)
        binding = self._bindings.get(generation_id)
        if binding is not None:
            binding.is_cancelled = True
        if self._active_generation_id == generation_id:
            self._active_generation_id = None
            self._active_turn_id = None

    def complete_generation(self, generation_id: UUID) -> None:
        """Mark generation completed and fence it against late deltas."""
        self._tombstones.add(generation_id)
        binding = self._bindings.get(generation_id)
        if binding is not None:
            binding.is_completed = True
        if self._active_generation_id == generation_id:
            self._active_generation_id = None
            self._active_turn_id = None

    def is_duplicate(self, event_key: str) -> bool:
        """Check if an event key has already been processed, recording it if not."""
        if event_key in self._seen_event_keys:
            return True
        self._seen_event_keys.add(event_key)
        return False

    def build_lineage(self, generation_id: UUID | None = None) -> RealtimeSessionLineage:
        gen_id = generation_id or self._active_generation_id
        binding = self._bindings.get(gen_id) if gen_id else None
        return RealtimeSessionLineage(
            session_id=self.session_id,
            turn_id=binding.turn_id if binding else self._active_turn_id,
            generation_id=gen_id,
            backend_id=self.backend_id,
            provider_session_id=self.provider_session_id,
            provider_response_id=binding.provider_response_id if binding else None,
            created_at=binding.created_at if binding else datetime.now(UTC),
            updated_at=datetime.now(UTC),
        )
