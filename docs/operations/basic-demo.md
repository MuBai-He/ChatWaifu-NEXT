# Basic Demo operations

## Start and stop

Run `make demo`. The command prepares the repository-pinned pnpm under `.local/tooling/` when a
global pnpm is unavailable, validates Web dependencies, then starts Runtime first and waits for
`GET http://127.0.0.1:8765/v1/runtime/health`, then starts Web and waits for
`http://127.0.0.1:5173`. `Ctrl+C` terminates both process groups and Runtime drains active
generation cancellation before closing SQLite.

If either port is already occupied, stop the old process or run the two services separately with
`make dev-runtime` and `make dev-web`.

## Provider truth

The header and `runtime.status` Skill show the resolved provider names. `demo` means deterministic
Demo LLM, not a hidden real model. `macos_say` is the zero-download speech adapter. `fake` TTS is a
valid WAV test tone and must not be described as character-quality speech.

## Local data

- SQLite: `.local/data/chatwaifu.db`
- Generated speech: `.local/data/audio/*.wav`
- Configuration defaults: `config/default.toml`
- Environment example: `.env.example`

Deleting local data is deliberately not part of the startup command. Explicit “forget” commands
tombstone one matched memory without erasing provenance. The destructive `重置` button requires a
browser confirmation, cancels active generation, then clears the current session's turns/events,
all explicit memories including tombstones, and all generated WAV files. It keeps the same ready
session and WebSocket so the user can start again immediately.

## Smoke test

1. Confirm header says `Runtime online`.
2. Send `你好，请介绍一下自己` and observe incremental text plus local speech.
3. Send another message, click `打断`, and confirm avatar returns to `idle` without stale audio.
4. Send `请记住我喜欢蓝色`, reload or create another session, and ask what it remembers.
5. Send `请忘记我喜欢蓝色` and confirm the active memory card becomes empty.
6. Run `运行状态 Skill` and compare provider names with the header.
7. Accumulate enough messages to scroll; confirm the browser page and Live2D stay fixed while only
   the transcript moves.
8. Click `重置`, accept the confirmation, and confirm transcript and memory are empty and a new turn
   can be sent in the same session.
