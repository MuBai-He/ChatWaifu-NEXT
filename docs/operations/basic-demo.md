# Basic Demo operations

## Start and stop

Run `make demo`. The command synchronizes the Python workspace, prepares the repository-pinned pnpm
under `.local/tooling/`, and validates Web dependencies. It then creates ephemeral loopback tokens,
starts the isolated faster-whisper, Qwen3-TTS MLX, and GPT-SoVITS workers on free loopback ports,
waits for their authenticated health responses, starts Runtime, and finally starts Web at
`http://127.0.0.1:5173`. `Ctrl+C` terminates every supervised process group and Runtime drains active
generation cancellation before closing SQLite. The first STT run downloads the public multilingual
`base` model (about 150 MB); later runs reuse `.local/models/faster-whisper/`. Neural TTS uses the
local-only profile described in `docs/operations/neural-tts.md`.

If either port is already occupied, stop the old process or run the two services separately with
`make dev-runtime` and `make dev-web`.

## Provider truth

The header and `runtime.status` Skill show the resolved LLM, STT, and TTS provider names plus the
Pipecat SmallWebRTC transport. `demo` means deterministic Demo LLM, not a hidden real model.
`faster_whisper_worker` means microphone PCM stays on loopback and inference runs in the isolated
worker. `qwen3_tts_mlx` is the neural TTS default and `gpt_sovits` is selectable under `输出声音`.
`macos_say` remains a zero-download adapter and `fake` is a valid WAV test tone; neither may be
described as character-quality voice cloning.

Open `CONFIG / 设置` to manage four independent model routes: chat, memory extraction, memory
summary, and embedding. Each card can use an offline fallback or an OpenAI-compatible endpoint and
has its own model id, base URL, context window, timeout, API key, save action, and connectivity test.
Saved keys are write-only and remain in `.local/config/model-secrets.json` with mode `0600`; Web
never receives them. `.env` chat fields are a legacy first-run import only.

## Local data

- SQLite: `.local/data/chatwaifu.db`
- Generated speech: `.local/data/audio/*.wav`
- Installed plugins: `.local/data/plugins/`
- Recoverable plugin removals: `.local/data/plugin-trash/`
- Local STT model cache: `.local/models/faster-whisper/`
- Local TTS profile: `.local/config/tts-profiles.toml`
- Local write-only model secrets: `.local/config/model-secrets.json`
- Local Qwen/GPT-SoVITS environments, vendors, and model caches: `.local/`
- Configuration defaults: `config/default.toml`
- Environment example: `.env.example`

Deleting local data is deliberately not part of the startup command. The memory center can review
implicit proposals, inspect event provenance, correct or pin accepted records, and tombstone one
record without erasing its audit history. The destructive `重置` button requires a browser confirmation,
cancels active generation, then clears the current session's turns/events, all structured memories
including proposals and tombstones, and all generated WAV files. It keeps the same ready
session and WebSocket so the user can start again immediately.

## Smoke test

1. Confirm header says `Runtime online`.
2. Confirm the header reports `STT · faster_whisper_worker`.
3. Confirm `输出声音` defaults to Qwen3-TTS, send one short turn, switch to GPT-SoVITS, and send a
   second turn. Confirm both play and the header follows the session selection.
4. Click `开启语音`, allow microphone access, hold `按住说话`, speak one sentence, then release it.
   Confirm the UI moves through listening, transcription, thinking, then shows a final user message
   and remote TTS. Confirm nearby speech does not move the meter or start a turn while the button is
   not held.
5. Hold `按住说话` and speak while the character is responding; confirm old audio stops and only the
   new reply continues. The explicit `打断` button must produce the same no-stale-output result.
6. While voice remains connected, unplug or disable the selected microphone. Confirm the selector
   falls back to another input and the state passes through `重连中` to `已连接`. Restart Runtime to
   exercise the same bounded WebRTC recovery path; explicit `断开麦克风` must not reconnect.
7. Let one short reply finish, then inspect its Runtime playback status. Confirm `spoken_text`
   contains the completed sentence. Interrupt a second reply midway and confirm its unfinished
   sentence is absent rather than being inferred from synthesis or enqueue completion.
8. Send `我喜欢蓝色`, open `记忆中心`, inspect the pending preference and its rationale, then accept it.
9. View its source event, pin it, reload or create another session, and ask `你记得我的喜好吗？`.
10. Correct the accepted record to `我喜欢紫色`; confirm the old record is superseded and only the new
    active record is recalled. Then forget the test record and confirm it is tombstoned.
11. Open `Skills & 插件`, run `runtime.status.read`, and compare provider names with the header.
12. Install the Local Echo example, run `echo`, then invoke `append_note`. Confirm it waits for an
    explicit decision and that `拒绝` produces a failed run without writing the note.
13. Invoke `wait` with a long duration and cancel it; confirm the terminal state is `cancelled`.
14. Open LOG / 历史 after accumulating enough messages; confirm its backlog scrolls while the
    visual-novel stage, Live2D character, and current dialogue remain fixed. Open CONFIG / 设置,
    switch between upper-body and full-body framing, and confirm both remain inside the viewport.
15. In CONFIG, confirm four model cards and the Character Kernel state are visible. Save and test one
    auxiliary model; reload and confirm it persists without changing the chat card.
16. Click `重置`, accept the confirmation, and confirm transcript, memory, Affect, and Relationship
    state return to their initial values and a new turn
    can be sent in the same session.

Plugin uninstall is recoverable and moves files into `.local/data/plugin-trash/`. This is a soft
child-process boundary, not an OS sandbox; only install trusted local plugins in the current Demo.

`按住说话` is the safe default. `自由对话（会听到附近人声）` keeps the outbound track enabled and
is intended for quiet, single-user environments. Silero VAD detects speech boundaries, not whether
speech is addressed to the character, so open-mic behavior must not be described as addressee-aware.

`make dev-runtime` intentionally keeps STT disabled unless a separately managed authenticated worker
and `CHATWAIFU_STT__*` environment values are supplied. Use `make demo` for the supported voice path.
