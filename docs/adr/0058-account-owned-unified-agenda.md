# ADR 0058: Account-owned unified agenda

Status: Implemented for review; provider consent and real writes pending acceptance.

## Decision

ChatWaifu presents events, tasks and local alarm rules in one time-ordered agenda, with
today, seven-day and thirty-day views. Events and tasks retain distinct domain semantics.
The user selects one writable calendar and one writable task list as the default write
destinations. These defaults are persisted and validated against the current selected
source and account or device capability on every write. Missing or revoked destinations
fail explicitly; writes never fall back to a local copy.

Google Calendar and Google Tasks remain separate provider adapters inside Runtime.
Apple Calendar and Reminders continue through the paired macOS EventKit device bridge;
ChatWaifu's own alarms remain in the existing durable scheduler. No new scheduler or
provider mirror is created. Provider records are not copied into a universal events
table. Multi-account views preserve provider, account, collection and item identity.
The same calendar exposed through Google and macOS is not silently deduplicated by
title or time; authoritative identity mapping requires a later explicit design.

Google writes require incremental Calendar events and Tasks OAuth scopes. An existing
read-only account can be upgraded in place only after its primary calendar identity
matches the newly authorized account. Selection and local account ID are preserved;
all other accounts remain unchanged. Scope absence is an explicit capability failure,
not a reason to revoke a working read-only account. Calendar writes use a caller
request ID as the Google event ID; edits and deletes require the provider ETag and
reject recurring or special events in this first slice. Google Tasks writes use the
provider's date-only due field; precise alarms stay in the ChatWaifu scheduler.

Writes use the existing owner-session, Runtime Skill permission and confirmation
boundaries. Apple operations retain their idempotent device receipt; Google task
writes are not automatically replayed after an ambiguous transport failure. The UI
asks the user to verify the source before another attempt. The agenda is a live
aggregation and reports partial source failures rather than calling stale data current.

## Deferred

External event changes do not yet reconcile associated ChatWaifu reminder rules.
The common missed/unhandled notification inbox, source identity links, cross-provider
write transactions, advanced recurring-event edits and live voice acceptance are
separate follow-up work. Desktop sleep cannot guarantee an alarm without an online
target device.
