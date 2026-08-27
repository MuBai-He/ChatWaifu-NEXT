"""Runtime-persisted cloud TTS configuration with write-only local secrets."""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal, cast

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from chatwaifu_runtime.config.settings import Settings
from chatwaifu_runtime.persistence.database import Database

ALIYUN_QWEN_TTS_PROVIDER_ID = "aliyun_qwen_realtime"
ALIYUN_COSYVOICE_TTS_PROVIDER_ID = "aliyun_cosyvoice_realtime"
# Backwards-compatible name retained for the existing Qwen adapter and callers.
ALIYUN_TTS_PROVIDER_ID = ALIYUN_QWEN_TTS_PROVIDER_ID
DEFAULT_ALIYUN_TTS_MODEL = "qwen3-tts-vc-realtime-2026-01-15"
DEFAULT_ALIYUN_VOICE_ID = "qwen-tts-vc-bailian-voice-20260828030329088-e738"
DEFAULT_COSYVOICE_MODEL = "cosyvoice-v3.5-plus"
DEFAULT_COSYVOICE_INSTRUCTION = "温柔自然，带一点害羞，避免播音腔。"

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
type CosyVoiceLanguage = Literal[
    "auto",
    "zh",
    "en",
    "fr",
    "de",
    "ja",
    "ko",
    "ru",
    "pt",
    "th",
    "id",
    "vi",
    "es",
    "it",
    "ms",
    "fil",
    "ar",
]


class _AliyunCloudTtsConfiguration(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    enabled: bool = False
    model: str = Field(min_length=1, max_length=256)
    voice_id: str = Field(default="", max_length=256)
    region: AliyunRegion = "beijing"
    workspace_id: str = Field(default="", max_length=256)
    sample_rate: Literal[8000, 16000, 24000, 48000] = 24000
    speech_rate: float = Field(default=1.0, ge=0.5, le=2.0)
    volume: int = Field(default=50, ge=0, le=100)
    pitch_rate: float = Field(default=1.0, ge=0.5, le=2.0)
    instruction: str = Field(default="", max_length=100)
    timeout_seconds: float = Field(default=45.0, gt=0, le=300)
    max_audio_bytes: int = Field(default=32_000_000, ge=1_000_000, le=128_000_000)
    api_key_configured: bool = False
    updated_at: datetime

    @field_validator("workspace_id")
    @classmethod
    def validate_workspace_id(cls, value: str) -> str:
        normalized = value.strip()
        if normalized and not all(
            character.isalnum() or character in {"-", "_"} for character in normalized
        ):
            raise ValueError("业务空间 ID 只能包含字母、数字、连字符和下划线")
        return normalized

    @property
    def voice_catalog_url(self) -> str:
        if self.workspace_id:
            if self.region == "singapore":
                host = f"{self.workspace_id}.ap-southeast-1.maas.aliyuncs.com"
            else:
                host = f"{self.workspace_id}.cn-beijing.maas.aliyuncs.com"
            return f"https://{host}/api/v1/services/audio/tts/customization"
        if self.region == "singapore":
            return "https://dashscope-intl.aliyuncs.com/api/v1/services/audio/tts/customization"
        return "https://dashscope.aliyuncs.com/api/v1/services/audio/tts/customization"


class AliyunTtsConfiguration(_AliyunCloudTtsConfiguration):
    provider_id: Literal["aliyun_qwen_realtime"] = ALIYUN_TTS_PROVIDER_ID
    model: str = Field(default=DEFAULT_ALIYUN_TTS_MODEL, min_length=1, max_length=256)
    voice_id: str = Field(default=DEFAULT_ALIYUN_VOICE_ID, min_length=1, max_length=256)
    language_type: TtsLanguage = "Auto"

    @model_validator(mode="after")
    def validate_voice_model_pair(self) -> AliyunTtsConfiguration:
        if "-vc-realtime-" not in self.model:
            raise ValueError("声音复刻音色必须搭配 qwen3-tts-vc-realtime 模型")
        if self.instruction:
            raise ValueError("Qwen3-TTS VC-Realtime 不支持情绪指令")
        return self

    @property
    def websocket_base_url(self) -> str:
        if self.region == "singapore":
            return "wss://dashscope-intl.aliyuncs.com/api-ws/v1/realtime"
        return "wss://dashscope.aliyuncs.com/api-ws/v1/realtime"


class AliyunCosyVoiceTtsConfiguration(_AliyunCloudTtsConfiguration):
    provider_id: Literal["aliyun_cosyvoice_realtime"] = ALIYUN_COSYVOICE_TTS_PROVIDER_ID
    model: str = Field(default=DEFAULT_COSYVOICE_MODEL, min_length=1, max_length=256)
    language_type: CosyVoiceLanguage = "auto"
    instruction: str = Field(default=DEFAULT_COSYVOICE_INSTRUCTION, max_length=100)

    @model_validator(mode="after")
    def validate_cosyvoice_configuration(self) -> AliyunCosyVoiceTtsConfiguration:
        if not self.model.startswith("cosyvoice-v"):
            raise ValueError("CosyVoice 音色必须搭配 cosyvoice 模型")
        if self.enabled and not self.voice_id.strip():
            raise ValueError("启用 CosyVoice 前需要填写声音复刻音色 ID")
        if self.region == "singapore" and self.model != "cosyvoice-v3-plus":
            raise ValueError("当前复刻音色在新加坡地域仅支持 cosyvoice-v3-plus")
        if _instruction_units(self.instruction) > 100:
            raise ValueError("CosyVoice 情绪指令超过 100 字符单位")
        return self

    @property
    def websocket_base_url(self) -> str:
        if self.workspace_id:
            if self.region == "singapore":
                host = f"{self.workspace_id}.ap-southeast-1.maas.aliyuncs.com"
            else:
                host = f"{self.workspace_id}.cn-beijing.maas.aliyuncs.com"
            return f"wss://{host}/api-ws/v1/inference"
        if self.region == "singapore":
            return "wss://dashscope-intl.aliyuncs.com/api-ws/v1/inference"
        return "wss://dashscope.aliyuncs.com/api-ws/v1/inference"


type AliyunCloudTtsConfiguration = AliyunTtsConfiguration | AliyunCosyVoiceTtsConfiguration


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
        self._configs: dict[str, AliyunCloudTtsConfiguration] = {}

    async def start(self) -> None:
        now = datetime.now(UTC).isoformat()
        async with self._database.transaction() as connection:
            await connection.execute(
                """
                INSERT OR IGNORE INTO tts_cloud_configs(
                    provider_id, enabled, model, voice_id, region, workspace_id,
                    language_type, sample_rate, speech_rate, volume, pitch_rate,
                    instruction, timeout_seconds, max_audio_bytes, updated_at
                ) VALUES (?, 0, ?, ?, 'beijing', '', 'Auto', 24000, 1.0, 50, 1.0, '',
                          45.0, 32000000, ?)
                """,
                (
                    ALIYUN_TTS_PROVIDER_ID,
                    DEFAULT_ALIYUN_TTS_MODEL,
                    DEFAULT_ALIYUN_VOICE_ID,
                    now,
                ),
            )
            await connection.execute(
                """
                INSERT OR IGNORE INTO tts_cloud_configs(
                    provider_id, enabled, model, voice_id, region, workspace_id,
                    language_type, sample_rate, speech_rate, volume, pitch_rate,
                    instruction, timeout_seconds, max_audio_bytes, updated_at
                ) VALUES (?, 0, ?, '', 'beijing', '', 'auto', 24000, 1.0, 50, 1.0, ?,
                          45.0, 32000000, ?)
                """,
                (
                    ALIYUN_COSYVOICE_TTS_PROVIDER_ID,
                    DEFAULT_COSYVOICE_MODEL,
                    DEFAULT_COSYVOICE_INSTRUCTION,
                    now,
                ),
            )
        await self.reload()

    async def reload(self) -> None:
        rows = await self._database.fetchall(
            """
            SELECT provider_id, enabled, model, voice_id, region, workspace_id,
                   language_type, sample_rate, speech_rate, volume, pitch_rate,
                   instruction, timeout_seconds, max_audio_bytes, updated_at
            FROM tts_cloud_configs WHERE provider_id IN (?, ?)
            """,
            (ALIYUN_QWEN_TTS_PROVIDER_ID, ALIYUN_COSYVOICE_TTS_PROVIDER_ID),
        )
        configs: dict[str, AliyunCloudTtsConfiguration] = {}
        for row in rows:
            provider_id = str(row["provider_id"])
            values = {
                **dict(row),
                "enabled": bool(row["enabled"]),
                "api_key_configured": self.api_key(provider_id) is not None,
                "updated_at": datetime.fromisoformat(str(row["updated_at"])),
            }
            if provider_id == ALIYUN_QWEN_TTS_PROVIDER_ID:
                configs[provider_id] = AliyunTtsConfiguration.model_validate(values)
            elif provider_id == ALIYUN_COSYVOICE_TTS_PROVIDER_ID:
                configs[provider_id] = AliyunCosyVoiceTtsConfiguration.model_validate(values)
        if len(configs) != 2:
            raise RuntimeError("Aliyun TTS configurations were not initialized")
        self._configs = configs

    def get(self) -> AliyunTtsConfiguration:
        config = self._configs.get(ALIYUN_QWEN_TTS_PROVIDER_ID)
        if not isinstance(config, AliyunTtsConfiguration):
            return AliyunTtsConfiguration(updated_at=datetime.now(UTC))
        return config

    def get_cosyvoice(self) -> AliyunCosyVoiceTtsConfiguration:
        config = self._configs.get(ALIYUN_COSYVOICE_TTS_PROVIDER_ID)
        if not isinstance(config, AliyunCosyVoiceTtsConfiguration):
            return AliyunCosyVoiceTtsConfiguration(updated_at=datetime.now(UTC))
        return config

    def api_key(self, provider_id: str = ALIYUN_QWEN_TTS_PROVIDER_ID) -> str | None:
        configured = self._secrets.get(provider_id)
        if configured is not None:
            return configured
        if provider_id != ALIYUN_QWEN_TTS_PROVIDER_ID:
            return self._secrets.get(ALIYUN_QWEN_TTS_PROVIDER_ID)
        return None

    async def update(
        self,
        config: AliyunCloudTtsConfiguration,
        *,
        api_key: str | None = None,
        clear_api_key: bool = False,
    ) -> AliyunCloudTtsConfiguration:
        now = datetime.now(UTC)
        async with self._database.transaction() as connection:
            await connection.execute(
                """
                UPDATE tts_cloud_configs SET
                    enabled = ?, model = ?, voice_id = ?, region = ?, workspace_id = ?,
                    language_type = ?, sample_rate = ?, speech_rate = ?, volume = ?,
                    pitch_rate = ?, instruction = ?, timeout_seconds = ?,
                    max_audio_bytes = ?, updated_at = ?
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
                    config.instruction,
                    config.timeout_seconds,
                    config.max_audio_bytes,
                    now.isoformat(),
                    config.provider_id,
                ),
            )
        if clear_api_key:
            self._secrets.set(config.provider_id, None)
        elif api_key is not None and api_key.strip():
            self._secrets.set(config.provider_id, api_key.strip())
        await self.reload()
        if config.provider_id == ALIYUN_COSYVOICE_TTS_PROVIDER_ID:
            return self.get_cosyvoice()
        return self.get()


def _instruction_units(value: str) -> int:
    return sum(2 if "\u2e80" <= character <= "\u9fff" else 1 for character in value)
