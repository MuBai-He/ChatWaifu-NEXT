# ADR 0010: SQLite WAL and FTS5 first

- Status: Accepted
- Date: 2026-08-23

## Context

The first product is local-first and single-user. Operating a network database would add deployment
and failure modes before concurrency requirements justify them.

## Decision

Future persistence starts with SQLite in WAL mode, foreign keys enabled, a documented busy timeout
and FTS5 for text retrieval. Repository ports isolate SQL. Business tables and migrations begin in
the runtime phase, not Phase 0/1.

## Consequences

Installation and backup remain simple. Write concurrency is deliberately bounded. Replacing SQLite
requires benchmarks and a new ADR.

## Alternatives

PostgreSQL from day one; Redis as durable state; embedded key-value storage.
