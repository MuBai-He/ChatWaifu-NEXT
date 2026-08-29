"""Configuration shared by the Qwen MLX and GPT-SoVITS worker processes."""

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
    backend: Literal["qwen3_tts_mlx", "gpt_sovits"]
    provider_id: str = Field(pattern=r"^[a-z0-9][a-z0-9_.-]{1,127}$")
    display_name: str
    worker_id: str
    model: str
    vendor_dir: Path
    model_dir: Path | None = None
    qwen_voice: str | None = Field(default=None, min_length=1, max_length=128)
    gpt_weights: Path | None = None
    sovits_weights: Path | None = None
    reference_audio: Path
    reference_text: str = Field(min_length=1, max_length=2_000)
    reference_language: Literal["zh", "ja", "en"] = "ja"
    device: str = "mps"
    preload: bool = False
    streaming_interval: float = Field(default=0.5, ge=0.08, le=4.0)
    stream_queue_size: int = Field(default=8, ge=1, le=64)
    max_stream_audio_bytes: int = Field(default=64_000_000, ge=1_000_000, le=256_000_000)
    temperature: float = Field(default=0.7, ge=0.1, le=2.0)

    @model_validator(mode="after")
    def validate_backend_paths(self) -> "WorkerSettings":
        if self.backend == "qwen3_tts_mlx" and self.model_dir is None:
            raise ValueError("Qwen MLX requires model_dir")
        if self.backend == "gpt_sovits" and (
            self.gpt_weights is None or self.sovits_weights is None
        ):
            raise ValueError("GPT-SoVITS requires gpt_weights and sovits_weights")
        return self
