# ADR 0001: Modular monolith first

- Status: Accepted
- Date: 2026-08-23

## Context

ChatWaifu NEXT has tightly coupled realtime lifecycle rules but many replaceable providers. Splitting
these rules across services before the boundaries are proven would make cancellation and tracing
harder to validate.

## Decision

Start as a modular monolith with explicit domain packages and adapter boundaries. Cross-domain and
cross-process messages use the shared protocol. Processes may be extracted only with a new ADR and
evidence that the boundary is stable.

## Consequences

Local development and end-to-end debugging stay simple. Module ownership must be enforced in code
review because deployment topology does not enforce it for us.

## Alternatives

Microservices from day one; a single unstructured application module.
