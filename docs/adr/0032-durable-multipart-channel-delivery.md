# ADR 0032: Durable Multipart Channel Delivery

- Status: Accepted
- Date: 2026-09-03
- Validation state: Protocol, migration, repository state machine, adapter delivery, crash recovery, and cancellation tests required

## Context

ChatWaifu NEXT connects to external messaging platforms (e.g., WeChat via the native iLink adapter) through the `ExternalChannelGateway` defined in ADR 0029 and ADR 0030. In V1, an inbound user message generates a single canonical assistant turn with one complete `reply_text`. The delivery layer models this as a `1:1` relationship: one `ChannelTurn` maps to one `ChannelDelivery`. The delivery is leased as a whole, sent via the provider adapter, and acknowledged with a single lease-bound ACK.

This V1 model cannot safely support conversational chat pacing or multipart delivery, such as:

1. Sending split short-sentence bubbles in sequence;
2. Pacing output with natural typing delay;
3. Sending follow-up media or stickers;
4. Allowing a newly arrived user turn to interrupt and cancel remaining unsent bubbles.

Naively putting a `for send + sleep` loop inside the adapter management loop (`management.py`) is dangerous and unacceptable:

- **Crash duplicate delivery**: If the process crashes after sending Part 1 or Part 2, a restart has no durable record of partial progress and must re-claim and re-send from the beginning, spamming the user with duplicate messages.
- **Lost idempotency keys**: In V1, the provider idempotency key (`client_id`) is derived from `delivery_id`. Sending multiple bubbles under the same `client_id` causes upstream providers to deduplicate and drop subsequent bubbles; conversely, generating volatile random client IDs prevents safe replay after a crash.
- **Uncancellable tail**: If the user sends a new message while remaining bubbles are pending, the system cannot atomically cancel the unsent tail while preserving already sent bubbles.
- **Mismatched ACK granularity**: An error or lease expiration on bubble 3 cannot be acknowledged or retried independently without corrupting bubble 1 and 2 status.
- **Event loop blockage**: Arbitrary sleeping inside the delivery path degrades gateway responsiveness and complicates shutdown.

We need a durable, provider-neutral multipart delivery foundation before implementing bubble splitting, cadence, typing indicators, or media.

## Decision

We introduce a durable multipart delivery foundation decoupling the presentation delivery plan from the canonical conversation turn:

```text
1 ChannelTurn (Canonical Assistant Turn with full reply_text)
  -> 1 ChannelDeliveryPlan (Aggregate root, reusing channel_deliveries)
       -> N Ordered ChannelDeliveryParts (channel_delivery_parts table)
```

### 1. Separation of Canonical Turn and Presentation Plan

The Canonical Assistant Turn remains single, complete, and immutable in conversation history, memory extraction, and desktop UI. Bubble splitting and multipart delivery are presentation concerns owned by the External Channel Gateway. The Gateway compiles the turn's reply into a `ChannelDeliveryPlan` containing ordered `ChannelDeliveryPart` records.

### 2. Aggregate Root and Child Entity State Machine

- **Aggregate Root (`ChannelDeliveryPlan` / `channel_deliveries`)**:
  - Reuses the existing `channel_deliveries` table as the durable plan root.
  - Lifecycle statuses: `pending`, `sending`, `delivered`, `failed`, `cancelled`.
  - Tracks `plan_version` and `cancel_requested_at`.
- **Child Entity (`ChannelDeliveryPart` / `channel_delivery_parts`)**:
  - Ordered by `ordinal` (0, 1, 2, ...).
  - Lifecycle statuses: `pending`, `sending`, `delivered`, `failed`, `cancelled`, `skipped`.
  - Part properties:
    - `part_id`: Unique UUID.
    - `delivery_id`: Foreign key referencing `channel_deliveries(delivery_id)` on delete cascade.
    - `ordinal`: Integer starting from 0.
    - `kind`: `ChannelDeliveryPartKind` (initially `text`; reserves `image`).
    - `payload_json`: Strongly typed, versioned payload (e.g. `ChannelTextDeliveryPartPayload(schema_version="1.0", kind="text", text=...)`).
    - `required`: Boolean flag (default true).
    - `delay_after_ms`: Cadence hint (default 0).
    - `not_before_at`: Scheduling timestamp (default immediate).
    - `attempt`: Incrementing attempt counter.
    - `lease_id` / `lease_expires_at`: Server-timed exclusive execution lease.
    - `provider_client_id`: Stable, deterministic idempotency key.
    - `provider_message_id`: Upstream message ID assigned upon delivery.
    - `last_error_json`: Structured error on failure.
    - Timestamps: `created_at`, `updated_at`, `delivered_at`.
- **Derived Parent State**:
  - The plan status is derived from its constituent parts within the same SQLite transaction.
  - When all required parts are `delivered`, the plan becomes `delivered`.
  - If a required part transitions to `failed`, the plan transitions to `failed`.
  - If remaining parts are cancelled via `cancel_remaining_parts`, un-sent parts transition to `cancelled`, and the plan transitions to `cancelled` (even if earlier parts were delivered).

### 3. Sequential Claim and Concurrency Control (CAS)

- Parts must be claimed strictly in increasing ordinal order: Part $k+1$ cannot be claimed while Part $k$ is not terminal (`delivered` or `skipped`).
- A part is claimable only when:
  1. The parent plan is not terminal and has not requested complete abort;
  2. The part is the lowest-ordinal unfinished required part;
  3. The part is either `pending`, or `sending` with an expired lease;
  4. `not_before_at <= current_time`.
- Claiming uses atomic Compare-And-Swap (CAS) in SQLite. Concurrency is limited to at most one active lease per delivery plan. Concurrent claims result in exactly one winner; losers receive `None` or a busy conflict.

### 4. Deterministic Provider Client ID and At-Least-Once Delivery

- Every part is assigned a deterministic, globally unique `provider_client_id` at plan creation time:
  ```text
  chatwaifu-{delivery_id_hex}-{ordinal:03d}
  ```
- This client ID is strictly preserved across lease expiries, retries, and process restarts.
- We do **not** claim exactly-once visible delivery: if a process crashes after a provider successfully transmits a message but before the Runtime commits the ACK, the restart will retry with the identical `provider_client_id`, relying on the provider's upstream idempotency window to deduplicate.

### 5. Part-Level Lease and ACK

- The adapter claims a lease for the next pending part.
- Upon sending, the adapter submits an acknowledgement containing `(delivery_id, part_id, lease_id)`.
- If the lease has expired or been reassigned, the ACK is rejected without modifying database state.
- ACKing a part as `delivered` is idempotent: duplicate ACKs for an already delivered part return the existing record without altering timestamps or status.
- A delivered part is strictly irreversible: it can never be moved back to pending, failed, or cancelled.

### 6. Crash Recovery Semantics

- Crash recovery requires no in-memory cursor.
- On startup or subsequent polling, the repository scans for the lowest-ordinal unfinished part.
- Expired leases are reclaimable.
- Previously delivered parts are recognized as terminal and skipped, guaranteeing that successfully delivered bubbles are never resent.

### 7. Cancellation Semantics

- `cancel_remaining_delivery_parts` sets `cancel_requested_at` on the plan and marks all `pending` parts as `cancelled`.
- Parts already `delivered` remain `delivered` and are not revoked.
- An in-flight `sending` part is allowed to finish its active lease; its eventual ACK will be accepted, but no subsequent parts will ever be claimed.

### 8. Migration 22 Backfill Strategy

- Migration 22 adds `plan_version` and `cancel_requested_at` to `channel_deliveries`, and creates `channel_delivery_parts`.
- For existing v21 databases:
  - Every existing row in `channel_deliveries` is backfilled with an `ordinal = 0` Text Part using its turn's `reply_text`.
  - The part status, attempts, lease, provider message ID, and delivered timestamp mirror the existing delivery record.
  - The client ID is populated as `chatwaifu-{delivery_id_hex}-000`.
  - Existing delivered turns remain delivered and immediately visible as a completed 1-part plan.

### 9. Phase 17.1A Scope and Exclusions

This task establishes only the durable foundation:

- Included: `ChannelDeliveryPlan`, `ChannelDeliveryPart`, Migration 22, repository state machine, part-level lease & ACK, stable client IDs, crash recovery, sequential sending, tail cancellation.
- Excluded (deferred to Phase 17.1B / future phases): `BubbleSplitter`, prompt short-sentence constraints, `asyncio.sleep` typing cadence, typing indicators, image/sticker delivery, VLM/OCR, and Cloud Realtime.

## Consequences

- The delivery state machine is robust against crashes and network delays.
- Downstream features (BubbleSplitter, Cadence, Typing, Stickers) can plug directly into the plan factory and scheduling loop without altering the core persistence or state machine.
- Single-text delivery remains 100% backward compatible.
- All operations are atomic, auditable, and covered by automated regression tests.

## References

- [ADR 0029: External Channel Gateway](0029-external-channel-gateway.md)
- [ADR 0030: Native WeChat iLink Adapter](0030-native-weixin-ilink-adapter.md)
- [External Channels Architecture](../architecture/external-channels.md)
