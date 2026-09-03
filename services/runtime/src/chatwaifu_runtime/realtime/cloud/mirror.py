"""Session mirror and generation fence for cloud realtime sessions.

Maintains bidirectional mapping between Runtime domain identities (session_id,
turn_id, generation_id) and opaque provider identities (provider_session_id,
provider_response_id), while enforcing strict cancellation tombstones to prevent
stale frames or out-of-order completions from corrupting domain state.
"""

from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass, field
from datetime import UTC, datetime
from uuid import UUID

from chatwaifu_runtime.realtime.cloud.contracts import RealtimeSessionLineage

MAX_SEEN_EVENT_KEYS = 5000
MAX_TOMBSTONES = 500
MAX_BINDINGS = 200
MAX_RESPONSES = 200
MAX_ITEMS = 500


@dataclass(slots=True)
class GenerationBinding:
    """Active binding of a runtime generation to a provider response."""

    generation_id: UUID
    turn_id: UUID
    provider_response_id: str | None = None
    utterance_id: UUID | None = None
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
        max_seen_event_keys: int = MAX_SEEN_EVENT_KEYS,
        max_tombstones: int = MAX_TOMBSTONES,
        max_bindings: int = MAX_BINDINGS,
        max_responses: int = MAX_RESPONSES,
        max_items: int = MAX_ITEMS,
    ) -> None:
        self.session_id: UUID = session_id
        self.backend_id: str = backend_id
        self.provider_session_id: str = provider_session_id

        self._max_seen_event_keys = max_seen_event_keys
        self._max_tombstones = max_tombstones
        self._max_bindings = max_bindings
        self._max_responses = max_responses
        self._max_items = max_items

        self._active_generation_id: UUID | None = None
        self._active_turn_id: UUID | None = None
        self._last_generation_id: UUID | None = None
        self._last_turn_id: UUID | None = None

        self._bindings: OrderedDict[UUID, GenerationBinding] = OrderedDict()
        self._response_to_generation: OrderedDict[str, UUID] = OrderedDict()
        self._item_to_generation: OrderedDict[str, UUID] = OrderedDict()
        self._tombstones: OrderedDict[UUID, None] = OrderedDict()
        self._seen_event_keys: OrderedDict[str, None] = OrderedDict()

    @property
    def active_generation_id(self) -> UUID | None:
        return self._active_generation_id

    @property
    def active_turn_id(self) -> UUID | None:
        return self._active_turn_id

    @property
    def last_generation_id(self) -> UUID | None:
        return self._last_generation_id

    @property
    def last_turn_id(self) -> UUID | None:
        return self._last_turn_id

    def set_provider_session_id(self, provider_session_id: str) -> None:
        self.provider_session_id = provider_session_id

    def has_binding(self, generation_id: UUID) -> bool:
        """Return True only if the generation was explicitly registered."""
        return generation_id in self._bindings

    def get_utterance_id(self, generation_id: UUID) -> UUID | None:
        """Return the admitted utterance id without any active/last fallback."""
        binding = self._bindings.get(generation_id)
        return binding.utterance_id if binding is not None else None

    def register_generation(
        self,
        generation_id: UUID,
        turn_id: UUID,
        *,
        provider_response_id: str | None = None,
        utterance_id: UUID | None = None,
    ) -> GenerationBinding:
        """Register a new runtime generation as active."""
        binding = GenerationBinding(
            generation_id=generation_id,
            turn_id=turn_id,
            provider_response_id=provider_response_id,
            utterance_id=utterance_id,
        )
        self._bindings[generation_id] = binding
        if len(self._bindings) > self._max_bindings:
            evicted_gen_id, _ = self._bindings.popitem(last=False)
            self._purge_response_mappings(evicted_gen_id)

        self._active_generation_id = generation_id
        self._active_turn_id = turn_id
        self._last_generation_id = generation_id
        self._last_turn_id = turn_id

        if provider_response_id:
            self._response_to_generation[provider_response_id] = generation_id
            if len(self._response_to_generation) > self._max_responses:
                self._response_to_generation.popitem(last=False)
        return binding

    def bind_provider_response(
        self,
        provider_response_id: str,
        generation_id: UUID | None = None,
    ) -> GenerationBinding | None:
        """Associate an opaque provider response id with a runtime generation."""
        target_gen_id = generation_id or self._active_generation_id or self._last_generation_id
        if target_gen_id is None:
            return None

        binding = self._bindings.get(target_gen_id)
        if binding is None:
            return None

        binding.provider_response_id = provider_response_id
        self._response_to_generation[provider_response_id] = target_gen_id
        if len(self._response_to_generation) > self._max_responses:
            self._response_to_generation.popitem(last=False)
        return binding

    def _purge_response_mappings(self, generation_id: UUID) -> None:
        """Remove provider-response/item mappings pointing at an evicted generation.

        Prevents a stale provider id from resolving to a binding that no longer
        exists after capacity eviction.
        """
        stale_responses = [
            response_id
            for response_id, mapped_gen_id in self._response_to_generation.items()
            if mapped_gen_id == generation_id
        ]
        for response_id in stale_responses:
            del self._response_to_generation[response_id]
        stale_items = [
            item_id
            for item_id, mapped_gen_id in self._item_to_generation.items()
            if mapped_gen_id == generation_id
        ]
        for item_id in stale_items:
            del self._item_to_generation[item_id]

    def bind_provider_item(
        self,
        provider_item_id: str,
        generation_id: UUID,
    ) -> GenerationBinding | None:
        """Associate a provider input/transcript item id with a registered generation.

        Item ids live in a different provider namespace than response ids and
        are recorded only against an explicitly registered binding, typically
        learned from a candidate that already carries Runtime identity.
        """
        binding = self._bindings.get(generation_id)
        if binding is None:
            return None
        self._item_to_generation[provider_item_id] = generation_id
        if len(self._item_to_generation) > self._max_items:
            self._item_to_generation.popitem(last=False)
        return binding

    def current_generation_id(self) -> UUID | None:
        """Return the active generation for Runtime-internal media-plane use only.

        Provider events must never use this: they resolve strictly through
        :meth:`resolve_generation_id` and are dropped when identity-less.
        """
        return self._active_generation_id

    def resolve_generation_id(
        self,
        *,
        generation_id: UUID | None = None,
        provider_response_id: str | None = None,
        provider_item_id: str | None = None,
    ) -> UUID | None:
        """Strictly resolve a Runtime generation id from provider-supplied identity.

        Every explicitly supplied identity is honored only when backed by a
        registered binding; unknown explicit identities resolve to None.
        Identity-less lookups (all arguments None) also resolve to None:
        provider events must carry Runtime identity or a registered provider
        mapping instead of guessing the active or last generation. Use
        :meth:`current_generation_id` for Runtime-internal queries.
        """
        if generation_id is not None:
            return generation_id if generation_id in self._bindings else None
        if provider_response_id is not None:
            resolved = self._response_to_generation.get(provider_response_id)
            if resolved is not None and resolved in self._bindings:
                return resolved
            return None
        if provider_item_id is not None:
            resolved_item = self._item_to_generation.get(provider_item_id)
            if resolved_item is not None and resolved_item in self._bindings:
                return resolved_item
            # Legacy alias: some providers reuse the response id as the item
            # id. Consult the response map before giving up.
            resolved_legacy = self._response_to_generation.get(provider_item_id)
            if resolved_legacy is not None and resolved_legacy in self._bindings:
                return resolved_legacy
            return None
        return None

    def get_turn_id(self, generation_id: UUID | None = None) -> UUID | None:
        if generation_id is not None:
            binding = self._bindings.get(generation_id)
            return binding.turn_id if binding is not None else None
        if self._active_turn_id is not None:
            return self._active_turn_id
        return self._last_turn_id

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
        self._tombstones[generation_id] = None
        if len(self._tombstones) > self._max_tombstones:
            self._tombstones.popitem(last=False)

        binding = self._bindings.get(generation_id)
        if binding is not None:
            binding.is_cancelled = True
        if self._active_generation_id == generation_id:
            self._active_generation_id = None
            self._active_turn_id = None

    def complete_generation(self, generation_id: UUID) -> None:
        """Mark generation completed and fence it against late deltas."""
        self._tombstones[generation_id] = None
        if len(self._tombstones) > self._max_tombstones:
            self._tombstones.popitem(last=False)

        binding = self._bindings.get(generation_id)
        if binding is not None:
            binding.is_completed = True
        if self._active_generation_id == generation_id:
            self._active_generation_id = None
            self._active_turn_id = None

    def is_duplicate(self, event_key: str) -> bool:
        """Check if an event key has already been processed, recording it if not."""
        if event_key in self._seen_event_keys:
            self._seen_event_keys.move_to_end(event_key)
            return True
        self._seen_event_keys[event_key] = None
        if len(self._seen_event_keys) > self._max_seen_event_keys:
            self._seen_event_keys.popitem(last=False)
        return False

    def build_lineage(self, generation_id: UUID | None = None) -> RealtimeSessionLineage:
        gen_id = generation_id or self._active_generation_id or self._last_generation_id
        binding = self._bindings.get(gen_id) if gen_id else None
        return RealtimeSessionLineage(
            session_id=self.session_id,
            turn_id=binding.turn_id if binding else (self._active_turn_id or self._last_turn_id),
            generation_id=gen_id,
            backend_id=self.backend_id,
            provider_session_id=self.provider_session_id,
            provider_response_id=binding.provider_response_id if binding else None,
            created_at=binding.created_at if binding else datetime.now(UTC),
            updated_at=datetime.now(UTC),
        )
