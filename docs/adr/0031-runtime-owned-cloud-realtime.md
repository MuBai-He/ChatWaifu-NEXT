# ADR 0031: Runtime-owned, provider-neutral cloud realtime subsystem

- Status: Accepted
- Date: 2026-09-03
- Validation state: Foundation contracts, fake backend, session mirror, media bridge, and egress policy tests required; real provider integrations deferred

## Context

ChatWaifu NEXT provides a galgame-style conversational character (Ayachi Nene) with real-time voice,
Live2D animations, structured memory, runtime skills, and desktop pet features. In Phases 12, 12.5,
and 12.5.1, the local-first cascade pipeline was hardened: microphone audio enters Pipecat, passes VAD,
reaches local STT (faster-whisper), feeds Character Kernel and LLM, generates speech through neural TTS
(Qwen3-TTS / GPT-SoVITS), and plays out over WebRTC while respecting strict 3-tuple generation cancellation
(`session_id`, `turn_id`, `generation_id`).

Emerging cloud multimodal models (such as OpenAI Realtime and Gemini Live) offer unified speech-to-speech
streaming with low voice-to-voice latency. However, treating a cloud realtime provider as the conversational
runtime would break ChatWaifu invariants:

1. The cloud provider has no concept of ChatWaifu persistent character canon, affect dynamics, relationship
   progression, or structured episodic memory.
2. Direct client-to-cloud connections (from Web or Tauri) would leak provider credentials and bypass
   ChatWaifu runtime permissions, skill execution boundaries, and privacy egress policies.
3. Provider WebSocket protocols are proprietary, volatile, and asymmetric; binding application state directly
   to OpenAI or Gemini SDK data models would create tight coupling and vendor lock-in.
4. Voice identity is a core character asset: native provider voices (e.g. generic cloud voices) must never
   be conflated with character-specific neural voices, and playback truth must remain synchronized with
   the avatar and client playback acknowledgment.

Therefore, Cloud Realtime must be designed as a **Runtime-owned, provider-neutral subsystem**, where the
cloud provider is strictly an external speech-to-speech adapter rather than an orchestration brain.

## Decision

### 1. Runtime remains the sole domain authority

`session_id`, `turn_id`, `generation_id`, and `skill_run_id` are minted and owned exclusively by ChatWaifu
Runtime. Opaque provider session and response identifiers are tracked in an internal `RealtimeSessionLineage`
and session mirror mapping, and are never promoted to domain primary keys.

### 2. Provider isolation and zero SDK leakage

Neither Character Kernel, Memory System, Conversation Store, EventHub, nor frontend clients may import
provider SDKs or parse proprietary provider JSON events. All communication with cloud backends passes
through a typed, provider-neutral protocol layer (`CloudRealtimeBackend` and `CloudRealtimeSession`).
Raw provider events are normalized at the adapter/mirror boundary before entering domain sinks.

### 3. Separation of media plane and persistent domain facts

High-frequency audio frames (PCM) flow strictly through the Pipecat media plane and bounded in-memory queues.
Raw PCM is never written to SQLite `events`, `outbox`, or audit tables.
Transcript deltas are treated as ephemeral telemetry forwarded over EventHub for real-time UI display.
Only final transcripts, terminal generation outcomes (`completed`, `cancelled`, `interrupted`), usage receipts,
and egress audit receipts are eligible for persistent storage.

### 4. Cancellation precedence and generation tombstones

When a user barges in or the runtime cancels an ongoing turn:

1. The active `generation_id` is immediately recorded in the local invalidation registry (tombstone).
2. An `interrupt` signal is dispatched to the provider session.
3. Media buffers downstream are purged to halt playback immediately.
4. Any late-arriving audio frames, transcript deltas/finals, completion events, or tool calls from the
   cancelled generation are intercepted by generation fences and safely discarded. A late event must never
   resurrect a superseded generation.

### 5. Explicit cloud egress policy and audit receipts

Cloud egress is governed by `privacy.cloud_egress` (`allow`, `ask`, `deny`):

- `deny`: Cloud backends are completely blocked; backend `open_session` calls must be strictly 0, and no
  audio or context may leave the machine.
- `ask`: A cloud session cannot be opened without explicit user consent. In the absence of an active
  grant, backend `open_session` calls remain 0.
- `allow`: Context is compiled through a budgeted `RealtimeContextPatchBuilder`, emitting a minimal patch
  (persona summary, relationship/affect state, filtered memory excerpts, and skill signatures without secrets).
  An `EgressReceipt` is recorded in the existing `EventStore` documenting the destination, component classes,
  record hashes, byte/token count, and policy decision, without duplicating sensitive plaintext.

### 6. Phased rollout: Fake first, single provider next, multi-provider later

To ensure testability and prevent half-baked implementations:

- Phase 13.0–13.3 builds the contract, deterministic `FakeCloudRealtimeBackend`, session mirror, Pipecat media
  bridge, context builder, and egress policy.
- Phase 13.4+ will integrate a single real provider adapter behind this interface.
- Automatic routing and multi-provider failover are explicitly deferred to later phases.

### 7. Voice identity differentiation

Cloud realtime native voices are tagged as external provider voices and clearly distinguished from
character neural TTS (Qwen3-TTS / GPT-SoVITS). Character visual novel presentation and Live2D lipsync
continue to consume normalized playback streams regardless of the underlying voice source.

## Consequences

- The existing Cascade pipeline (local VAD → ASR → LLM → TTS) remains the default and intact.
- Cloud Realtime can be thoroughly tested deterministically using `FakeCloudRealtimeBackend` without
  requiring real API keys, internet connectivity, or incurring provider costs.
- The boundary between media frames and domain facts is strictly preserved across both cascade and cloud paths.
- Egress policies are enforced before any network connection can be established.
- Adding OpenAI Realtime or Gemini Live in future iterations requires only implementing `CloudRealtimeBackend`
  without refactoring character, memory, conversation, or frontend state.

## Alternatives

1. **Direct client-to-cloud WebRTC/WebSocket connection**: Rejected because it exposes API keys to client
   runtimes, bypasses server-side memory extraction and egress audit, and prevents unified session coordination.
2. **Adopting the cloud provider's session as the ChatWaifu session**: Rejected because cloud sessions are
   transient, opaque, vendor-specific, and lack support for long-term memory, skills, and local character state.
3. **Persisting PCM chunks in EventStore**: Rejected because audio streams generate hundreds of frames per second,
   which would cause severe SQLite write amplification and database bloat.

## References

- [ADR 0002: Pipecat media plane](0002-pipecat-media-plane.md)
- [ADR 0003: Domain event envelope](0003-domain-event-envelope.md)
- [ADR 0004: Generation ID cancellation](0004-generation-id-cancellation.md)
- [ADR 0009: Explicit cloud egress policy](0009-cloud-egress-policy.md)
- [ADR 0015: Persistent character kernel](0015-persistent-character-kernel.md)
- [ADR 0022: Conversation composition and persistence ports](0022-conversation-composition-and-persistence-ports.md)
