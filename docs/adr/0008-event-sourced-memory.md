# ADR 0008: Memory as policy-governed event projections

- Status: Accepted
- Date: 2026-08-23

## Context

A transcript archive is not durable character memory. Memory needs provenance, correction,
deletion, privacy and deterministic reconstruction.

## Decision

Future memory writes begin as proposals, pass policy and deduplication, then emit committed,
superseded or tombstoned events. Read models are projections and may be rebuilt. Raw media frames
never enter the memory event stream.

## Consequences

Memory changes are auditable and deletion has explicit semantics. Retrieval and projection work is
larger than a simple chat-history table and is deferred to Phase 11.

## Alternatives

Store all transcripts as memories; mutable rows without provenance; vector database as source of truth.
