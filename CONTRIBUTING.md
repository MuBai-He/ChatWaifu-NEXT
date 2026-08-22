# Contributing

Read `CODEX_HANDOFF.md`, `docs/implementation-status.yaml`, relevant ADRs, and the
applicable `.agents/skills/` entry before changing code. Work on one accepted phase
or vertical slice at a time.

Run `make format`, `make lint`, `make typecheck`, `make test`, and the affected phase
gate. Generated protocol files must be refreshed with `make generate-protocol` and
must not be edited by hand.

Do not commit model weights, proprietary Live2D Core files, credentials, user media,
private character assets, or generated local data.
