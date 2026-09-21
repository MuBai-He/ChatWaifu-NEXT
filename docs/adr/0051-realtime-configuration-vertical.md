# ADR 0051: Realtime Configuration Vertical, CAS Revision Control, Write-Only Local Secrets, and WebRTC Dynamic Admission

- Status: Accepted
- Date: 2026-09-11
- Scope: Phase 13.8A

## Context

Phase 13 introduces full-duplex Cloud Realtime voice interaction (ADR 0031, ADR 0046, ADR 0047, ADR 0049, ADR 0050) alongside ChatWaifu's native Cascade voice pipeline (STT -> LLM -> TTS). To enable users to switch modes and configure provider options seamlessly from the desktop settings surface, a dedicated runtime configuration vertical is required:

1. **Dual Connection Modes and Provider Profiles**: Users must be able to toggle between `cascade` (local pipeline) and `cloud_realtime` (OpenAI Realtime), customize the cloud model, provider voice, transcription model, and cloud tools toggle.
2. **Write-Only Local Secrets and Zero-Leak Guarantee**: Cloud API keys must be securely stored on the local filesystem (`.local/config/realtime-secrets.json` with permissions `0600`). Raw API keys must **never** be exposed via `GET /v1/realtime/configuration`, stored in SQLite databases, emitted in domain events or logs, reflected in validation error responses, or leaked in exception `__repr__`.
3. **Optimistic Concurrency Control (CAS)**: Concurrent UI tabs or settings modifications must not silently overwrite each other. Updates require `expected_revision` verification; stale revisions must return `HTTP 409 Conflict`.
4. **Dynamic Activation Without Desktop Restart**: Switching configuration modes must not require terminating or restarting the runtime process. Fresh WebRTC connections must immediately pick up updated settings, while existing active connections (`pc_id`) must remain pinned to their initial snapshot across SDP renegotiations to prevent in-flight pipeline corruption.
5. **Incomplete Configuration vs. Safe Admission**: Users must be permitted to save draft or partial configurations (e.g. empty model, cleared API key). However, WebRTC connection admission must strictly validate completeness before initializing cloud resources, failing with actionable, safe HTTP 400/403 errors.
6. **Cloud Egress Policy and Scoped Consent**: In accordance with ADR 0009 and ADR 0049, `deny` egress policy blocks all cloud voice turns. In `ask` mode, explicit UI consent mints a session-scoped grant for `backend_id='openai'`; when tools are enabled, `tool_result` is granted. If UI consent is revoked, connection admission fails closed with zero socket traffic.

## Decision

### 1. SQLite Realtime Configuration Schema and Atomic CAS

Migration 31 adds the `realtime_configurations` table:

```sql
CREATE TABLE realtime_configurations (
    id TEXT PRIMARY KEY,
    schema_version TEXT NOT NULL,
    revision INTEGER NOT NULL,
    connection_mode TEXT NOT NULL CHECK(connection_mode IN ('cascade', 'cloud_realtime')),
    cloud_backend TEXT NOT NULL,
    model TEXT NOT NULL,
    voice TEXT NOT NULL,
    transcription_model TEXT NOT NULL,
    cloud_tools_enabled INTEGER NOT NULL CHECK(cloud_tools_enabled IN (0, 1)),
    cloud_egress_consent INTEGER NOT NULL CHECK(cloud_egress_consent IN (0, 1)),
    secret_key_ref TEXT,
    updated_at TEXT NOT NULL
);
```

- Optimistic concurrency is enforced in `SQLiteRealtimeConfigurationRepository.update_configuration_cas` using `WHERE id = 'default' AND revision = ?`.
- Only a non-secret reference (`secret_key_ref`) is stored in SQLite; raw API keys are excluded from the database.

### 2. Write-Only Atomic Secret Storage

Local API keys are managed by `AtomicSecretStore`:

- Persisted at `.local/config/realtime-secrets.json` (or `<config_dir>/realtime-secrets.json`) with `0600` file permissions.
- Secret mutations generate a unique revision key (`openai_key_r{rev}_{uuid}`). The new secret is written to disk _before_ executing the database CAS.
- Upon successful CAS commit, the immutable in-memory snapshot is published before obsolete keys are pruned. Cleanup failures are logged and cannot leave readers on the previous revision.
- Once storage mutation starts, cancellation is propagated only after the mutation and snapshot publication finish under the writer lock. Immutable staged keys are retained after uncertain persistence outcomes; a later successful mutation prunes unreferenced candidates. Previously committed references are never compensated away.
- Explicit `clear_api_key: True` removes secret references and prunes the store; subsequent restarts never resurrect keys from environment variables or initial settings.
- Corrupted secret storage files fail closed, logging errors and setting `api_key_configured = False` without overwriting the damaged file.

### 3. API Contract and Input Sanitization

Two REST endpoints manage configuration:

- `GET /v1/realtime/configuration`: Returns public metadata (`schema_version`, `revision`, `connection_mode`, `cloud_backend`, `model`, `voice`, `transcription_model`, `cloud_tools_enabled`, `cloud_egress_consent`, `api_key_configured`, `active_connections`). Never contains raw secret strings.
- `PUT /v1/realtime/configuration`: Validates updates with Pydantic `extra="forbid"`. Rejects non-blank `api_key` combined with `clear_api_key: True` with HTTP 422. Stale `expected_revision` returns HTTP 409.
- Realtime configuration validation returns a fixed safe 422 message. Raw input, arbitrary property names in error locations, and custom validation messages are not reflected. Other routes keep their existing validation response contract.

### 4. WebRTC Connection Admission and Renegotiation Freeze

In `PipecatMediaAdapter.offer()`:

- An immutable `RealtimeConnectionSnapshot` is captured _synchronously before any `await`_.
- For an existing connection `pc_id` undergoing SDP renegotiation (`restart_pc=False`), the adapter preserves the originally captured snapshot and `bridge_factory` from `_pc_snapshots[pc_id]`.
- For fresh connections, admission checks `_validate_admission`:
  - `connection_mode == "cloud_realtime"`: requires non-empty `model`, valid `api_key` (for OpenAI), and `cloud_egress_consent=True`.
  - Checks `CloudEgressGateway`: `deny` policy rejects; `ask` policy registers scoped session consent including `tool_result` if tools are enabled.
  - Violations raise clean, safe `ValueError` (HTTP 400) or `PermissionError` (HTTP 403).

### 5. Dynamic Factory Construction and Resource Teardown

- `RuntimeContainer` dynamically builds provider bridges per connection snapshot via `_build_cloud_bridge_factory_for_snapshot`.
- When operating in `cascade` mode, cloud egress gateways and cloud backends are not eagerly instantiated at container boot, adhering to zero-cloud-overhead constraints.
- Dynamic switching from `cloud_realtime` to `cascade` admits subsequent offers into the cascade pipeline immediately upon connection teardown, with no lingering cloud tasks or memory leaks.

## Consequences & Verification

- **Automated Verification**: Comprehensive unit, API, fault injection, and wire integration tests pass in `test_realtime_configuration.py`, covering contract compliance, CAS conflicts, secret redaction, 422 input stripping, corrupt store fail-closed behavior, renegotiation freeze, loopback wire verification, and cascade switching.
- **User interface**: Desktop voice settings and Web chat settings share the same panel. Blank keys retain saved credentials; explicit clear removes the reference. A 409 preserves edits until the user reloads. Runtime restart aborts stale requests, clears ephemeral keys, and reloads when ready. Saving never contacts a provider or remounts the avatar.
- **Activation**: All settings, including consent, apply to the next voice connection. Users must hang up to stop an active call immediately. Saved bootstrap profiles remain authoritative after environment changes.
- **Acceptance**: An isolated local Runtime and browser verified actual save/read, refresh, explicit clear, and process restart without key resurrection. Public OpenAI connectivity and physical microphone/audio acceptance remain pending.
