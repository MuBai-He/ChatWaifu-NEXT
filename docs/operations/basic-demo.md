# Basic Demo operations

## Start and stop

Run `make demo`. The command synchronizes the Python workspace, prepares the repository-pinned pnpm
under `.local/tooling/`, and validates Web dependencies. It then creates an ephemeral loopback token,
starts the isolated faster-whisper worker on a free loopback port, waits for the authenticated worker
health response, starts Runtime, and finally starts Web at `http://127.0.0.1:5173`. `Ctrl+C`
terminates all three process groups and Runtime drains active generation cancellation before closing
SQLite. The first run downloads the public multilingual `base` model (about 150 MB); later runs reuse
`.local/models/faster-whisper/`.

If either port is already occupied, stop the old process or run the two services separately with
`make dev-runtime` and `make dev-web`.

## Provider truth

The header and `runtime.status` Skill show the resolved LLM, STT, and TTS provider names plus the
Pipecat SmallWebRTC transport. `demo` means deterministic Demo LLM, not a hidden real model.
`faster_whisper_worker` means microphone PCM stays on loopback and inference runs in the isolated
worker. `macos_say` is the zero-download speech adapter. `fake` TTS is a valid WAV test tone and must
not be described as character-quality speech.

## Local data

- SQLite: `.local/data/chatwaifu.db`
- Generated speech: `.local/data/audio/*.wav`
- Local STT model cache: `.local/models/faster-whisper/`
- Configuration defaults: `config/default.toml`
- Environment example: `.env.example`

Deleting local data is deliberately not part of the startup command. Explicit “forget” commands
tombstone one matched memory without erasing provenance. The destructive `重置` button requires a
browser confirmation, cancels active generation, then clears the current session's turns/events,
all explicit memories including tombstones, and all generated WAV files. It keeps the same ready
session and WebSocket so the user can start again immediately.

## Smoke test

1. Confirm header says `Runtime online`.
2. Confirm the header reports `STT · faster_whisper_worker`.
3. Click `开启语音`, allow microphone access, hold `按住说话`, speak one sentence, then release it.
   Confirm the UI moves through listening, transcription, thinking, then shows a final user message
   and remote TTS. Confirm nearby speech does not move the meter or start a turn while the button is
   not held.
4. Hold `按住说话` and speak while the character is responding; confirm old audio stops and only the
   new reply continues. The explicit `打断` button must produce the same no-stale-output result.
5. Send `请记住我喜欢蓝色`, reload or create another session, and ask what it remembers.
6. Send `请忘记我喜欢蓝色` and confirm the active memory card becomes empty.
7. Run `运行状态 Skill` and compare provider names with the header.
8. Accumulate enough messages to scroll; confirm the browser page and Live2D stay fixed while only
   the transcript moves.
9. Click `重置`, accept the confirmation, and confirm transcript and memory are empty and a new turn
   can be sent in the same session.

`按住说话` is the safe default. `自由对话（会听到附近人声）` keeps the outbound track enabled and
is intended for quiet, single-user environments. Silero VAD detects speech boundaries, not whether
speech is addressed to the character, so open-mic behavior must not be described as addressee-aware.

`make dev-runtime` intentionally keeps STT disabled unless a separately managed authenticated worker
and `CHATWAIFU_STT__*` environment values are supplied. Use `make demo` for the supported voice path.
