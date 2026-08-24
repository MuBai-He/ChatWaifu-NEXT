# Kokoro TTS worker

This worker isolates `sherpa-onnx` and Kokoro from the Runtime dependency graph. `make demo`
supervises it on a random loopback port with an ephemeral bearer token; the browser never calls it
directly.

Prepare the locked environment and verified public model cache:

```bash
make setup-tts-worker
```

The setup script downloads the official `kokoro-multi-lang-v1_1.tar.bz2` release, checks its pinned
SHA-256 before extraction, and stores it under the Git-ignored `.local/models/kokoro/` directory.
The model produces 24 kHz Chinese/English speech with multiple speaker embeddings. ChatWaifu's
default `speaker_id=3` is labeled only as a generic synthetic female Demo voice; it is not a clone
of the original character voice actor.

Sources and licenses:

- sherpa-onnx and packaged Kokoro instructions: Apache-2.0
- Kokoro 82M v1.1 Chinese model: Apache-2.0
- ChatWaifu character profile: configuration only; no original game voice sample, artwork, Live2D
  model, or copyrighted script is included

Every synthesis request and response carries request, session, turn, generation, and segment/job
identity. The Runtime rejects mismatches, and cancellation is propagated to the worker before stale
audio can be queued for playback.
