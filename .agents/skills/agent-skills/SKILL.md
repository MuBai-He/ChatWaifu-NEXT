---
name: agent-skills
description: Build or maintain ChatWaifu product Runtime Skills, plugin manifests, capability discovery, schemas, registration, execution, permissions, confirmations, timeouts, normalized errors, MCP/native adapters, and plugin isolation. This is not for Codex development skills.
---

# Agent Skills

This skill concerns the product capability system under `skills/`, not Codex
Development Skills under `.agents/skills/`.

Use Agent -> Skill Router -> Registry -> Permission Broker -> Executor -> Adapter.
Do not route with a growing name-based if/else chain. Each capability has a stable
identifier, version, description, input/output schema, side-effect classification,
required permissions, confirmation rule, timeout, and normalized error contract.

Keep installation, granted permission, and per-invocation confirmation separate.
Propagate parent cancellation into execution. Treat third-party packages as
untrusted and keep an isolation boundary even before a full sandbox exists. Expose
only relevant permitted capabilities to the model; do not load every skill schema.

Test manifest/schema validation, duplicates and incompatible versions, disabled or
missing dependencies, valid/invalid execution, permission denial, confirmation,
timeout, cancellation, and external-service failures.
