---
id: local.echo
version: 1.0.0
name: Local Echo
---

# Local Echo

Use `echo` to verify the MCP plugin transport without side effects.

Use `append_note` only when the user explicitly wants to append a local test note. It
requires `plugin.notes.write` and per-invocation confirmation. Never represent this test
file as long-term character memory.

Use `wait` only for cancellation and timeout diagnostics.
