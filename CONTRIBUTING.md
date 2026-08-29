# Contributing

Read `CODEX_HANDOFF.md`, `docs/implementation-status.yaml`, relevant ADRs, and the
applicable `.agents/skills/` entry before changing code. Work on one accepted phase
or vertical slice at a time.

Run `make format`, `make lint`, `make typecheck`, `make test`, and the affected phase
gate. Generated protocol files must be refreshed with `make generate-protocol` and
must not be edited by hand.

Do not commit model weights, proprietary Live2D Core files, credentials, user media,
private character assets, or generated local data.

## CI ownership

- Python CI validates the workspace on Linux, macOS, and Windows. The neural TTS
  worker is an independent uv project and is checked from its own lockfile on the
  same platform matrix; model weights and optional inference SDKs are not CI inputs.
- Web CI owns formatting, lint, unit/type checks, and production builds for the
  protocol, avatar SDK, and Web packages. It does not recursively build Tauri.
- Rust CI installs the documented Tauri Linux prerequisites, runs the workspace
  checks on all three desktop platforms, and compiles the release desktop host
  without creating or signing an installer.
- Browser E2E uses only pnpm and the Fake avatar path. It must remain independent
  of uv, Runtime services, proprietary Cubism files, and local model assets.
- `tools/check_architecture_boundaries.py` is the focused static guard for the
  currently enforced boundaries: no heavy model SDK inside Runtime, repository
  ports in conversation and Runtime Skills, and no direct provider integration in
  Web. It does not replace domain tests or architecture review.
