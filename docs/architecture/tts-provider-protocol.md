# Unified TTS provider boundary

```text
Web provider selector
  -> Runtime session command
  -> TtsRouter
       -> WorkerTtsProvider(qwen3_tts_mlx)
       -> WorkerTtsProvider(gpt_sovits)
       -> AliyunQwenRealtimeTtsProvider
       -> AliyunCosyVoiceRealtimeTtsProvider
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

The Web presentation groups both Bailian adapters under one `阿里云百炼` entry. Its API setting
selects Qwen VC or CosyVoice and resolves that choice back to the concrete Runtime provider ID before
issuing the session command. This is presentation-only aggregation: capability discovery, model/voice
validation, cancellation, streaming, and diagnostics remain isolated per adapter.

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

| Contract field      | Qwen3-TTS MLX               | GPT-SoVITS CPUFast           | Bailian Qwen VC Realtime       | Bailian CosyVoice Realtime  |
| ------------------- | --------------------------- | ---------------------------- | ------------------------------ | --------------------------- |
| Languages           | Chinese, Japanese, English  | Chinese, Japanese, English   | Auto/Chinese/Japanese/English+ | auto/zh/ja/en+              |
| Voice identity      | reference or fixed speaker  | weights + reference prompt   | private Bailian voice ID       | private Bailian voice ID    |
| Emotion instruction | not exposed                 | not exposed                  | unsupported by VC model        | v3.5 Plus/Flash, v3 Flash   |
| Internal generation | incremental decoder         | CPU v2ProPlus pipeline       | realtime session deltas        | task WebSocket binary PCM   |
| Runtime delivery    | batch WAV adapted to PCM    | batch WAV adapted to PCM     | native PCM16 fragments         | native PCM16 fragments      |
| WAV fallback        | mono 24 kHz                 | mono 32 kHz                  | mono configured sample rate    | mono configured sample rate |
| Unload              | decoder/model/cache release | pipeline/model/cache release | close WebSocket                | close WebSocket             |

The interface carries `style` and `pitch`. Local Qwen, GPT-SoVITS, and Qwen VC currently report
`style` as unsupported. CosyVoice maps `style` to a bounded provider instruction, so Character Kernel
can vary a turn's delivery without exposing provider syntax to conversation code.
