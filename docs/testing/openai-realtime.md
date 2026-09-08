# OpenAI Realtime development acceptance

Phase 13.4B provides an opt-in server adapter. Keep the normal Nene desktop on Cascade while testing
an isolated Runtime with its own data/config directories. Do not launch a desktop from a worktree
that lacks the owner's private Live2D resources.

## Server configuration

The Runtime needs these explicit settings, provided through a private process environment or an
ignored local development profile. Do not commit a key or put one into a browser. A Codex/ChatGPT
subscription or an OpenAI-compatible text-model key does not configure this adapter.

```text
CHATWAIFU_REALTIME__CONNECTION_MODE=cloud_realtime
CHATWAIFU_REALTIME__CLOUD_BACKEND=openai
CHATWAIFU_REALTIME__OPENAI__MODEL=<accessible Realtime model ID>
CHATWAIFU_REALTIME__OPENAI__API_KEY=<server-side API credential>
CHATWAIFU_REALTIME__OPENAI__VOICE=marin
CHATWAIFU_REALTIME__OPENAI__TRANSCRIPTION_MODEL=gpt-4o-mini-transcribe
```

The model is required; the voice and transcription values shown are configurable defaults. The
endpoint is `wss://api.openai.com/v1/realtime`. Changing a text-model role in the settings UI does
not change this voice provider. The returned audio is the configured OpenAI voice, not cloned Nene
TTS. Public Runtime configuration omits the Realtime key entirely.

Cloud egress must be explicitly allowed for this test, or granted through the existing scoped
gateway grant. The default ungranted `ask` policy blocks connection. This slice does not add a new
consent UI. Use a separate data directory; do not change the owner's running Runtime configuration
or copy their conversation database for a provider smoke test.

## Automated acceptance

```sh
uv sync --frozen --all-packages
uv run --no-sync pytest services/runtime/tests/test_openai_realtime.py services/runtime/tests/test_openai_realtime_integration.py
uv run --no-sync pytest services/runtime/tests/test_cloud_realtime_lifecycle.py services/runtime/tests/test_cloud_realtime_identity_fence.py
```

The integration tests open real local WebSockets and an isolated SQLite database; they do not
contact OpenAI. They verify final transcripts, token usage normalization, exact generation binding,
duplicate suppression, 16-to-24 kHz input, output PCM, close/cancellation and durable egress policy.
They do not prove recognition quality, provider availability or physical audio playback.

## Public endpoint and listening pass

With an explicitly configured isolated Runtime, confirm effective session readiness, then speak a
short non-private phrase. Check both transcripts, returned audio, token usage and the Runtime turn's
terminal state. Repeat with interruption before the first response and during output. Disconnect
the network during a response and confirm local audio is flushed and the generation terminates.
Open a fresh voice connection to retry; this adapter does not reconnect or replay input itself.

Record the model/voice, Runtime commit, timestamps and observed outcomes without secrets or raw
audio in diagnostic logs. Treat recognition quality and the owner's listening confirmation as
separate from the deterministic tests. Automatic context recovery, native playback acknowledgment
and mode selection have their own remaining Phase 13 acceptance gates (ADR 0047).
