# ADR 0006: Supervised model workers use a versioned process protocol

- Status: Accepted
- Date: 2026-08-23

## Context

Local models have different dependencies, memory footprints and crash behavior. Importing every
provider into the runtime process would weaken isolation and recovery.

## Decision

Future local models run in supervised worker processes. Lifecycle, health, capabilities, requests,
stream chunks, cancellation and normalized errors use a versioned protocol; provider objects stay
inside worker adapters.

## Consequences

Workers can fail and restart independently, at the cost of serialization and supervision logic.
No worker or model SDK is added during Phase 0/1.

## Alternatives

In-process model imports; one worker per vendor without a shared lifecycle contract.
