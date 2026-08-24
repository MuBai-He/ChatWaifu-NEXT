# ADR 0013: Permissioned stdio MCP plugin execution

- Status: Accepted
- Date: 2026-08-24

## Context

ADR 0007 requires Runtime Skills to have schemas, permissions, side-effect metadata, timeouts,
cancellation, and normalized errors, but it does not choose the first executable plugin boundary.
Running third-party Python in the Runtime process would expose provider secrets, database objects,
and process lifetime to untrusted code.

## Decision

Runtime Skill discovery reads validated `chatwaifu.yaml` metadata. `SKILL.md` instructions are
loaded only through explicit activation. Installed plugins have a versioned `plugin.json` and are
copied from an explicitly selected local directory into the Runtime data directory after bounded
regular-file and path validation; symlinks and path escapes are rejected.

The first adapter is MCP `2025-11-25` over stdio JSON-RPC. Each invocation starts a fresh Python
child process with `-I -B`, a dedicated plugin working directory, a cleaned environment, bounded
messages, and no provider credentials. Timeout and cancellation terminate the child process group.
Plugin code cannot receive database implementations; only its declared MCP tool input is passed.

The Permission Broker persists grants separately from per-invocation confirmations. Read-only
permissions may be granted permanently, ordinary writes at most for a session, and destructive,
external-communication, or device-control capabilities require confirmation every time. A
capability may require confirmation even when all permissions are already granted.

Runs, confirmation requests, grants, and tool calls are persisted in SQLite and emit audit events.
Uninstall moves plugin files to a local recovery directory before deleting registration state.

## Consequences

Builtin and MCP capabilities share one registry, permission policy, executor, error model, and run
state machine. Runtime restarts expire unfinished jobs deterministically. The current child-process
boundary is soft isolation, not an operating-system sandbox: filesystem and network denial are not
yet enforced by the OS. Only trusted local plugins should be installed until a packaged sandbox is
implemented.

The initial adapter permits Python entrypoints only. Additional executable types, network MCP
transports, secret handles, resource limits, signatures, updates, and plugin dependencies require
follow-up decisions and tests.

## Alternatives

Import plugins into the Runtime process; call MCP servers directly from the frontend or model;
reuse a long-lived untrusted process across users; treat permission grants as confirmation; delete
plugin files permanently during uninstall.
