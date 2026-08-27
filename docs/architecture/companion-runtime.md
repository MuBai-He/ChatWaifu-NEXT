# Companion Runtime policies

## Ownership

The companion slice adds attention, resource-lifecycle, and proactive-behavior policies without
moving character logic into Tauri or React:

- Rust owns the local service process group, dynamic bootstrap handshake, crash recovery, and OS
  windows only.
- Runtime owns wake-phrase attention, quiet hours, frequency limits, model-idle policy, Character
  Kernel planning, persistence, and audit events.
- React exposes settings and projects Runtime state. It never starts workers or calls model providers.

Companion settings are stored in the Runtime SQLite database. OS/window preferences remain in the
Tauri application configuration file. API keys and model SDK objects do not cross either boundary.

## Attention boundary

Push-to-talk is always an intentional address. Open microphone mode requires a configured wake
phrase at the beginning of the final transcript by default. VAD still detects only speech
boundaries; it does not decide who was addressed.

With the wake gate enabled, open-microphone speech does not interrupt current character playback at
VAD start. The Runtime first completes local STT, accepts and strips a leading address such as
`宁宁，`, and only then interrupts the active generation. Nearby speech without that address emits
`voice.utterance_ignored` and is discarded without creating a user turn. Disabling the gate is an
explicit setting for quiet single-user rooms.

## Resource lifecycle

The Runtime tracks user/session activity independently from process health. After the configured
idle threshold it asks idle TTS and ASR adapters to release model weights. It never cancels an active
generation or in-flight synthesis to satisfy the idle policy. The worker process, Runtime, session,
memory, and Live2D surface stay alive; the next accepted interaction lazily reloads the model.

Resource state is visible through `/v1/companion/status`, and the settings surface offers explicit
sleep and wake actions. A busy resource returns a conflict rather than pretending it slept.

## Proactive behavior

Proactive speech is disabled by default. The first implemented event source is an idle check-in. A
turn is eligible only when all of these are true:

- proactive behavior is enabled;
- the session has crossed its idle threshold;
- local time is outside the configured quiet period;
- no generation is active;
- the global cooldown has elapsed;
- the daily interruption budget remains.

Every trigger or meaningful deferral is recorded in `ambient_actions`. Repeated deferrals with the
same reason are debounced. A triggered ambient turn is stored as an internal system turn, so the
LLM receives the event context without a fabricated user message appearing in history. Character
Kernel supplies a short, relationship-bounded response plan; normal generation identity,
cancellation, semantic avatar cues, TTS, playback ACK, and spoken-memory rules still apply.

The scheduler uses a cancellable event/timer loop and stops before model resources during Runtime
shutdown. Manual preview uses the same generation path but is an explicit user action and therefore
does not require the background feature to be enabled.

## Current limits

- The shipped proactive source is idle check-in only. Prospective memory, completed Skills, calendar,
  and scene events remain future sources.
- Ignore-based adaptive frequency and silent-card delivery are not implemented yet.
- Wake attention is transcript-based after local STT, not a low-power acoustic keyword engine or
  enrolled-speaker verifier.
- Settings and policy work on shared Web/desktop Runtime paths; signed cross-platform installers are
  still separate release work.
