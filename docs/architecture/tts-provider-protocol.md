# Unified TTS provider boundary

```text
Web provider selector
  -> Runtime session command
  -> TtsRouter
       -> WorkerTtsProvider(qwen3_tts_mlx)
       -> WorkerTtsProvider(gpt_sovits)
       -> AliyunQwenRealtimeTtsProvider
  -> authenticated loopback worker protocol v1
     or authenticated provider WebSocket
  -> provider-neutral ordered PCM16 stream
  -> ephemeral bounded fan-out + generation-scoped WAV fallback
  -> Web Audio / Runtime event / WebRTC fallback playback
```

## Ownership

Web owns presentation and a session-scoped provider choice. Runtime owns provider discovery,
selection, generation identity, synthesis destinations, cancellation propagation, inactive-model
unload, audio asset publication, and stale-output rejection. A worker owns exactly one heavy engine,
its model/reference paths, native caches, and SDK-specific cleanup.

The normalized request is `TtsSynthesisRequest`; the normalized result is `TtsSynthesisResult`.
`TtsWorkerCapabilities` is descriptive and must not be inferred from a display name. Provider
adapters validate every returned identity before writing a WAV into Runtime-owned storage. Cloud
voice-cloning adapters also validate the selected region, voice visibility, and exact voice-to-model
binding before opening their media WebSocket.

`TtsRouter.stream` is now the delivery boundary. A native provider yields PCM16 while generation is
still running. A batch provider is normalized into the same event sequence after its WAV completes.
The ephemeral stream is never persisted; the completed WAV and its metadata remain durable enough
for fallback and diagnostics. `native_streaming` means the provider can reduce first-audio latency,
not merely that its internal model uses an incremental decoder.

Changing provider cancels the active generation before changing the route. The router tracks which
sessions use each provider and unloads an old model only when no session and no synthesis job still
uses it. Worker cancellation sets an engine-visible signal, cancels the request task, and never
allows its eventual native-thread result to become a Runtime asset.

## Current engine mappings

| Contract field      | Qwen3-TTS MLX                  | GPT-SoVITS CPUFast             | Aliyun Bailian Qwen VC Realtime |
| ------------------- | ------------------------------ | ------------------------------ | ------------------------------- |
| Languages           | Chinese, Japanese, English     | Chinese, Japanese, English     | Auto/Chinese/Japanese/English+  |
| Voice identity      | reference or fixed speaker     | weights + reference prompt     | private Bailian voice ID        |
| Internal generation | incremental decoder            | CPU v2ProPlus pipeline         | provider WebSocket deltas       |
| Runtime delivery    | batch WAV adapted to PCM       | batch WAV adapted to PCM       | native PCM16 fragments          |
| WAV fallback        | mono 24 kHz                    | mono 32 kHz                    | mono configured sample rate     |
| Unload              | decoder/model/cache release    | pipeline/model/cache release   | close WebSocket                 |

The interface already carries `style` and `pitch`, but these two configured adapters report them as
unsupported. A future provider may implement them without changing the conversation or Web API.
