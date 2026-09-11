# ADR 0050: Cloud Realtime Single-Round Tool Bridge and Permissioned Skill Execution Boundary

- Status: Accepted
- Date: 2026-09-11
- Scope: Phase 13.5A

## Context

Cloud Realtime speech sessions (such as OpenAI Realtime) support model-initiated function/tool calling over full-duplex WebSockets. However, directly exposing arbitrary Runtime Skills or MCP host instances to a cloud provider introduces critical safety, privacy, and stability risks:

1. **Protocol and Socket Blocking**: Executing skills synchronously within the realtime coordinator's event pump loop stalls audio streaming, buffering, and interruption handling.
2. **Privilege Escalation and Untrusted Egress**: Exposing destructive (`WRITE`) side-effects or tools requiring interactive user confirmation during live voice turns can trigger unconfirmed side-effects or hang waiting for user UI input while audio is streaming. Outbound results must also undergo privacy egress policy enforcement and durable auditing before reaching the provider socket.
3. **Session Lineage and Rebinding Collisions**: The provider may emit multiple responses per turn (a tool decision response followed by a spoken continuation response). Without strict generation reservation and 4-way lineage (`session_id`, `turn_id`, `generation_id`, `provider_tool_call_id`), tool executions cannot be accurately attributed or cancelled upon user barge-in.
4. **Replay and Mutation Attacks**: Providers or network jitter may duplicate tool call requests across differing wire event IDs, or mutate argument payloads for an in-flight call identity.

## Decision

### 1. Provider-Neutral Tool Contracts

Tool calling contracts are standardized at the `chatwaifu_runtime.realtime.cloud.contracts` boundary:

- `RealtimeToolDefinition`: Clean JSON schema projection with budgeted properties.
- `RealtimeToolCall`: Structured tool call request with `call_id`, `name`, and parsed `arguments`.
- `ToolCallRequestedEvent`: Coordinator-level domain event emitted when a decision round completes with tool calls.
- `CloudRealtimeSession`: Extended with `submit_tool_result(call_id, output)` and `request_continuation(generation_id, disable_tools=True)`.

### 2. Narrow Snapshot Tool Projection

Cloud tools are opt-in (`realtime.cloud_tools_enabled`, default false). With tools enabled,
the initial request uses GA `output_modalities: ["text"]`; the final request uses
`output_modalities: ["audio"]`. Both tool-bearing and empty decisions pass through the
coordinator reservation before final speech. This adds a model round trip only when opted in.

Only a strictly bounded subset of Runtime Skills is projected into cloud realtime sessions via `project_cloud_realtime_tools`:

- **Source**: Trusted `builtin` skills only (external MCP plugins are excluded in Phase 13.5A).
- **Side Effect**: Read-only (`SideEffect.READ`). Write side-effects are excluded.
- **Confirmation**: `confirmation_required=False`. Interactive human-in-the-loop tools are prohibited.
- **Timeout**: Bounded interactive timeout (`<= 30.0` seconds).
- **Size and Count Budget**: Maximum 8 tools per session; schema payload bounded to 24 KiB total. Names are allocated deterministically via `allocate_tool_names`.

### 3. Dedicated Asynchronous `CloudToolBridge`

All tool execution is decoupled from the realtime coordinator event pump:

- **Non-Blocking Execution**: `handle_tool_calls` synchronously registers call identities and spawns an asynchronous execution task (`_execute_tool_round`), allowing the coordinator event loop to continue pumping frames.
- **Deduplication and Mutation Defense**:
  - Exactly one decision round is permitted per generation (`_executed_generations`).
  - At most 4 tool calls are executed per decision round (`MAX_AGENT_TOOL_CALLS = 4`).
  - Call IDs are tracked alongside an SHA-256 digest of invocation arguments. If arguments mutate on an existing `call_id`, the generation is cancelled immediately (`call_args_mutation_rejected`).
  - Duplicate calls across differing event IDs with identical digests are dropped without re-executing.
- **Invoke Admission Shielding**: Skill invocation is shielded against premature cancellation until an active `run_id` is registered in `_active_runs`. Once admitted, cancellation invokes `cancel_skill_run_safely`.
- **Generation Active and Tombstone Re-checking**: Generation activity is checked at execution boundaries; provider writes recheck interruption under their write lock. Cancelled or tombstoned generations strictly discard results.

### 4. Cloud Egress Gateway Enforcement

Outbound tool results must pass through `CloudEgressGateway.evaluate_and_submit_tool_result` before reaching provider sockets:

- **Policy Modes**:
  - `allow`: Permitted with durable audit.
  - `ask`: Requires explicit `EgressGrant` specifically authorizing the `tool_result` component kind; ungranted or mismatched grants fail closed with `ConsentRequiredError`.
  - `deny`: Blocked immediately with `PolicyDeniedError`.
- **Durable Audit Write First**: Egress receipts are committed to the durable SQLite event store _before_ the socket write. If audit persistence fails, execution fails closed with zero provider socket writes.
- **Metadata-Only Receipts**: Raw result payloads and arguments never enter egress receipts or audit logs.
- **Result Size Bounds**: Payloads exceeding 32 KiB are truncated with a `truncated: True` flag and summary.

### 5. Lineage Tracking and Final Turn Continuation

- **4-Way Lineage**: Every execution records `session_id`, `turn_id`, `generation_id`, and `provider_tool_call_id` into the SQLite `skill_runs` table with origin `"agent"`.
- **Continuation Lineage Reservation**: Before invoking `session.request_continuation`, `RealtimeSessionMirror.reserve_continuation(generation_id)` and `reset_provider_response_done(generation_id)` are invoked. The OpenAI adapter validates that any second `response.created` maps to an existing generation only when continuation is explicitly reserved.
- **Tools Disabled on Continuation**: Continuation requests explicitly specify `tools=[]` and `tool_choice="none"`, enforcing a strict single decision round per voice turn.

## Verification and Limits

### Automated Verification

The test suite in `services/runtime/tests/test_cloud_realtime_tools.py` covers the following lifecycle cases:

1. Full loopback: OpenAI wire -> RuntimeSkillService -> SQLite `skill_runs` 4-way lineage -> tool result -> continuation audio and playback ACK.
2. Skill execution failure / permission denied yields sanitized error payloads without terminating the session.
3. Unknown or unprojected tools return sanitized errors without calling the skill gateway.
4. Oversized (>64 KiB) or malformed JSON arguments fail closed safely.
5. Mutated arguments on the same `call_id` trigger generation cancellation; duplicate call IDs across differing events are suppressed; max 4 calls enforced.
6. User interruption cancels in-flight tasks and runs with shielded admission, sending zero socket writes.
7. Provider EOF/teardown cancels all in-flight tool tasks without orphaned background tasks.
8. Late responses for tombstoned generations are dropped.
9. Provider response rebinding without explicit continuation reservation is rejected.
10. Egress policy deny/ask and audit persistence failure guarantee zero provider socket writes.
11. Result payloads exceeding 32 KiB are safely truncated.

### Limits and Deferred Scope

- **Phase 13.5A Scope**: Confined to trusted `builtin` read-only skills, single decision round, and OpenAI/Fake providers.
- **Deferred to Phase 13.5B+**:
  - External MCP plugin tool exposure and sandboxed process execution.
  - State-mutating `WRITE` skills and interactive user confirmation flows.
  - Background asynchronous job handles for long-running operations.
  - Multi-round tool execution.
- **Live Acceptance**: Live public OpenAI network connectivity and real microphone hardware testing remain pending.
