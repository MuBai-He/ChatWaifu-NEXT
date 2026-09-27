# ADR 0057: Native Apple access and server-owned reminder delivery

Status: Implemented for review; real OS authorization and device acceptance pending.

## Decision

The Linux Runtime owns scheduling independently of Google OAuth configuration. Migration 34
stores paired devices, durable tasks, occurrences and short-lived Apple operation receipts.
The existing single-process SQLite transaction boundary serializes occurrence creation and
schedule advancement. No Celery, local Python server or account mirroring is introduced.

Owner-facing API and Skill calls derive the owner from the existing immutable session scope.
Devices receive a separate random capability at pairing; only its SHA-256 digest is stored on
the server. Device requests also require the existing Runtime bearer. Pairing and polling use
HTTPS by default. A direct local Runtime connection requires explicit operator opt-in
(`device_allow_direct_loopback=true`) and rejects proxy headers; never enable this on a proxied
Runtime. Loopback peer identity alone is not a transport trust contract.
The native client stores credentials with mode 0600, keyed by server origin.

Only the avatar-overlay executes outbound device polling. The settings window configures the
device and displays operation results. A dedicated native command validates the window and
selected source allowlist. Monotonic source revisions prevent a delayed poll from restoring
a deselected source; deselection also purges retained results. EventKit runs inside the macOS Tauri application through a small
Objective-C C ABI bridge compiled with the system SDK; this replaces the earlier plan's Swift
helper implementation choice while preserving the same EventKit and OS-permission boundary.
No subprocess, shell command, cloud Apple password or private iCloud API is used.

Each Apple command has an idempotency key and a five-minute validity window. The server leases
it only once. The native journal records an uncertain write before calling EventKit and stores
the eventual result for acknowledgement retry. A crash or lost result does not trigger another
write. The UI reports queued, executing, failed, expired or uncertain separately from success.
A successful save/readback means local EventKit completion, not verified iCloud propagation.
Native creation attaches a ChatWaifu operation URL to newly created items. Existing item edits
require lastModifiedDate comparison and cannot move an item out of the selected source.

Tasks support one-time, daily and weekday wall-clock schedules in an IANA timezone. DST gaps
advance through the gap; overlaps use the first occurrence once. Advancing a schedule uses the
original wall time, not a previous DST-shifted occurrence. Transactional unique(task_id,due)
prevents duplicate occurrences. One explicit target device prevents multi-device ringing.
Alarm lateness is bounded to two minutes, reminder lateness to one hour. Stale occurrences are
missed, never replayed on wake. Snooze creates one separate occurrence five minutes later.

Task cancellation/revocation removes active deliveries; late acknowledgements cannot resurrect
them. Native presentation IDs survive restart, preventing duplicate sounds/notifications after
UI reload. Network failure stops ongoing sound. The desktop card remains available to close or
retry snooze. System notifications are best effort and do not establish delivery acceptance.

## Scope and limits

Basic Apple event/reminder query/create/edit/complete/delete is supported. Recurring edits,
attendee events and all-day event edits are rejected. Advanced list features, series changes,
attachments, phone Clock integration, calendar conflict merging, and cloud voice writes remain
outside this slice. Read and write Skills are separate; writes retain the existing permission
and confirmation path. Cloud realtime's read-only restriction is unchanged.

Apple access requires a Mac. Other desktop platforms can pair for tasks/system notifications;
Linux native audio acceptance remains pending. Closing or sleeping the only target desktop
cannot produce a guaranteed alarm. This is a convenience reminder service, not an OS wake timer.
