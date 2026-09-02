"""Configuration shared by isolated Qwen and GPT-SoVITS worker processes."""

from pathlib import Path
from typing import Literal

from pydantic import Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class WorkerSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="CHATWAIFU_NEURAL_TTS_WORKER_",
        extra="ignore",
        frozen=True,
    )

    host: str = "127.0.0.1"
    port: int = Field(ge=1, le=65_535)
    token: SecretStr
    backend: Literal["qwen3_tts_mlx", "qwen3_tts_torch", "gpt_sovits"]
    provider_id: str = Field(pattern=r"^[a-z0-9][a-z0-9_.-]{1,127}$")
    display_name: str
    worker_id: str
    model: str
    vendor_dir: Path | None = None
    model_dir: Path | None = None
    qwen_voice: str | None = Field(default=None, min_length=1, max_length=128)
    qwen_attn_implementation: Literal["sdpa", "flash_attention_2", "eager"] = "sdpa"
    qwen_dtype: Literal["auto", "bfloat16", "float16", "float32"] = "auto"
    gpt_weights: Path | None = None
    sovits_weights: Path | None = None
    reference_audio: Path | None = None
    reference_text: str | None = Field(default=None, min_length=1, max_length=2_000)
    reference_language: Literal["zh", "ja", "en"] = "ja"
    device: str = "mps"
    preload: bool = False
    streaming_interval: float = Field(default=0.5, ge=0.08, le=4.0)
    stream_queue_size: int = Field(default=8, ge=1, le=64)
    max_stream_audio_bytes: int = Field(default=64_000_000, ge=1_000_000, le=256_000_000)
    temperature: float = Field(default=0.7, ge=0.1, le=2.0)

    @model_validator(mode="after")
    def validate_backend_paths(self) -> "WorkerSettings":
        if self.backend in {"qwen3_tts_mlx", "qwen3_tts_torch"}:
            if self.model_dir is None:
                raise ValueError("Qwen3-TTS requires model_dir")
            if self.backend == "qwen3_tts_mlx" and self.vendor_dir is None:
                raise ValueError("Qwen MLX requires vendor_dir")
            if self.qwen_voice is None and (
                self.reference_audio is None or self.reference_text is None
            ):
                raise ValueError(
                    "Qwen Base voice cloning requires reference_audio and reference_text"
                )
        if self.backend == "gpt_sovits" and (
            self.vendor_dir is None
            or self.gpt_weights is None
            or self.sovits_weights is None
            or self.reference_audio is None
            or self.reference_text is None
        ):
            raise ValueError(
                "GPT-SoVITS requires vendor_dir, weights, reference_audio, and reference_text"
            )
        return self
