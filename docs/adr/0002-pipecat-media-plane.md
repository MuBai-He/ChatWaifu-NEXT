# ADR 0002: Pipecat as the future media plane

- Status: Accepted
- Date: 2026-08-23

## Context

Realtime audio needs transport, frame processing, interruption and provider integration. Those
concerns should not become the owner of character, memory, skill or routing policy.

## Decision

Use Pipecat in Phase 5 as the realtime media plane behind ChatWaifu-owned ports and protocols.
Pipecat types must not leak into domain packages. This ADR does not authorize adding Pipecat in
Phase 0 or Phase 1.

## Consequences

Media plumbing can reuse a focused framework while product semantics remain portable. Framework
upgrades need cancellation and latency regression tests.

## Alternatives

Build the full media pipeline in-house; make Pipecat the application architecture.
