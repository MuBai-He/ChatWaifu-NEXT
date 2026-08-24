"""Public model worker process protocol."""

from chatwaifu_model_worker.models import (
    WORKER_SCHEMA_VERSION,
    SttTranscriptionRequest,
    SttTranscriptionResult,
    TtsSynthesisRequest,
    TtsSynthesisResult,
    WorkerHealth,
)

__all__ = [
    "WORKER_SCHEMA_VERSION",
    "SttTranscriptionRequest",
    "SttTranscriptionResult",
    "TtsSynthesisRequest",
    "TtsSynthesisResult",
    "WorkerHealth",
]
