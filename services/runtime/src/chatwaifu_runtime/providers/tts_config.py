"""Runtime-persisted cloud TTS configuration with write-only local secrets."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal, cast

import aiosqlite
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from chatwaifu_runtime.config.settings import Settings
from chatwaifu_runtime.persistence.atomic_secret_store import AtomicSecretStore
from chatwaifu_runtime.persistence.database import Database
from chatwaifu_runtime.providers.contracts import TtsProvider

ALIYUN_QWEN_TTS_PROVIDER_ID = "aliyun_qwen_realtime"
ALIYUN_COSYVOICE_TTS_PROVIDER_ID = "aliyun_cosyvoice_realtime"
# Backwards-compatible name retained for the existing Qwen adapter and callers.
ALIYUN_TTS_PROVIDER_ID = ALIYUN_QWEN_TTS_PROVIDER_ID
DEFAULT_ALIYUN_TTS_MODEL = "qwen3-tts-vc-realtime-2026-01-15"
DEFAULT_ALIYUN_VOICE_ID = "qwen-tts-vc-bailian-voice-20260828030329088-e738"
DEFAULT_COSYVOICE_MODEL = "cosyvoice-v3.5-plus"
DEFAULT_COSYVOICE_INSTRUCTION = "温柔自然，带一点害羞，避免播音腔。"
COSYVOICE_INSTRUCTION_MODELS = frozenset(
    {"cosyvoice-v3.5-plus", "cosyvoice-v3.5-flash", "cosyvoice-v3-flash"}
)

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
        if self.instruction and self.model not in COSYVOICE_INSTRUCTION_MODELS:
            raise ValueError(f"{self.model} 不支持情绪指令，请清空基础情绪指令")
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


type TtsUiControl = Literal["toggle", "text", "secret", "select", "number", "textarea"]


@dataclass(frozen=True, slots=True)
class TtsUiOption:
    value: str | int | float
    label: str

    def public(self) -> dict[str, object]:
        return {"value": self.value, "label": self.label}


@dataclass(frozen=True, slots=True)
class TtsUiField:
    key: str
    label: str
    control: TtsUiControl
    advanced: bool = False
    placeholder: str = ""
    help_text: str = ""
    options: tuple[TtsUiOption, ...] = ()
    minimum: float | None = None
    maximum: float | None = None
    step: float | None = None

    def public(self) -> dict[str, object]:
        return {
            "key": self.key,
            "label": self.label,
            "control": self.control,
            "advanced": self.advanced,
            "placeholder": self.placeholder,
            "help_text": self.help_text,
            "options": [option.public() for option in self.options],
            "minimum": self.minimum,
            "maximum": self.maximum,
            "step": self.step,
        }


@dataclass(frozen=True, slots=True)
class TtsProviderPresentation:
    group_id: str | None = None
    group_display_name: str | None = None
    variant_label: str | None = None
    group_default: bool = False

    def __post_init__(self) -> None:
        if self.group_id is None:
            if self.group_display_name is not None or self.variant_label is not None:
                raise ValueError("ungrouped TTS presentation cannot declare group labels")
            if self.group_default:
                raise ValueError("ungrouped TTS presentation cannot be a group default")
        elif not self.group_display_name or not self.variant_label:
            raise ValueError("grouped TTS presentation requires group and variant labels")

    def public(self) -> dict[str, object]:
        return {
            "group_id": self.group_id,
            "group_display_name": self.group_display_name,
            "variant_label": self.variant_label,
            "group_default": self.group_default,
        }


@dataclass(frozen=True, slots=True)
class TtsProviderRegistration:
    provider_id: str
    display_name: str
    configuration_type: type[BaseModel]
    default_factory: Callable[[datetime], BaseModel]
    build: Callable[[TtsConfigurationService], TtsProvider]
    ui_fields: tuple[TtsUiField, ...]
    configuration_schema_version: str = "1.0"
    api_key_fallback_provider_id: str | None = None
    presentation: TtsProviderPresentation = TtsProviderPresentation()

    def __post_init__(self) -> None:
        if not self.provider_id or "provider_id" not in self.configuration_type.model_fields:
            raise ValueError("TTS configuration models must declare provider_id")
        if not self.configuration_schema_version:
            raise ValueError("TTS configuration schema version cannot be blank")
        keys = [field.key for field in self.ui_fields]
        if len(keys) != len(set(keys)):
            raise ValueError("TTS UI field keys must be unique")
        model_fields = self.configuration_type.model_fields
        for field in self.ui_fields:
            if field.control == "secret":
                if field.key != "api_key":
                    raise ValueError("TTS registrations currently support only api_key secrets")
            elif field.key not in model_fields:
                raise ValueError(f"TTS UI field is absent from configuration model: {field.key}")
        if self.api_key_fallback_provider_id is not None and not self.has_api_key:
            raise ValueError("TTS API key fallback requires an api_key UI field")
        if self.has_api_key and "api_key_configured" not in model_fields:
            raise ValueError("TTS API key registrations must declare api_key_configured")
        default = self.default_factory(datetime.now(UTC))
        if not isinstance(default, self.configuration_type):
            raise ValueError("TTS default factory returned the wrong configuration type")
        if _config_provider_id(default) != self.provider_id:
            raise ValueError("TTS default configuration has a mismatched provider ID")

    @property
    def has_api_key(self) -> bool:
        return any(field.control == "secret" for field in self.ui_fields)

    def schema(self) -> dict[str, object]:
        return cast(dict[str, object], self.configuration_type.model_json_schema())

    def ui_schema(self) -> dict[str, object]:
        return {
            "schema_version": "1.0",
            "fields": [field.public() for field in self.ui_fields],
        }

    def credential(self) -> dict[str, object] | None:
        if not self.has_api_key:
            return None
        return {
            "kind": "api_key",
            "field_key": "api_key",
            "configured_field": "api_key_configured",
            "clear_field": "clear_api_key",
            "fallback_provider_id": self.api_key_fallback_provider_id,
        }


@dataclass(frozen=True, slots=True)
class _TtsSecretMutation:
    previous_secret: str | None
    next_secret: str | None
    previous_updated_at: str
    next_updated_at: str


class _TtsSecretMutationJournal:
    """Durable intent spanning one SQLite row and one secret-file entry."""

    def __init__(self, path: Path) -> None:
        self._store = AtomicSecretStore(path)

    def get(self, provider_id: str) -> _TtsSecretMutation | None:
        serialized = self._store.get(provider_id)
        if serialized is None:
            return None
        try:
            value: object = json.loads(serialized)
        except json.JSONDecodeError as error:
            raise RuntimeError("TTS secret mutation journal is corrupt") from error
        if not isinstance(value, dict):
            raise RuntimeError("TTS secret mutation journal has an invalid document")
        document = cast(dict[str, object], value)
        previous_secret = document.get("previous_secret")
        next_secret = document.get("next_secret")
        previous_updated_at = document.get("previous_updated_at")
        next_updated_at = document.get("next_updated_at")
        if (
            (previous_secret is not None and not isinstance(previous_secret, str))
            or (next_secret is not None and not isinstance(next_secret, str))
            or not isinstance(previous_updated_at, str)
            or not isinstance(next_updated_at, str)
        ):
            raise RuntimeError("TTS secret mutation journal has invalid entries")
        return _TtsSecretMutation(
            previous_secret=previous_secret,
            next_secret=next_secret,
            previous_updated_at=previous_updated_at,
            next_updated_at=next_updated_at,
        )

    def prepare(self, provider_id: str, mutation: _TtsSecretMutation) -> None:
        self._store.set(
            provider_id,
            json.dumps(
                {
                    "previous_secret": mutation.previous_secret,
                    "next_secret": mutation.next_secret,
                    "previous_updated_at": mutation.previous_updated_at,
                    "next_updated_at": mutation.next_updated_at,
                },
                ensure_ascii=False,
                sort_keys=True,
            ),
        )

    def discard(self, provider_id: str) -> None:
        self._store.set(provider_id, None)


class TtsConfigurationService:
    def __init__(
        self,
        database: Database,
        settings: Settings,
        registrations: tuple[TtsProviderRegistration, ...],
    ) -> None:
        provider_ids = tuple(item.provider_id for item in registrations)
        if not provider_ids:
            raise ValueError("at least one TTS provider registration is required")
        if len(set(provider_ids)) != len(provider_ids):
            raise ValueError("TTS provider registrations must use unique provider IDs")
        self._database = database
        self._secrets = AtomicSecretStore(settings.config_dir / "tts-secrets.json")
        self._secret_mutations = _TtsSecretMutationJournal(
            settings.config_dir / "tts-secret-mutations.json"
        )
        self._registrations = {item.provider_id: item for item in registrations}
        for registration in registrations:
            fallback = registration.api_key_fallback_provider_id
            if fallback is None:
                continue
            target = self._registrations.get(fallback)
            if target is None or not target.has_api_key:
                raise ValueError("TTS API key fallback must target a registered API key provider")
        self._configs: dict[str, BaseModel] = {}
        self._api_keys: dict[str, str | None] = {}
        # The secret journal spans SQLite and a separate atomic file. Keep the
        # complete recovery/update/reload sequence single-writer so two HTTP
        # requests cannot compensate each other's secret or publish a mixed
        # in-memory configuration.
        self._mutation_lock = asyncio.Lock()

    async def start(self) -> None:
        async with self._mutation_lock:
            await self._start_unlocked()

    async def _start_unlocked(self) -> None:
        now = datetime.now(UTC)
        async with self._database.transaction() as connection:
            for registration in self._registrations.values():
                cursor = await connection.execute(
                    "SELECT 1 FROM tts_provider_configs WHERE provider_id = ?",
                    (registration.provider_id,),
                )
                current = await cursor.fetchone()
                await cursor.close()
                if current is not None:
                    continue
                cursor = await connection.execute(
                    "SELECT * FROM tts_cloud_configs WHERE provider_id = ?",
                    (registration.provider_id,),
                )
                legacy = await cursor.fetchone()
                await cursor.close()
                config = (
                    self._legacy_configuration(registration, dict(legacy))
                    if legacy is not None
                    else registration.default_factory(now)
                )
                updated_at = _config_updated_at(config, now).isoformat()
                await connection.execute(
                    """
                    INSERT INTO tts_provider_configs(
                        provider_id, schema_version, configuration_json, updated_at
                    ) VALUES (?, ?, ?, ?)
                    """,
                    (
                        registration.provider_id,
                        registration.configuration_schema_version,
                        _serialize_configuration(config),
                        updated_at,
                    ),
                )
                await self._mirror_legacy_configuration(
                    connection,
                    config,
                    updated_at,
                    insert_if_missing=True,
                )
        await self._recover_secret_mutations()
        await _run_blocking_atomically(lambda: self._secrets.prune(set(self._registrations)))
        await self._reload_unlocked()

    async def reload(self) -> None:
        async with self._mutation_lock:
            await self._reload_unlocked()

    async def _reload_unlocked(self) -> None:
        provider_ids = tuple(self._registrations)
        configured_secrets = {
            provider_id: self._secrets.get(provider_id) for provider_id in provider_ids
        }
        resolved_api_keys: dict[str, str | None] = {}
        for provider_id, registration in self._registrations.items():
            secret = configured_secrets[provider_id]
            if secret is None and registration.api_key_fallback_provider_id is not None:
                secret = configured_secrets.get(registration.api_key_fallback_provider_id)
            resolved_api_keys[provider_id] = secret
        placeholders = ", ".join("?" for _ in provider_ids)
        rows = await self._database.fetchall(
            f"""
            SELECT provider_id, schema_version, configuration_json, updated_at
            FROM tts_provider_configs WHERE provider_id IN ({placeholders})
            """,
            provider_ids,
        )
        configs: dict[str, BaseModel] = {}
        for row in rows:
            provider_id = str(row["provider_id"])
            registration = self._registrations.get(provider_id)
            if registration is not None:
                schema_version = str(row["schema_version"])
                if schema_version != registration.configuration_schema_version:
                    raise RuntimeError(
                        f"unsupported TTS configuration version for {provider_id}: {schema_version}"
                    )
                values = _parse_configuration(str(row["configuration_json"]))
                configs[provider_id] = self._validate_configuration_values(
                    registration,
                    values,
                    updated_at=datetime.fromisoformat(str(row["updated_at"])),
                    api_key_configured=resolved_api_keys[provider_id] is not None,
                )
        if len(configs) != len(self._registrations):
            raise RuntimeError("registered TTS configurations were not initialized")
        # No await between these assignments: event-loop readers see either the
        # previous effective config/key pair or the newly committed pair.
        self._api_keys = resolved_api_keys
        self._configs = configs

    def registrations(self) -> tuple[TtsProviderRegistration, ...]:
        return tuple(self._registrations.values())

    def registration_for(self, provider_id: str) -> TtsProviderRegistration | None:
        return self._registrations.get(provider_id)

    def get_for(self, provider_id: str) -> BaseModel:
        config = self._configs.get(provider_id)
        if config is not None:
            return config
        registration = self._registrations.get(provider_id)
        if registration is None:
            raise KeyError(provider_id)
        return registration.default_factory(datetime.now(UTC))

    def validate_update(self, provider_id: str, values: dict[str, object]) -> BaseModel:
        registration = self._registrations.get(provider_id)
        if registration is None:
            raise KeyError(provider_id)
        return self._validate_configuration_values(
            registration,
            {**values, "provider_id": provider_id},
            updated_at=datetime.now(UTC),
            api_key_configured=self.api_key(provider_id) is not None,
        )

    def get(self) -> AliyunTtsConfiguration:
        return cast(AliyunTtsConfiguration, self.get_for(ALIYUN_QWEN_TTS_PROVIDER_ID))

    def get_cosyvoice(self) -> AliyunCosyVoiceTtsConfiguration:
        return cast(
            AliyunCosyVoiceTtsConfiguration,
            self.get_for(ALIYUN_COSYVOICE_TTS_PROVIDER_ID),
        )

    def api_key(self, provider_id: str = ALIYUN_QWEN_TTS_PROVIDER_ID) -> str | None:
        return self._api_keys.get(provider_id)

    async def update(
        self,
        config: BaseModel,
        *,
        api_key: str | None = None,
        clear_api_key: bool = False,
    ) -> BaseModel:
        async with self._mutation_lock:
            return await self._update_unlocked(
                config,
                api_key=api_key,
                clear_api_key=clear_api_key,
            )

    async def update_patch(
        self,
        provider_id: str,
        values: dict[str, object],
        *,
        api_key: str | None = None,
        clear_api_key: bool = False,
    ) -> BaseModel:
        """Merge and persist one API patch under the mutation lock.

        Keeping the read/merge step inside the same critical section as the
        secret journal prevents two HTTP PATCH-like PUT requests from both
        reading an old snapshot and silently reverting each other's fields.
        """

        async with self._mutation_lock:
            current = self.get_for(provider_id)
            merged = current.model_dump(exclude={"provider_id", "api_key_configured", "updated_at"})
            merged.update(values)
            config = self.validate_update(provider_id, merged)
            return await self._update_unlocked(
                config,
                api_key=api_key,
                clear_api_key=clear_api_key,
            )

    async def _update_unlocked(
        self,
        config: BaseModel,
        *,
        api_key: str | None = None,
        clear_api_key: bool = False,
    ) -> BaseModel:
        if clear_api_key and api_key is not None and api_key.strip():
            raise ValueError("cannot set and clear a TTS API key in one update")
        provider_id = _config_provider_id(config)
        registration = self._registrations.get(provider_id)
        if registration is None or not isinstance(config, registration.configuration_type):
            raise ValueError(f"unregistered TTS configuration: {provider_id}")
        if (
            clear_api_key or (api_key is not None and api_key.strip())
        ) and not registration.has_api_key:
            raise ValueError(f"TTS provider does not accept an API key: {provider_id}")
        row = await self._database.fetchone(
            "SELECT updated_at FROM tts_provider_configs WHERE provider_id = ?",
            (provider_id,),
        )
        if row is None:
            raise RuntimeError("registered TTS configuration was not initialized")
        previous_secret = await _run_blocking_atomically(lambda: self._secrets.get(provider_id))
        next_secret = previous_secret
        if clear_api_key:
            next_secret = None
        elif api_key is not None and api_key.strip():
            next_secret = api_key.strip()
        mutation = _TtsSecretMutation(
            previous_secret=previous_secret,
            next_secret=next_secret,
            previous_updated_at=str(row["updated_at"]),
            next_updated_at=datetime.now(UTC).isoformat(),
        )
        secret_changed = mutation.next_secret != mutation.previous_secret
        if secret_changed:
            await _run_blocking_atomically(
                lambda: self._secret_mutations.prepare(provider_id, mutation)
            )
            try:
                await _run_blocking_atomically(
                    lambda: self._secrets.set(provider_id, mutation.next_secret)
                )
            except BaseException as secret_error:
                await self._compensate_secret_mutation(provider_id, mutation, secret_error)

        try:
            await self._persist_config(config, mutation.next_updated_at)
        except BaseException as database_error:
            if secret_changed:
                await self._compensate_secret_mutation(provider_id, mutation, database_error)
            raise

        await self._reload_unlocked()
        if secret_changed:
            await _run_blocking_atomically(lambda: self._secret_mutations.discard(provider_id))
        return self.get_for(provider_id)

    async def _persist_config(self, config: BaseModel, updated_at: str) -> None:
        provider_id = _config_provider_id(config)
        async with self._database.transaction() as connection:
            await connection.execute(
                """
                UPDATE tts_provider_configs SET
                    schema_version = ?, configuration_json = ?, updated_at = ?
                WHERE provider_id = ?
                """,
                (
                    self._registrations[provider_id].configuration_schema_version,
                    _serialize_configuration(config),
                    updated_at,
                    provider_id,
                ),
            )
            await self._mirror_legacy_configuration(
                connection,
                config,
                updated_at,
                insert_if_missing=False,
            )

    def _validate_configuration_values(
        self,
        registration: TtsProviderRegistration,
        values: dict[str, object],
        *,
        updated_at: datetime,
        api_key_configured: bool,
    ) -> BaseModel:
        hydrated = dict(values)
        hydrated["provider_id"] = registration.provider_id
        model_fields = registration.configuration_type.model_fields
        if "updated_at" in model_fields:
            hydrated["updated_at"] = updated_at
        if "api_key_configured" in model_fields:
            hydrated["api_key_configured"] = api_key_configured
        config = registration.configuration_type.model_validate(hydrated)
        if _config_provider_id(config) != registration.provider_id:
            raise RuntimeError("TTS configuration has a mismatched provider ID")
        return config

    def _legacy_configuration(
        self,
        registration: TtsProviderRegistration,
        row: dict[str, object],
    ) -> BaseModel:
        values = {
            key: value
            for key, value in row.items()
            if key in registration.configuration_type.model_fields
        }
        if "enabled" in values:
            values["enabled"] = bool(values["enabled"])
        updated_at = datetime.fromisoformat(str(row["updated_at"]))
        try:
            return self._validate_configuration_values(
                registration,
                values,
                updated_at=updated_at,
                api_key_configured=False,
            )
        except ValueError as error:
            raise RuntimeError(
                f"legacy TTS configuration is incompatible with {registration.provider_id}"
            ) from error

    async def _mirror_legacy_configuration(
        self,
        connection: aiosqlite.Connection,
        config: BaseModel,
        updated_at: str,
        *,
        insert_if_missing: bool,
    ) -> None:
        """Keep the pre-v2 Aliyun-shaped row usable during the transition.

        The JSON table is authoritative. This compatibility mirror is selected
        by field shape rather than provider ID and disappears once old readers
        are retired.
        """

        values = config.model_dump(mode="json")
        required = {
            "provider_id",
            "enabled",
            "model",
            "voice_id",
            "region",
            "workspace_id",
            "language_type",
            "sample_rate",
            "speech_rate",
            "volume",
            "pitch_rate",
            "instruction",
            "timeout_seconds",
            "max_audio_bytes",
        }
        if not required.issubset(values):
            return
        parameters = (
            str(values["provider_id"]),
            int(bool(values["enabled"])),
            str(values["model"]),
            str(values["voice_id"]),
            str(values["region"]),
            str(values["workspace_id"]).strip(),
            str(values["language_type"]),
            int(cast(int, values["sample_rate"])),
            float(cast(float, values["speech_rate"])),
            int(cast(int, values["volume"])),
            float(cast(float, values["pitch_rate"])),
            str(values["instruction"]),
            float(cast(float, values["timeout_seconds"])),
            int(cast(int, values["max_audio_bytes"])),
            updated_at,
        )
        if insert_if_missing:
            await connection.execute(
                """
                INSERT OR IGNORE INTO tts_cloud_configs(
                    provider_id, enabled, model, voice_id, region, workspace_id,
                    language_type, sample_rate, speech_rate, volume, pitch_rate,
                    instruction, timeout_seconds, max_audio_bytes, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                parameters,
            )
            return
        await connection.execute(
            """
            UPDATE tts_cloud_configs SET
                enabled = ?, model = ?, voice_id = ?, region = ?, workspace_id = ?,
                language_type = ?, sample_rate = ?, speech_rate = ?, volume = ?,
                pitch_rate = ?, instruction = ?, timeout_seconds = ?,
                max_audio_bytes = ?, updated_at = ?
            WHERE provider_id = ?
            """,
            (*parameters[1:], parameters[0]),
        )

    async def _recover_secret_mutations(self) -> None:
        for provider_id in self._registrations:
            mutation = await _run_blocking_atomically(
                lambda provider_id=provider_id: self._secret_mutations.get(provider_id)
            )
            if mutation is None:
                continue
            row = await self._database.fetchone(
                "SELECT updated_at FROM tts_provider_configs WHERE provider_id = ?",
                (provider_id,),
            )
            if row is None:
                raise RuntimeError("TTS secret mutation references a missing provider")
            current_updated_at = str(row["updated_at"])
            if current_updated_at == mutation.next_updated_at:
                target = mutation.next_secret
            elif current_updated_at == mutation.previous_updated_at:
                target = mutation.previous_secret
            else:
                raise RuntimeError(
                    "TTS secret mutation journal does not match durable configuration"
                )
            await _run_blocking_atomically(
                lambda provider_id=provider_id, target=target: self._secrets.set(
                    provider_id, target
                )
            )
            await _run_blocking_atomically(
                lambda provider_id=provider_id: self._secret_mutations.discard(provider_id)
            )

    async def _compensate_secret_mutation(
        self,
        provider_id: str,
        mutation: _TtsSecretMutation,
        primary: BaseException,
    ) -> None:
        try:
            await _run_blocking_atomically(
                lambda: self._secrets.set(provider_id, mutation.previous_secret)
            )
            await _run_blocking_atomically(lambda: self._secret_mutations.discard(provider_id))
        except BaseException as compensation_error:
            raise BaseExceptionGroup(
                "TTS configuration mutation and secret compensation both failed",
                [primary, compensation_error],
            ) from None
        raise primary


def _instruction_units(value: str) -> int:
    return sum(2 if "\u2e80" <= character <= "\u9fff" else 1 for character in value)


def _config_provider_id(config: BaseModel) -> str:
    provider_id = config.model_dump().get("provider_id")
    if not isinstance(provider_id, str) or not provider_id:
        raise ValueError("TTS configuration provider_id must be a non-empty string")
    return provider_id


def _config_updated_at(config: BaseModel, fallback: datetime) -> datetime:
    value = config.model_dump().get("updated_at")
    return value if isinstance(value, datetime) else fallback


def _serialize_configuration(config: BaseModel) -> str:
    excluded = {
        field
        for field in config.__class__.model_fields
        if field == "updated_at" or field.endswith("_configured")
    }
    return json.dumps(
        config.model_dump(mode="json", exclude=excluded),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _parse_configuration(serialized: str) -> dict[str, object]:
    try:
        value: object = json.loads(serialized)
    except json.JSONDecodeError as error:
        raise RuntimeError("TTS configuration JSON is corrupt") from error
    if not isinstance(value, dict):
        raise RuntimeError("TTS configuration JSON must be an object")
    document = cast(dict[object, object], value)
    if not all(isinstance(key, str) for key in document):
        raise RuntimeError("TTS configuration JSON keys must be strings")
    return cast(dict[str, object], document)


async def _run_blocking_atomically[T](operation: Callable[[], T]) -> T:
    """Keep an atomic file operation inside its caller's async critical section.

    Cancelling ``asyncio.to_thread`` does not stop its worker thread. Await the
    underlying operation before propagating cancellation so a stale journal
    write cannot escape the service lock and overwrite a later mutation.
    """

    task = asyncio.create_task(asyncio.to_thread(operation))
    try:
        return await asyncio.shield(task)
    except asyncio.CancelledError as cancellation:
        try:
            await task
        except BaseException as operation_error:
            raise BaseExceptionGroup(
                "TTS configuration cancellation and atomic file operation both failed",
                [cancellation, operation_error],
            ) from None
        raise
