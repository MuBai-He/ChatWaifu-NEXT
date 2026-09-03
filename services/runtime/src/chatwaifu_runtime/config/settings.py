"""Validated TOML configuration with environment overrides."""

import json
import os
import tomllib
from collections.abc import Mapping
from copy import deepcopy
from pathlib import Path
from typing import Literal, Self, cast

from dotenv import dotenv_values
from pydantic import BaseModel, ConfigDict, Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


def _resource_root() -> Path:
    """Resolve immutable product resources for source and frozen runtimes."""

    configured = os.environ.get("CHATWAIFU_RESOURCE_ROOT", "").strip()
    if configured:
        return Path(configured).expanduser().resolve()
    return Path(__file__).resolve().parents[5]


PROJECT_ROOT = _resource_root()
DEFAULT_CONFIG_PATH = PROJECT_ROOT / "config" / "default.toml"


class RuntimeConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    host: str = "127.0.0.1"
    port: int = Field(default=8765, ge=0, le=65_535)
    web_origin: str = "http://127.0.0.1:5173"
    event_queue_size: int = Field(default=128, ge=8, le=4096)


class StorageConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    kind: str = "sqlite"
    database_path: Path | None = None
    journal_mode: Literal["wal"] = "wal"
    synchronous: Literal["full", "extra"] = "full"
    wal_autocheckpoint_pages: int = Field(default=1000, ge=1, le=100_000)
    foreign_keys: bool = True
    busy_timeout_ms: int = Field(default=5000, ge=100, le=60_000)


class PrivacyConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    cloud_egress: Literal["allow", "ask", "deny"] = "ask"


class SecurityConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    admin_token: SecretStr | None = None
    capability_token: SecretStr | None = Field(default=None, min_length=32)
    windows_appcontainer_launcher: Path | None = None
    allowed_hosts: list[str] = Field(default_factory=list)
    allowed_origins: list[str] = Field(default_factory=list)
    auth_enabled: bool = True


class LlmConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    provider: str = "demo"
    model: str = "chatwaifu-demo"
    base_url: str = "http://127.0.0.1:11434/v1"
    api_key: SecretStr | None = None
    timeout_seconds: float = Field(default=60.0, gt=0, le=600)
    demo_chunk_delay_ms: int = Field(default=25, ge=0, le=1000)


class TtsWorkerEndpointConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    url: str
    token: SecretStr | None = None
    display_name: str
    model: str
    languages: list[str] = Field(min_length=1, max_length=32)
    supports_voice_cloning: bool = False
    supports_style: bool = False
    supports_speed: bool = True
    supports_pitch: bool = False
    native_streaming: bool = False


class TtsConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    default_provider: str = "qwen3_tts_mlx"
    provider: str | None = None
    voice: str = "Tingting"
    sample_rate: int = Field(default=24_000, ge=8_000, le=48_000)
    rate: int = Field(default=190, ge=80, le=500)
    timeout_seconds: float = Field(default=30.0, gt=0, le=300)
    worker_url: str = "http://127.0.0.1:8767"
    worker_token: SecretStr | None = None
    workers: dict[str, TtsWorkerEndpointConfig] = Field(
        default_factory=lambda: {
            "qwen3_tts_mlx": TtsWorkerEndpointConfig(
                url="http://127.0.0.1:8767",
                display_name="Qwen3-TTS · MLX",
                model="Qwen3-TTS-12Hz-0.6B-Base-8bit",
                languages=["zh", "ja", "en"],
                supports_voice_cloning=True,
                supports_style=False,
                supports_speed=False,
                native_streaming=True,
            ),
            "gpt_sovits": TtsWorkerEndpointConfig(
                url="http://127.0.0.1:8768",
                display_name="GPT-SoVITS",
                model="local-character-voice",
                languages=["zh", "ja", "en"],
                supports_voice_cloning=True,
                supports_style=False,
                supports_speed=False,
                native_streaming=True,
            ),
        }
    )

    @property
    def selected_provider(self) -> str:
        return self.provider or self.default_provider


class RealtimeConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    enabled: bool = True
    connection_mode: Literal["cascade", "cloud_realtime"] = "cascade"
    cloud_backend: Literal["fake"] | None = None
    input_sample_rate: int = Field(default=16_000, ge=8_000, le=48_000)
    output_sample_rate: int = Field(default=24_000, ge=8_000, le=48_000)
    vad_confidence: float = Field(default=0.7, ge=0, le=1)
    vad_start_ms: int = Field(default=160, ge=0, le=2_000)
    vad_stop_ms: int = Field(default=650, ge=50, le=5_000)
    pre_roll_ms: int = Field(default=320, ge=0, le=2_000)
    max_utterance_seconds: int = Field(default=30, ge=1, le=120)
    echo_enabled: bool = False

    @model_validator(mode="after")
    def validate_cloud_backend(self) -> Self:
        if self.connection_mode == "cloud_realtime" and self.cloud_backend != "fake":
            raise ValueError(
                "When connection_mode is 'cloud_realtime', cloud_backend must be "
                "explicitly set to 'fake' (Phase 13.0-13.3)."
            )
        return self


class SttConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    provider: str = "disabled"
    worker_url: str = "http://127.0.0.1:8766"
    worker_token: SecretStr | None = None
    # ``auto`` is normalized to ``None`` at the STT domain boundary so local
    # engines can identify Chinese, Japanese, or English without a hard-coded
    # language hint.
    language: str = Field(default="auto", min_length=2, max_length=32)
    timeout_seconds: float = Field(default=60.0, gt=0, le=300)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="CHATWAIFU_",
        env_nested_delimiter="__",
        extra="ignore",
        frozen=True,
    )

    environment: str = "development"
    log_level: str = "INFO"
    config_dir: Path = Path(".local/config")
    data_dir: Path = Path(".local/data")
    characters_dir: Path = PROJECT_ROOT / "characters"
    skills_dir: Path = PROJECT_ROOT / "skills"
    runtime: RuntimeConfig = RuntimeConfig()
    storage: StorageConfig = StorageConfig()
    privacy: PrivacyConfig = PrivacyConfig()
    security: SecurityConfig = SecurityConfig()
    llm: LlmConfig = LlmConfig()
    tts: TtsConfig = TtsConfig()
    realtime: RealtimeConfig = RealtimeConfig()
    stt: SttConfig = SttConfig()

    @model_validator(mode="after")
    def validate_local_bind(self) -> Self:
        if self.runtime.host not in {"127.0.0.1", "localhost", "::1"}:
            raise ValueError("basic demo Runtime must bind to a loopback address")
        if self.storage.kind != "sqlite":
            raise ValueError("basic demo supports only SQLite persistence")
        return self

    @property
    def database_path(self) -> Path:
        return self.storage.database_path or self.data_dir / "chatwaifu.db"

    def public_dict(self) -> dict[str, object]:
        public = self.model_dump(mode="json", exclude={"security", "llm", "stt", "tts"})
        public["llm"] = self.llm.model_dump(mode="json", exclude={"api_key"})
        public["stt"] = self.stt.model_dump(mode="json", exclude={"worker_token"})
        public["tts"] = self.tts.model_dump(mode="json", exclude={"worker_token"})
        return public


def load_settings(config_path: Path | None = None, env_path: Path | None = None) -> Settings:
    path = config_path or DEFAULT_CONFIG_PATH
    data: dict[str, object] = {}
    if path.exists():
        with path.open("rb") as config_file:
            loaded = tomllib.load(config_file)
        data = deepcopy(loaded)
    dotenv = dotenv_values(env_path or PROJECT_ROOT / ".env")
    _merge_environment(data, {key: value for key, value in dotenv.items() if value is not None})
    _merge_environment(data, os.environ)
    return Settings.model_validate(data)


def _merge_environment(data: dict[str, object], environment: Mapping[str, str]) -> None:
    prefix = "CHATWAIFU_"
    aliases = {
        "CONFIG_DIR": ["config_dir"],
        "DATA_DIR": ["data_dir"],
        "ENVIRONMENT": ["environment"],
        "LOG_LEVEL": ["log_level"],
    }
    for name, raw_value in environment.items():
        if not name.startswith(prefix):
            continue
        suffix = name[len(prefix) :]
        path = aliases.get(suffix, [part.lower() for part in suffix.split("__")])
        _set_nested(data, path, _parse_env_value(raw_value))


def _set_nested(target: dict[str, object], path: list[str], value: object) -> None:
    cursor = target
    for part in path[:-1]:
        child_object = cursor.get(part)
        if isinstance(child_object, dict):
            child = cast(dict[str, object], child_object)
        else:
            child = {}
            cursor[part] = child
        cursor = child
    cursor[path[-1]] = value


def _parse_env_value(value: str) -> object:
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return value
