# Unified neural TTS worker

This process owns one heavy neural TTS engine and exposes the same authenticated
loopback API regardless of whether the backend is Qwen3-TTS through MLX-Audio or
GPT-SoVITS. Runtime code depends only on `chatwaifu-model-worker-sdk`; model SDK
objects and weight paths do not cross this boundary.

Endpoints:

- `GET /v1/health`
- `GET /v1/capabilities`
- `POST /v1/synthesize`
- `POST /v1/jobs/{generation_id}/cancel`
- `POST /v1/model/unload`

All endpoints require the ephemeral bearer token supplied by the demo
supervisor. Model and reference-audio paths are local configuration and must not
be committed.
