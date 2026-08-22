# Phase 0 and Phase 1 boundary

This repository revision establishes tooling and the shared protocol language only.

## Implemented

- reproducible Python, TypeScript and Rust workspaces
- Pydantic protocol source and a major-version-aware registry
- deterministic JSON Schema and generated TypeScript declarations
- Zod validation at TypeScript runtime boundaries
- cross-language golden fixtures and contract tests
- a React status page used as a build/test smoke target

## Not implemented

There is no runtime server, event hub, event store, database schema, microphone path, WebRTC,
Pipecat integration, Tauri sidecar, Live2D runtime or model adapter. Directories for later phases are
ownership placeholders, not claims of working modules.

## Generation flow

```text
Pydantic source
  -> schemas/domain/v1/*.schema.json
  -> packages/protocol-typescript/src/generated/domain.ts
  -> Python and TypeScript contract tests
```

Generated files are deterministic and checked for drift in CI. Runtime parsers intentionally remain
hand-reviewed Zod schemas so untrusted JSON is not accepted merely because a compile-time type exists.
