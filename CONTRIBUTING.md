# Contributing

Read `docs/architecture/master-architecture.md`, `docs/implementation-plan.md`, `docs/implementation-status.yaml`, relevant ADRs, and the
applicable `.agents/skills/` entry before changing code. Work on one accepted phase
or vertical slice at a time.

Run `make format`, `make lint`, `make typecheck`, `make test`, and the affected phase
gate. Generated protocol files must be refreshed with `make generate-protocol` and
must not be edited by hand.

User-facing installation and extension documentation lives in `docs-site/`. Run
`make dev-docs` while authoring and `make build-docs` before submitting changes.
Keep the root README focused on the verified shortest path; put platform-specific
walkthroughs, screenshots, and troubleshooting in the documentation site.
The source in this repository is canonical. `make publish-docs` intentionally
publishes only the audited static build to the separate public Pages repository;
run it only when a public documentation update is intended.

Do not commit model weights, proprietary Live2D Core files, credentials, user media,
private character assets, or generated local data.

## CI ownership

- Python CI validates the workspace on Linux, macOS, and Windows. The neural TTS
  worker is an independent uv project and is checked from its own lockfile on the
  same platform matrix; model weights and optional inference SDKs are not CI inputs.
- Web Product CI owns the `web` React graph only. It runs the Web package's
  formatting, lint, unit/type checks, build, and product-manifest isolation gate;
  it never invokes Tauri or Runtime builds.
- Desktop Product CI installs the documented Tauri prerequisites, builds the
  `desktop` React graph through Tauri, builds the Runtime component wheel used as
  installer input, and compiles the unsigned host on all three platforms. A wheel
  plus naked host is not a frozen sidecar or installable release.
- Browser E2E starts the Web and Desktop Vite profiles separately. Runtime-backed
  scenarios use deterministic providers and remain independent of proprietary
  Cubism files and local model assets.
- Ordinary product CI is path-filtered. Tags are not path-filtered: `web-v*` and
  `desktop-v*` always run their complete release gate from a commit reachable from
  `main`.
- `tools/check_architecture_boundaries.py` is the focused static guard for the
  currently enforced boundaries: no heavy model SDK inside Runtime, repository
  ports in conversation and Runtime Skills, and no direct provider integration in
  Web. It does not replace domain tests or architecture review.
