# ADR 0019: Atomic checksummed SQLite migrations

- Status: Accepted
- Date: 2026-08-29

## Context

SQLite `executescript()` may leave earlier DDL applied when a later statement fails unless the
transaction is part of the same script. Recording only a migration version also cannot detect an
edited historical script or a database created by a newer Runtime.

## Decision

Each migration script, its SHA-256 checksum, and its ledger insert execute inside one explicit
`BEGIN IMMEDIATE ... COMMIT` script. Failure always rolls back before startup returns. Applied
scripts are immutable: a checksum mismatch or an unknown newer version fails closed. Existing
version-only ledgers are upgraded once and checksummed against the first Runtime that understands
the new ledger.

## Consequences

Interrupted migrations can be retried without partial schema state. Historical migrations must
never be edited; corrections require a new version. A legacy ledger can only be baselined, not
cryptographically prove which old script originally created it.
