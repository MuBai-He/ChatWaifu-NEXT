"""Public model worker process protocol."""

from chatwaifu_model_worker.models import (
    WORKER_SCHEMA_VERSION,
    WORKER_STREAM_SCHEMA_VERSION,
    SttTranscriptionRequest,
    SttTranscriptionResult,
    SttWorkerCapabilities,
    TtsStreamCompleted,
    TtsStreamFailed,
    TtsStreamReady,
    TtsStreamStart,
    TtsSynthesisRequest,
    TtsSynthesisResult,
    TtsWorkerCapabilities,
    WorkerHealth,
)
from chatwaifu_model_worker.streaming import (
    TTS_PCM_FRAME_MAGIC,
    TTS_PCM_FRAME_MAX_PAYLOAD_BYTES,
    TTS_PCM_FRAME_VERSION,
    TtsPcmFrame,
    pack_tts_pcm_frame,
    unpack_tts_pcm_frame,
)

__all__ = [
    "TTS_PCM_FRAME_MAGIC",
    "TTS_PCM_FRAME_MAX_PAYLOAD_BYTES",
    "TTS_PCM_FRAME_VERSION",
    "WORKER_SCHEMA_VERSION",
    "WORKER_STREAM_SCHEMA_VERSION",
    "SttTranscriptionRequest",
    "SttTranscriptionResult",
    "SttWorkerCapabilities",
    "TtsPcmFrame",
    "TtsStreamCompleted",
    "TtsStreamFailed",
    "TtsStreamReady",
    "TtsStreamStart",
    "TtsSynthesisRequest",
    "TtsSynthesisResult",
    "TtsWorkerCapabilities",
    "WorkerHealth",
    "pack_tts_pcm_frame",
    "unpack_tts_pcm_frame",
]
