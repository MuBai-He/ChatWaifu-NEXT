# ADR 0003: Versioned domain event and command envelopes

- Status: Accepted
- Date: 2026-08-23

## Context

UI, runtime and workers need one system language. Ad hoc JSON and string prefixes cannot provide
compatibility, idempotency or reliable tracing.

## Decision

Persistable facts use `EventEnvelope`; requested actions use a separate `CommandEnvelope`. Wire
types use lowercase dot namespaces, schemas carry major/minor versions, and high-value payloads are
strongly typed. Unknown major versions and unknown message types are rejected; optional additions
within a supported major are ignored.

## Consequences

Protocol changes are reviewable and cross-language contract tests become a release gate. Producers
must regenerate schemas and TypeScript types when contracts change.

## Alternatives

Independent JSON per boundary; RPC-only contracts; using class names as wire types.
