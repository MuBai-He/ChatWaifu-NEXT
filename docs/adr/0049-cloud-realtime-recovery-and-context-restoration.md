# ADR 0049: Client-Driven Cloud Realtime Recovery and Confirmed Context Restoration

- Status: Accepted
- Date: 2026-09-10
- Scope: Phase 13.7A

## Context

Cloud Realtime sessions can terminate abruptly due to provider rate limits, socket timeouts, backend errors, or transient network interruptions. In existing implementations, provider EOF or unexpected closure causes silent transport collapse, leaving the user with an unresponsive audio interface ("dead mic").

Replacing a bridge inside a running pipeline would require transferring ownership of queued
frames, playback acknowledgments, and provider listeners. This slice uses a fresh
connection so obsolete work can be canceled before admitting replacement media.
Only confirmed dialogue is restored; queued audio and tool calls are not replayed.

## Decision

### 1. Client-Driven Fresh Connection Reconstruction

Reconnection is orchestrated strictly from the client layer:

- The backend `CloudRealtimeMediaBridge` exposes a dedicated `closed_event: asyncio.Event` triggered whenever provider EOF, socket error, or bridge teardown occurs.
- `PipecatMediaAdapter` runs a session watcher task (`watch_cloud_termination`) that awaits `closed_event` and immediately shuts down the associated WebRTC peer connection transport.
- The browser voice client observes transport termination (`onStateChange("disconnected")`) and initiates an automated, backoff-bounded reconnection episode.
- To prevent infinite reconnection storms when a provider fails immediately upon handshake, the client's reconnection attempt counter is not reset on WebRTC `"connected"`; it resets only after the connection remains healthy for a sustained threshold (5,000 ms).
- Cloud provider initialization and egress authorization finish before the offer succeeds (15-second initialization deadline). Policy failures return 403, known invalid configuration returns 400, and transient provider failures return 503 with sanitized messages.
- Non-retryable HTTP responses (e.g. 4xx authorization or policy rejections) terminate recovery immediately without looping.

### 2. Targeted Connection Ownership and Serialized Admission

To resolve teardown races:

- The runtime issues a unique connection identifier (`pc_id`) per WebRTC offer.
- Browser teardown uses `DELETE /sessions/{session_id}/webrtc?pc_id=...`; the existing session-wide administrative endpoint remains available. A late teardown for a superseded connection cleanly releases obsolete resources without killing active replacement connections.
- Peer connection admission in `PipecatMediaAdapter` is serialized via a per-session lock. Superseding connections explicitly join and cancel obsolete peer tasks with a bounded 2.0-second timeout before starting the new pipeline.
- Stale offers resolved after client disconnect are targeted and deleted by their assigned `pc_id`.

### 3. Confirmed Dialogue History Snapshot (`latest_confirmed_history`)

When opening a new cloud session, the runtime reconstructs dialogue history using strict confirmation filters:

- **Finalized User Turns Only**: User turns are included only if `committed_text` is non-null.
- **Playback-Confirmed Assistant Turns Only**: Assistant turns are included only if the generation state is `'completed'` AND `spoken_text` is non-null (conforming to ADR 0048 playback confirmation). In-flight, unplayed, interrupted, or pending assistant generations are strictly excluded.
- **Photo Context Redactions**: Redacted assistant responses recorded in `photo_context_redactions` are replaced with sanitized markers before transmission.
- **Chronological Ordering & Bounded Window**: Turns are queried in descending order (most recent `limit=16`) and reversed to chronological order. Oldest-turn queries (`list_messages`) and uncommitted turns are prohibited.

### 4. Untrusted Dialogue Quoting and Egress Policy Enforcement

- History turns are formatted into the initial session prompt under `recent_history` with priority 6, subordinate to safety (priority 0), persona (priority 1), and character state.
- Every untrusted turn is safely escaped using `json.dumps(..., ensure_ascii=False)` to keep dialogue visibly separated from instructions; quoting is not a guarantee against prompt injection.
- Budget pruning prunes lower-priority dialogue history (priority 6) first, ensuring system directives and memories are never truncated for dialogue turns.
- Session opening passes through `CloudEgressGateway`, evaluating allow/ask/deny policies, fail-closing on ungranted permissions, and persisting audit receipts without leaking private text or full transcripts into audit logs.

## Verification and Limits

### Automated Verification

1. **Bridge EOF & Watcher Teardown**: Provider EOF triggers `closed_event`, adapter watcher closes peer transport cleanly within bounded time.
2. **History Filtering & Photo Redaction**: Unit and integration tests verify unplayed/pending generations are excluded, photo redaction applied, chronological order preserved, and foreign sessions isolated.
3. **Targeted Teardown & Concurrent Offers**: Adapter tests verify serialized admission, replacement of superseded tasks, and immunity of active connections against stale `DELETE ?pc_id=...` requests.
4. **Egress Gateway & Audit**: Tests confirm policy enforcement, fail-closed behavior, and metadata audit receipts without transcript leakage.
5. **Context Budget Pruning**: Verification that priority 6 history is dropped before priority 1-3 system/memory context.
6. **Frontend Reconnect Episode**: Vitest tests confirm healthy threshold reset (5s), bounded backoff exhaustion, targeted `pc_id` deletion, stale offer cleanup, and 4xx non-retryable termination.

### Limits

- Live public OpenAI network connectivity and real microphone hardware acceptance remain pending manual validation.
- Cloud tool execution (Phase 13.5), context synchronization (Phase 13.6), and Phase 17.4C remain separate roadmap items.
