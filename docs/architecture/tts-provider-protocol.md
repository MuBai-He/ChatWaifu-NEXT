# Unified TTS provider boundary

```text
Web provider selector
  -> Runtime session command
  -> TtsRouter
       -> WorkerTtsProvider(qwen3_tts_mlx)
       -> WorkerTtsProvider(gpt_sovits)
  -> authenticated loopback worker protocol v1
  -> engine SDK adapter
  -> generation-scoped WAV asset
  -> Runtime event / WebRTC playback
```

## Ownership

Web owns presentation and a session-scoped provider choice. Runtime owns provider discovery,
selection, generation identity, synthesis destinations, cancellation propagation, inactive-model
unload, audio asset publication, and stale-output rejection. A worker owns exactly one heavy engine,
its model/reference paths, native caches, and SDK-specific cleanup.

The normalized request is `TtsSynthesisRequest`; the normalized result is `TtsSynthesisResult`.
`TtsWorkerCapabilities` is descriptive and must not be inferred from a display name. Provider
adapters validate every returned identity before writing a WAV into Runtime-owned storage.

Changing provider cancels the active generation before changing the route. The router tracks which
sessions use each provider and unloads an old model only when no session and no synthesis job still
uses it. Worker cancellation sets an engine-visible signal, cancels the request task, and never
allows its eventual native-thread result to become a Runtime asset.

## Current engine mappings

| Contract field      | Qwen3-TTS MLX                                     | GPT-SoVITS CPUFast                            |
| ------------------- | ------------------------------------------------- | --------------------------------------------- |
| Languages           | Chinese, Japanese, English                        | Chinese, Japanese, English                    |
| Voice identity      | Base: reference clone; CustomVoice: fixed speaker | weights + reference WAV + transcript          |
| Internal generation | MLX incremental decoder                           | CPU v2ProPlus pipeline                        |
| Runtime output      | mono 24 kHz WAV                                   | mono 32 kHz WAV                               |
| Speed capability    | reported unsupported                              | reported unsupported on this CPUFast branch   |
| Unload              | reset decoder, release model, clear MLX cache     | stop pipeline, release models, collect caches |

The interface already carries `style` and `pitch`, but these two configured adapters report them as
unsupported. A future provider may implement them without changing the conversation or Web API.
