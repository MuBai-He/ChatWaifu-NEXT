# ADR 0014: Unified selectable local neural TTS

- Status: Accepted
- Date: 2026-08-25

## Context

The Kokoro demo path was small but did not meet the desired character-voice quality. The target
experience needs a trainable Chinese/Japanese character voice, while Qwen3-TTS and GPT-SoVITS use
incompatible Python stacks and expose different model-specific APIs. Loading both models inside
Runtime or exposing either native API to Web would couple conversation, playback, and configuration
to one engine and would make interruption cleanup unsafe.

## Decision

Runtime owns a `TtsRouter` whose providers implement one versioned `TtsProvider` contract. The Web
client discovers normalized provider metadata and makes a session-scoped selection through Runtime;
it never receives worker tokens, model paths, reference audio, or engine SDK objects.

Every isolated worker implements the same authenticated loopback surface:

- `GET /v1/health`
- `GET /v1/capabilities`
- `POST /v1/synthesize`
- `POST /v1/jobs/{generation_id}/cancel`
- `POST /v1/model/unload`

Synthesis requests carry request, session, turn, generation, and job identity plus text, language,
voice id, speaker id, speed, optional style, optional pitch, and output format. Results echo the full
identity and return a validated RIFF/WAVE asset with provider, model, sample rate, and duration.
Unsupported controls remain in the contract but are reported as unsupported in capability metadata.

`qwen3_tts_mlx` is the supervised Demo default. It runs the official Qwen3-TTS 0.6B Base checkpoint
through the third-party MLX-Audio adapter and uses its incremental decoder internally before the
current Runtime asset boundary receives one WAV. `gpt_sovits` is selectable and runs the local
CPUFast v2ProPlus environment. Both are lazy-loaded. When no session uses the previous provider,
Runtime calls its unload endpoint so the two heavy models do not remain resident together.

Voice weights, reference clips, transcripts, third-party repositories, and engine environments live
under ignored local paths. The committed configuration contains only provider-neutral defaults and
an example profile. The current local GPT-SoVITS Roxy model is evaluation data, not a Ningning model,
and is not a distributable product asset.

The current playback boundary remains complete generation-scoped WAV segments. True worker-to-
Runtime audio-chunk streaming is a compatible future extension; it must preserve active-generation
gating, bounded buffering, cancellation, and playback acknowledgements.

## Consequences

Qwen and GPT-SoVITS can be compared from one UI without restarting Runtime or changing conversation
code. Model-specific dependencies stay isolated, inactive models can release memory, and cancellation
continues to reject late audio. The first utterance after selection pays model-load latency. A local
profile is required before `make demo`, and the checked-out weights/reference audio determine voice
identity and legal usability.

## Alternatives

Keep Kokoro as the only managed worker; embed both engines in Runtime; run two native vendor APIs and
let React call them directly; load both models permanently; make GPT-SoVITS the only provider.
