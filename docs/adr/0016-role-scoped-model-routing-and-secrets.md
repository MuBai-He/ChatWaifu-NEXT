# ADR 0016: Runtime-persisted role-scoped model routing and secrets

- Status: Accepted
- Date: 2026-08-26

## Context

The Demo previously selected its LLM from TOML and `.env` at process startup. Character chat,
memory extraction, conversation summarization, and embeddings have different latency, privacy,
quality, and cost requirements. Coupling them to one provider prevents local/cloud mixes and makes
model experimentation require restarts and file editing. Storing provider keys in Web state,
localStorage, SQLite rows, logs, or Git would violate the local secret boundary.

## Decision

Runtime persists four independent model roles in SQLite: `chat`, `memory_extraction`,
`memory_summary`, and `embedding`. Each record owns provider kind, model id, base URL, timeout,
context window, enabled state, and update time. The Web settings panel reads and updates these rows
through loopback Runtime APIs and can probe each route separately. Provider construction remains
inside Runtime adapters; frontend code never calls a model endpoint.

API keys are write-only. Runtime stores them by role in `.local/config/model-secrets.json`, creates
the file with mode `0600`, and returns only `api_key_configured`. The browser does not persist or
receive a saved key. Existing `.env` chat configuration is imported once as a compatibility
bootstrap when no chat secret exists; after that, Runtime database and secret storage are the
product configuration source.

OpenAI-compatible chat-completions and embeddings are the first network adapters. Deterministic
Demo extraction/summary and a rebuildable 64-dimensional local hash projection keep the offline
Demo usable. SQLite WAL + FTS5 records remain memory truth; `memory_embeddings` is a disposable
projection keyed by an embedding fingerprint and is rebuilt when the embedding route changes.

## Consequences

Changing the memory extractor or embedding model no longer changes chat behavior, and all four roles
can be configured without restarting or editing `.env`. A bad auxiliary provider degrades to the
deterministic/FTS path instead of corrupting durable records. The local hash fallback is not a
replacement for a multilingual semantic model; it exists to exercise the projection and ranking
path without downloading weights.

The first API assumes a trusted user on the loopback Runtime. Packaged multi-user or remote control
requires authentication, secret handles/keychain integration, egress confirmation, and SSRF policy.

## Alternatives

Use one global LLM setting; expose provider SDKs or keys to React; save keys in localStorage or the
model-role table; make a hosted memory service the source of truth; require `.env` edits and Runtime
restarts for every model change.
