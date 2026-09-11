# OpenAI Realtime development acceptance

The cloud Realtime path provides an opt-in server adapter. Keep the normal Nene desktop on Cascade while testing
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
The web client can open a fresh voice connection after a bounded retry. Confirm that it restores only
confirmed dialogue from the same Runtime session and does not replay interrupted microphone input.
The provider adapter itself does not reconnect or replay input.

Record the model/voice, Runtime commit, timestamps and observed outcomes without secrets or raw
audio in diagnostic logs. Treat recognition quality and the owner's listening confirmation as
separate from the deterministic tests. Fresh-connection context recovery and playback acknowledgment have deterministic coverage under
ADRs 0048 and 0049; their public endpoint and real listening acceptance remain separate gates.
Mode selection and automatic Cascade fallback remain outside this acceptance slice.

## Read-only cloud tools (Phase 13.5A)

Enable `CHATWAIFU_REALTIME__CLOUD_TOOLS_ENABLED=true` only in the isolated test Runtime.
It defaults to false, preserving the existing single-response streaming path. When enabled, a
text-only decision precedes the streamed audio response, adding a model round trip.

This slice exposes a bounded snapshot of trusted, short, interruptible, built-in read-only skills.
It does not expose write operations, external plugins, interactive confirmations, or background
jobs. The built-in `runtime.status` capability provides a small diagnostic acceptance target.

Tool results require their own `tool_result` egress authorization. An existing scoped grant that
only allows conversation context must not authorize results. With an isolated test configuration,
exercise both explicit result authorization and denial; do not broaden the owner's policy. Egress
receipts contain metadata and must be persisted before any result is written to the provider.

For a local automated pass, include the cloud tool tests and the skill admission cancellation
regression in addition to the adapter and lifecycle suites above:

```sh
uv run --no-sync pytest services/runtime/tests/test_cloud_realtime_tools.py services/runtime/tests/test_runtime_skill_lifecycle_faults.py
```

For a public listening pass with an explicitly configured isolated Runtime:

1. Ask for the Runtime's current status. Confirm the provider actually requested the advertised
   status tool; a plausible spoken answer alone is not evidence of execution.
2. Check that the durable skill run has the same session, turn, generation, and provider call ID.
   Confirm the egress receipt precedes the function result, and only the final spoken response
   reaches captions, playback, and confirmed conversation history.
3. Ask an ordinary conversational question. Confirm its final response streams and the internal
   decision text does not become a second spoken answer.
4. Interrupt a tool turn and repeat after a fresh connection. Confirm that the cancelled turn's
   result and audio do not reappear, and the new turn can complete normally.
5. Repeat with a grant that omits `tool_result`. Confirm a blocked receipt and no outbound tool
   result. Treat the expected policy block separately from provider or skill failure.

Use deterministic barrier tests for precise cancellation windows and duplicate provider events;
manual timing alone cannot establish these invariants. Record the deployed commit and observed
outcomes. Passing local loopback tests does not establish public provider or microphone acceptance.
