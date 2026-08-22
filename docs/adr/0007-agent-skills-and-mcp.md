# ADR 0007: Separate development skills from runtime skills

- Status: Accepted
- Date: 2026-08-23

## Context

Codex instructions used to build the repository and product capabilities invoked by a character
have different trust, lifecycle and permission requirements.

## Decision

Codex Development Skills live in `.agents/skills/`. Product Runtime Skills live in `skills/` and
must declare input/output schemas, permissions, side effects, timeouts, cancellation and normalized
errors. MCP is an adapter option, not the domain model.

## Consequences

Tooling guidance cannot accidentally become an end-user capability. Runtime skills require an
explicit security surface before activation.

## Alternatives

One shared skills directory; direct model-to-MCP calls without a runtime policy layer.
