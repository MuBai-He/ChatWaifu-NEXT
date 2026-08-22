# ADR 0012: Tiered local TTS providers

- Status: Accepted
- Date: 2026-08-23

## Context

GPT-SoVITS provides strong voice cloning but its training/inference environment, supporting models,
and operational surface are too heavy for the default basic demo. The product still needs Chinese
and English speech, character voice selection, local operation, cancellation, and an upgrade path to
zero-shot voice cloning.

## Decision

Use a tiered `TtsProvider` interface. Provider objects and model-specific settings remain inside
adapters.

1. `fake`: deterministic CI and cancellation tests; never presented as real speech quality.
2. `sherpa_onnx_kokoro`: default local demo provider using the external
   `kokoro-multi-lang-v1_1` or quantized model bundle. It supports Chinese and English, 103 speaker
   embeddings, and 24 kHz output without a PyTorch runtime.
3. `cosyvoice_http`: optional higher-quality, zero-shot/cross-lingual voice-cloning worker using
   Fun-CosyVoice 3 0.5B. It runs out of process and is not installed by the default bootstrap.
4. `gpt_sovits_http`: compatibility adapter may be added for an existing user-managed server, but
   GPT-SoVITS is not a default dependency or managed worker.

The Runtime owns synthesis requests, cancellation, segmentation, cache lifetime, and normalized
errors. The Web client only receives generation-scoped audio descriptors and playback commands.
Model files live under ignored local data directories and every voice manifest records provider,
model/license identifier, speaker/reference input, language, and sample rate.

## Consequences

The basic demo has a reasonably small offline voice path and can later upgrade voice similarity
without changing conversation or frontend contracts. Kokoro speaker selection is not voice cloning;
users who require cloning select CosyVoice or an externally managed GPT-SoVITS adapter. Real quality
and latency claims require a benchmark on the target machine and chosen voice.

## Alternatives

GPT-SoVITS as a mandatory dependency; browser `speechSynthesis`; a cloud-only TTS API; exposing
provider HTTP APIs directly to React.

## Decision evidence

- Kokoro official model/library: <https://github.com/hexgrad/kokoro>
- Kokoro v1.1 Chinese model card: <https://huggingface.co/hexgrad/Kokoro-82M-v1.1-zh>
- sherpa-onnx Kokoro v1.1 package and speaker catalog:
  <https://k2-fsa.github.io/sherpa/onnx/tts/pretrained_models/kokoro.html>
- CosyVoice official repository: <https://github.com/QwenAudio/CosyVoice>
- GPT-SoVITS official repository: <https://github.com/RVC-Boss/GPT-SoVITS>
