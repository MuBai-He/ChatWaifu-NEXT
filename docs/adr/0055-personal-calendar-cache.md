# ADR 0055: Owner-private calendar cache and atomic sync batches

Status: Accepted for the Stage A persistence boundary; account UI and OAuth remain pending.

## Context

Google remains the event authority. Concurrent refresh, calendar deselection and account
revocation must not allow a late network response to repopulate personal data. The existing
SQLite database serializes transactions and finishes commits before propagating cancellation.

## Decision

Migration 33 adds separate account, selected-calendar and event-cache tables. Accounts are
restricted to `local`; API/Skill integration must derive this scope from the authenticated
session, never accept it as a caller-selected privilege. OAuth tokens stay in the independent
secret store; only an opaque secret reference is stored in the account row.

Discovery never selects a calendar automatically. Each calendar has a revision and sync
cursor, each account a revision and revocation state. A sync ticket captures those versions
before network I/O. Applying a complete batch compares both revisions and the prior cursor,
then writes events and the next cursor in a single transaction. Deselect/reselect invalidates
the ticket and clears that calendar's cache. Full replacement occurs only with a complete
snapshot; cancelled recurring exceptions are retained as tombstones for later expansion.

Local account revocation is idempotent, increments its revision, and deletes calendar/cache
rows before secret cleanup. Revoked account IDs cannot be reused. Keep the secret reference
on the revoked row so later account-service cleanup can resume after a crash; the service
must finish cleanup without returning tokens to clients. Remote revocation success is a
separate outcome and is not established by the repository operation.

No HTTP or OAuth request runs while holding a database transaction. If cancellation arrives
during the existing shielded commit, the mutation can have committed despite CancelledError.
Callers reread durable state; retrying a stale ticket cannot apply the batch twice.

## Consequences and alternatives

This is a cache, not a second calendar or transcript store. The Runtime now owns
these classes under a default-disabled feature. Account service and retryable credential cleanup now exist, including session-derived
scope and asynchronous secret-file I/O that finishes before propagating cancellation. Startup
reconciliation requires a dedicated secret file; never prune a shared provider store. OAuth
coordination must validate state, PKCE, expiry and transport before calling connect_authorized.
Native callback and connection UI plus direct-TLS-only handoff are implemented; authorized cache
reads, recurrence expansion, calendar selection UI and actual native consent remain Stage A work. Partial-page commits were
rejected because advancing a cursor without all events silently loses changes. Deleting the
account row outright was rejected because it loses crash-recovery secret-cleanup references.

Validation: seven real-SQLite race/rollback/restart checks pass; existing database/migration
checks pass (14, one Windows-specific skip on macOS). This is not live Google acceptance.

Authorization handoff initially admits only the configured HTTPS origin directly at the Runtime,
with forwarded headers rejected; ordinary LAN chat remains unchanged. A trusted reverse-proxy
transport contract is deliberately deferred rather than inferred from a loopback peer. Mac system
browser authorization is supported in code; other desktop platforms return an unsupported error.
