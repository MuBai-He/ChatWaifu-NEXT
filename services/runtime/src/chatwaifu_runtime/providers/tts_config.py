"""Runtime-persisted cloud TTS configuration with write-only local secrets."""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal, cast

from pydantic import BaseModel, ConfigDict, Field, model_validator

from chatwaifu_runtime.config.settings import Settings
from chatwaifu_runtime.persistence.database import Database

ALIYUN_TTS_PROVIDER_ID = "aliyun_qwen_realtime"
DEFAULT_ALIYUN_TTS_MODEL = "qwen3-tts-vc-realtime-2026-01-15"
DEFAULT_ALIYUN_VOICE_ID = "qwen-tts-vc-bailian-voice-20260828030329088-e738"

type AliyunRegion = Literal["beijing", "singapore"]
type TtsLanguage = Literal[
    "Auto",
    "Chinese",
    "English",
    "German",
    "Italian",
    "Portuguese",
    "Spanish",
    "Japanese",
    "Korean",
    "French",
    "Russian",
]


class AliyunTtsConfiguration(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    provider_id: Literal["aliyun_qwen_realtime"] = ALIYUN_TTS_PROVIDER_ID
    enabled: bool = False
    model: str = Field(default=DEFAULT_ALIYUN_TTS_MODEL, min_length=1, max_length=256)
    voice_id: str = Field(default=DEFAULT_ALIYUN_VOICE_ID, min_length=1, max_length=256)
    region: AliyunRegion = "beijing"
    workspace_id: str = Field(default="", max_length=256)
    language_type: TtsLanguage = "Auto"
    sample_rate: Literal[8000, 16000, 24000, 48000] = 24000
    speech_rate: float = Field(default=1.0, ge=0.5, le=2.0)
    volume: int = Field(default=50, ge=0, le=100)
    pitch_rate: float = Field(default=1.0, ge=0.5, le=2.0)
    timeout_seconds: float = Field(default=45.0, gt=0, le=300)
    max_audio_bytes: int = Field(default=32_000_000, ge=1_000_000, le=128_000_000)
    api_key_configured: bool = False
    updated_at: datetime

    @model_validator(mode="after")
    def validate_voice_model_pair(self) -> AliyunTtsConfiguration:
        if "-vc-realtime-" not in self.model:
            raise ValueError("声音复刻音色必须搭配 qwen3-tts-vc-realtime 模型")
        return self

    @property
    def websocket_base_url(self) -> str:
        if self.region == "singapore":
            return "wss://dashscope-intl.aliyuncs.com/api-ws/v1/realtime"
        return "wss://dashscope.aliyuncs.com/api-ws/v1/realtime"

    @property
    def voice_catalog_url(self) -> str:
        if self.region == "singapore":
            return "https://dashscope-intl.aliyuncs.com/api/v1/services/audio/tts/customization"
        return "https://dashscope.aliyuncs.com/api/v1/services/audio/tts/customization"


class _LocalTtsSecretStore:
    """Mode-0600 storage that never exposes values through Runtime responses."""

    def __init__(self, path: Path) -> None:
        self._path = path

    def get(self, provider_id: str) -> str | None:
        value = self._read().get(provider_id)
        return value if isinstance(value, str) and value else None

    def set(self, provider_id: str, value: str | None) -> None:
        secrets = self._read()
        if value:
            secrets[provider_id] = value
        else:
            secrets.pop(provider_id, None)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self._path.with_name(f".{self._path.name}.tmp")
        descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as secret_file:
                secret_file.write(
                    json.dumps(secrets, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
                )
                secret_file.flush()
                os.fsync(secret_file.fileno())
            os.replace(temporary, self._path)
            os.chmod(self._path, 0o600)
        finally:
            temporary.unlink(missing_ok=True)

    def _read(self) -> dict[str, str]:
        if not self._path.exists():
            return {}
        try:
            value: object = json.loads(self._path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return {}
        if not isinstance(value, dict):
            return {}
        return {
            str(key): item
            for key, item in cast(dict[object, object], value).items()
            if isinstance(item, str)
        }


class TtsConfigurationService:
    def __init__(self, database: Database, settings: Settings) -> None:
        self._database = database
        self._secrets = _LocalTtsSecretStore(settings.config_dir / "tts-secrets.json")
        self._config: AliyunTtsConfiguration | None = None

    async def start(self) -> None:
        now = datetime.now(UTC).isoformat()
        async with self._database.transaction() as connection:
            await connection.execute(
                """
                INSERT OR IGNORE INTO tts_cloud_configs(
                    provider_id, enabled, model, voice_id, region, workspace_id,
                    language_type, sample_rate, speech_rate, volume, pitch_rate,
                    timeout_seconds, max_audio_bytes, updated_at
                ) VALUES (?, 0, ?, ?, 'beijing', '', 'Auto', 24000, 1.0, 50, 1.0, 45.0,
                          32000000, ?)
                """,
                (
                    ALIYUN_TTS_PROVIDER_ID,
                    DEFAULT_ALIYUN_TTS_MODEL,
                    DEFAULT_ALIYUN_VOICE_ID,
                    now,
                ),
            )
        await self.reload()

    async def reload(self) -> None:
        row = await self._database.fetchone(
            """
            SELECT provider_id, enabled, model, voice_id, region, workspace_id,
                   language_type, sample_rate, speech_rate, volume, pitch_rate,
                   timeout_seconds, max_audio_bytes, updated_at
            FROM tts_cloud_configs WHERE provider_id = ?
            """,
            (ALIYUN_TTS_PROVIDER_ID,),
        )
        if row is None:
            raise RuntimeError("Aliyun TTS configuration was not initialized")
        self._config = AliyunTtsConfiguration.model_validate(
            {
                **dict(row),
                "enabled": bool(row["enabled"]),
                "api_key_configured": self.api_key() is not None,
                "updated_at": datetime.fromisoformat(str(row["updated_at"])),
            }
        )

    def get(self) -> AliyunTtsConfiguration:
        config = self._config
        if config is None:
            return AliyunTtsConfiguration(updated_at=datetime.now(UTC))
        return config

    def api_key(self) -> str | None:
        return self._secrets.get(ALIYUN_TTS_PROVIDER_ID)

    async def update(
        self,
        config: AliyunTtsConfiguration,
        *,
        api_key: str | None = None,
        clear_api_key: bool = False,
    ) -> AliyunTtsConfiguration:
        now = datetime.now(UTC)
        async with self._database.transaction() as connection:
            await connection.execute(
                """
                UPDATE tts_cloud_configs SET
                    enabled = ?, model = ?, voice_id = ?, region = ?, workspace_id = ?,
                    language_type = ?, sample_rate = ?, speech_rate = ?, volume = ?,
                    pitch_rate = ?, timeout_seconds = ?, max_audio_bytes = ?, updated_at = ?
                WHERE provider_id = ?
                """,
                (
                    int(config.enabled),
                    config.model,
                    config.voice_id,
                    config.region,
                    config.workspace_id.strip(),
                    config.language_type,
                    config.sample_rate,
                    config.speech_rate,
                    config.volume,
                    config.pitch_rate,
                    config.timeout_seconds,
                    config.max_audio_bytes,
                    now.isoformat(),
                    ALIYUN_TTS_PROVIDER_ID,
                ),
            )
        if clear_api_key:
            self._secrets.set(ALIYUN_TTS_PROVIDER_ID, None)
        elif api_key is not None and api_key.strip():
            self._secrets.set(ALIYUN_TTS_PROVIDER_ID, api_key.strip())
        await self.reload()
        return self.get()
