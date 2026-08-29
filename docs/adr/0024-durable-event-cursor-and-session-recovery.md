# ADR 0024: Browser sessions recover through a durable event cursor

- Status: Accepted
- Date: 2026-08-29

## Context

The Runtime EventHub is intentionally bounded and live-only. The Web client previously fetched a
message snapshot and only then opened its WebSocket. A generation committed during that gap, a
short disconnect, or a full subscriber queue could permanently hide `generation_started`, text,
or completion events from the client. Reconnecting to another live-only socket did not repair the
state.

## Decision

- Every session WebSocket subscription accepts `after_sequence`. The server subscribes to the live
  Hub before reading the durable event stream, replays persisted events in pages, and de-duplicates
  queued live copies by sequence.
- A sequence gap observed after connection causes another durable replay before the live event is
  delivered.
- Session bootstrap obtains committed messages and a replay cursor from one SQLite snapshot. Idle
  sessions start at the latest sequence; an active generation starts immediately before its
  `assistant.generation_started` event so its transient text/playback state can be rebuilt.
- The browser retains its last delivered sequence across reconnects, serializes event handling,
  de-duplicates repeats, and independently fills a detected gap through the HTTP event endpoint.
- Reset deletes only reset-owned history, keeps `sessions.next_sequence` monotonic, and appends a
  durable `session.data_reset` event in the same SQLite transaction. Every connected client consumes
  that next sequence and clears its projection without restarting its subscription or reusing a
  cursor; provenance events retained for another memory scope therefore cannot collide with new
  events.

## Consequences

The in-memory Hub remains fast and bounded without becoming a second source of truth. Startup,
reconnect, and queue overflow all converge on the same persisted event stream. The extra recovery
read is local SQLite work, and events remain ordered and idempotent at the browser boundary.
