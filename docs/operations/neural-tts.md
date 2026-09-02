# Local Qwen3-TTS and GPT-SoVITS

## Local-only profile

Copy `config/tts-profiles.example.toml` to `.local/config/tts-profiles.toml`, then set the local
environment, vendor, model, weight, reference-audio, exact transcript, and transcript-language
paths. `.local/` is ignored by Git. Do not put character weights, reference audio, bearer tokens, or
machine-specific paths in committed TOML, character manifests, or frontend environment files.

For a converted Qwen CustomVoice checkpoint, set `qwen_voice` to the exact speaker name stored in
its `talker_config.spk_id`. The Worker then calls the fixed CustomVoice speaker and does not send the
reference WAV or transcript. Base checkpoints omit `qwen_voice` and continue using reference cloning.

This checkout can point its ignored local profile at the converted Nene Qwen checkpoint and the
supplied GPT-SoVITS model. Neither local voice is a distributable product asset.

Run `make setup-neural-tts-workers` to install the lightweight ChatWaifu worker shim into both
pre-existing engine environments. This does not copy either engine into Runtime. `make demo` runs
the setup check automatically, creates fresh loopback tokens and free ports, and starts both worker
servers with lazy model loading.

## Selection and memory use

The page defaults to `Qwen3-TTS · MLX`. Select `GPT-SoVITS` under `输出声音`; an in-progress reply is
cancelled before the route changes. The selected engine loads on its next utterance. If no other
session uses the old engine, it is unloaded before the switch completes.

On the validated Apple Silicon host, Qwen's first streamed synthesis reached about 4.5 GB MLX peak
allocation and about 6.5 GB process peak footprint; the GPT-SoVITS CPUFast validation reached about
3.5 GB RSS. These are local measurements, not portable guarantees. Lazy load plus inactive-provider
unload is therefore part of the correctness path rather than a cosmetic optimization.

## Expected diagnostics

- `GET /v1/tts/providers?session_id=...` reports selection, endpoint status, model-loaded state,
  device, language list, and normalized capabilities.
- Qwen emits 24 kHz WAV assets; the validated GPT-SoVITS v2ProPlus path emits 32 kHz WAV assets.
- A silent GPT-SoVITS fallback is rejected as an error instead of being queued for playback.
- Qwen cancellation closes the generator and explicitly resets the MLX speech-decoder stream state.
- Switching or closing the last session calls `/v1/model/unload` on the old worker.

Worker Protocol v2 lets the validated Qwen MLX and GPT-SoVITS paths stream ordered,
generation-scoped PCM16 over an authenticated WebSocket. Runtime applies bounded fan-out,
cancellation and playback acknowledgements, while still retaining a complete WAV as reconnect and
history fallback. The Windows Qwen3-TTS Torch/CUDA pack currently reports
`native_streaming=false`: its official wrapper generates the complete waveform before Runtime can
deliver it, so that backend does not yet provide true first-chunk latency.
