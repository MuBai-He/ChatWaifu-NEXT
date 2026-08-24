"""TTS worker-only settings; the ephemeral token never leaves loopback."""

from pathlib import Path

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class WorkerSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="CHATWAIFU_TTS_WORKER_",
        extra="ignore",
        frozen=True,
    )

    host: str = "127.0.0.1"
    port: int = Field(default=8767, ge=1, le=65_535)
    token: SecretStr
    worker_id: str = "tts-sherpa-kokoro"
    model: str = "kokoro-multi-lang-v1_1"
    model_dir: Path = Path(".local/models/kokoro/kokoro-multi-lang-v1_1")
    num_threads: int = Field(default=2, ge=1, le=16)
    preload: bool = True
