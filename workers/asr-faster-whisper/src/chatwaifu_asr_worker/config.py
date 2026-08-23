"""ASR worker-only settings; secrets never cross into public status."""

from pathlib import Path

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class WorkerSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="CHATWAIFU_STT_WORKER_",
        extra="ignore",
        frozen=True,
    )

    host: str = "127.0.0.1"
    port: int = Field(default=8766, ge=1, le=65_535)
    token: SecretStr
    worker_id: str = "asr-faster-whisper"
    model: str = "base"
    model_dir: Path = Path(".local/models/faster-whisper")
    device: str = "cpu"
    compute_type: str = "int8"
    preload: bool = True
