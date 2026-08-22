---
name: project-architecture
description: Preserve ChatWaifu NEXT domain boundaries when adding a subsystem, moving responsibilities, introducing infrastructure, changing shared contracts, or performing a large refactor. Do not use for isolated implementation details that leave architecture unchanged.
---

# Project Architecture

Keep the runtime decomposable into realtime, session/conversation, providers,
agent/character, memory, product runtime skills, policy, avatar, frontend,
persistence, and observability domains.

Prefer dependencies in this direction:

Frontend -> application protocol -> services -> domain interfaces -> adapters.

Provider SDK objects, SQLAlchemy models, Pipecat frames, and Live2D identifiers do
not cross their adapter boundaries. Pipecat may orchestrate media but is not the
application domain model. The agent produces semantic AvatarCue objects, never
renderer parameters.

Before adding a subsystem, state its responsibility, exclusions, inputs, outputs,
failure behavior, lifecycle, cancellation semantics, observability, tests, and
dependencies. Verify that no current domain already owns the responsibility.

Create an ADR under `docs/adr/` for decisions that are expensive to reverse. At
completion report responsibility and dependency changes, migration path, tests,
and remaining risks.
