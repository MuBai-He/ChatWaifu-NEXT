"""Domain service for runtime-persisted realtime configuration and local secrets."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Literal, Self
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, model_validator

from chatwaifu_runtime.config.settings import Settings
from chatwaifu_runtime.persistence.atomic_secret_store import AtomicSecretStore, SecretStoreError
from chatwaifu_runtime.persistence.database import Database
from chatwaifu_runtime.persistence.sqlite_realtime_config import (
    CloudBackend,
    ConnectionMode,
    RealtimeConfigurationRecord,
    SQLiteRealtimeConfigurationRepository,
)

logger = logging.getLogger(__name__)


class RealtimeConfigurationError(RuntimeError):
    """Base error for realtime configuration operations."""


class RealtimeRevisionConflictError(RealtimeConfigurationError):
    """Raised when an update expected_revision does not match the stored revision."""


@dataclass(frozen=True, slots=True)
class RealtimeConnectionSnapshot:
    """Immutable coherent snapshot of realtime configuration for readers and connections."""

    schema_version: str
    revision: int
    connection_mode: ConnectionMode
    cloud_backend: CloudBackend
    model: str
    voice: str
    transcription_model: str
    cloud_tools_enabled: bool
    cloud_egress_consent: bool
    api_key_configured: bool
    _api_key: str | None = field(default=None, repr=False)

    @property
    def api_key(self) -> str | None:
        return self._api_key

    def __repr__(self) -> str:
        return (
            f"RealtimeConnectionSnapshot(revision={self.revision}, "
            f"connection_mode={self.connection_mode!r}, "
            f"cloud_backend={self.cloud_backend!r}, "
            f"model={self.model!r}, "
            f"voice={self.voice!r}, "
            f"transcription_model={self.transcription_model!r}, "
            f"cloud_tools_enabled={self.cloud_tools_enabled}, "
            f"cloud_egress_consent={self.cloud_egress_consent}, "
            f"api_key_configured={self.api_key_configured})"
        )

    def to_public_dict(self, active_connections: int = 0) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "revision": self.revision,
            "connection_mode": self.connection_mode,
            "cloud_backend": self.cloud_backend,
            "model": self.model,
            "voice": self.voice,
            "transcription_model": self.transcription_model,
            "cloud_tools_enabled": self.cloud_tools_enabled,
            "cloud_egress_consent": self.cloud_egress_consent,
            "api_key_configured": self.api_key_configured,
            "active_connections": active_connections,
        }


class RealtimeConfigurationUpdateRequest(BaseModel):
    """Validated body for PUT /v1/realtime/configuration."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["1.0"] = "1.0"
    expected_revision: int = Field(ge=0)
    connection_mode: ConnectionMode
    cloud_backend: Literal["openai"] = "openai"
    model: str = Field(default="", max_length=256)
    voice: str = Field(default="marin", min_length=1, max_length=256)
    transcription_model: str = Field(default="gpt-4o-mini-transcribe", min_length=1, max_length=256)
    cloud_tools_enabled: bool = False
    cloud_egress_consent: bool = False
    api_key: str | None = Field(default=None, max_length=1024, repr=False)
    clear_api_key: bool = False

    @model_validator(mode="after")
    def validate_api_key_flags(self) -> Self:
        if self.clear_api_key and self.api_key is not None and self.api_key.strip():
            raise ValueError("cannot specify both clear_api_key=True and a non-empty api_key")
        return self


class RealtimeConfigurationResponse(BaseModel):
    """Standard representation returned by GET and PUT /v1/realtime/configuration."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["1.0"]
    revision: int
    connection_mode: ConnectionMode
    cloud_backend: CloudBackend
    model: str
    voice: str
    transcription_model: str
    cloud_tools_enabled: bool
    cloud_egress_consent: bool
    api_key_configured: bool
    active_connections: int


class RealtimeConfigurationService:
    """Manages versioned realtime configuration in SQLite and write-only local secrets."""

    def __init__(
        self,
        database: Database,
        settings: Settings,
        *,
        secret_store: AtomicSecretStore | None = None,
        repository: SQLiteRealtimeConfigurationRepository | None = None,
    ) -> None:
        self._database = database
        self._settings = settings
        self._secret_store = secret_store or AtomicSecretStore(
            settings.config_dir / "realtime-secrets.json"
        )
        self._repository = repository or SQLiteRealtimeConfigurationRepository(database)
        self._mutation_lock = asyncio.Lock()
        self._current_snapshot: RealtimeConnectionSnapshot | None = None

    @property
    def secret_store_path(self) -> str:
        return str(getattr(self._secret_store, "_path", ""))

    def current_snapshot(self) -> RealtimeConnectionSnapshot:
        if self._current_snapshot is None:
            raise RealtimeConfigurationError("RealtimeConfigurationService is not started")
        return self._current_snapshot

    async def start(self) -> None:
        async with self._mutation_lock:
            record = await self._repository.get_configuration()
            if record is not None:
                api_key: str | None = None
                if record.secret_key_ref:
                    try:
                        api_key = self._secret_store.get(record.secret_key_ref)
                    except SecretStoreError as err:
                        logger.error(
                            "Failed to read secret %s (failing closed): %s",
                            record.secret_key_ref,
                            err,
                        )
                        api_key = None
                self._current_snapshot = RealtimeConnectionSnapshot(
                    schema_version=record.schema_version,
                    revision=record.revision,
                    connection_mode=record.connection_mode,
                    cloud_backend=record.cloud_backend,
                    model=record.model,
                    voice=record.voice,
                    transcription_model=record.transcription_model,
                    cloud_tools_enabled=record.cloud_tools_enabled,
                    cloud_egress_consent=record.cloud_egress_consent,
                    api_key_configured=bool(api_key),
                    _api_key=api_key,
                )
                return

            # Bootstrap from Settings ONLY if no saved record exists in SQLite
            now = datetime.now(UTC).isoformat()
            rt_settings = self._settings.realtime
            connection_mode: ConnectionMode = rt_settings.connection_mode
            cloud_backend: CloudBackend = rt_settings.cloud_backend or "openai"
            model = rt_settings.openai.model or ""
            voice = rt_settings.openai.voice or "marin"
            transcription_model = rt_settings.openai.transcription_model or "gpt-4o-mini-transcribe"
            cloud_tools_enabled = rt_settings.cloud_tools_enabled
            cloud_egress_consent = self._settings.privacy.cloud_egress == "allow"

            api_key = None
            secret_key_ref = None
            if rt_settings.openai.api_key:
                raw_key = rt_settings.openai.api_key.get_secret_value().strip()
                if raw_key:
                    api_key = raw_key
                    secret_key_ref = f"openai_key_r1_{uuid4().hex[:8]}"
                    try:
                        self._secret_store.set(secret_key_ref, api_key)
                    except SecretStoreError as err:
                        logger.error("Failed to write initial secret (failing closed): %s", err)
                        api_key = None
                        secret_key_ref = None

            initial_record = RealtimeConfigurationRecord(
                id="default",
                schema_version="1.0",
                revision=1,
                connection_mode=connection_mode,
                cloud_backend=cloud_backend,
                model=model,
                voice=voice,
                transcription_model=transcription_model,
                cloud_tools_enabled=cloud_tools_enabled,
                cloud_egress_consent=cloud_egress_consent,
                secret_key_ref=secret_key_ref,
                updated_at=now,
            )
            await self._repository.insert_configuration(initial_record)
            self._current_snapshot = RealtimeConnectionSnapshot(
                schema_version="1.0",
                revision=1,
                connection_mode=connection_mode,
                cloud_backend=cloud_backend,
                model=model,
                voice=voice,
                transcription_model=transcription_model,
                cloud_tools_enabled=cloud_tools_enabled,
                cloud_egress_consent=cloud_egress_consent,
                api_key_configured=bool(api_key),
                _api_key=api_key,
            )

    async def update_configuration(
        self, update: RealtimeConfigurationUpdateRequest
    ) -> RealtimeConnectionSnapshot:
        async with self._mutation_lock:
            operation = asyncio.create_task(self._update_locked(update))
            try:
                return await asyncio.shield(operation)
            except asyncio.CancelledError:
                # Once storage mutation begins, finish publication while retaining
                # the writer lock. Cancellation cannot undo an already committed CAS.
                while not operation.done():
                    try:
                        await asyncio.shield(operation)
                    except asyncio.CancelledError:
                        continue
                    except Exception:
                        break
                if not operation.cancelled():
                    operation.exception()
                raise

    async def _update_locked(
        self, update: RealtimeConfigurationUpdateRequest
    ) -> RealtimeConnectionSnapshot:
        current_record = await self._repository.get_configuration()
        if current_record is None:
            raise RealtimeConfigurationError("realtime configuration record missing")

        if update.expected_revision != current_record.revision:
            raise RealtimeRevisionConflictError(
                f"stale configuration revision: expected {update.expected_revision}, "
                f"current is {current_record.revision}"
            )

        new_revision = current_record.revision + 1
        new_key_ref: str | None = None
        new_api_key: str | None = None

        try:
            if update.clear_api_key:
                new_key_ref = None
                new_api_key = None
            elif update.api_key is not None and update.api_key.strip():
                new_api_key = update.api_key.strip()
                new_key_ref = f"openai_key_r{new_revision}_{uuid4().hex[:8]}"
                # Write to secret store BEFORE committing DB CAS
                self._secret_store.set(new_key_ref, new_api_key)
            else:
                # Blank / null api_key keeps old
                new_key_ref = current_record.secret_key_ref
                new_api_key = self._secret_store.get(new_key_ref) if new_key_ref else None
        except (SecretStoreError, OSError) as err:
            raise RealtimeConfigurationError(
                "无法安全保存语音配置密钥，请检查本地配置目录权限。"
            ) from err

        now = datetime.now(UTC).isoformat()
        new_record = RealtimeConfigurationRecord(
            id="default",
            schema_version="1.0",
            revision=new_revision,
            connection_mode=update.connection_mode,
            cloud_backend=update.cloud_backend,
            model=update.model.strip(),
            voice=update.voice.strip(),
            transcription_model=update.transcription_model.strip(),
            cloud_tools_enabled=update.cloud_tools_enabled,
            cloud_egress_consent=update.cloud_egress_consent,
            secret_key_ref=new_key_ref,
            updated_at=now,
        )

        success = await self._repository.update_configuration_cas(
            current_record.revision, new_record
        )
        if not success:
            # Retain immutable candidates after uncertain storage outcomes. A
            # later successful mutation can prune them against its durable ref.
            raise RealtimeRevisionConflictError("stale configuration revision, reload required")

        self._current_snapshot = RealtimeConnectionSnapshot(
            schema_version="1.0",
            revision=new_record.revision,
            connection_mode=new_record.connection_mode,
            cloud_backend=new_record.cloud_backend,
            model=new_record.model,
            voice=new_record.voice,
            transcription_model=new_record.transcription_model,
            cloud_tools_enabled=new_record.cloud_tools_enabled,
            cloud_egress_consent=new_record.cloud_egress_consent,
            api_key_configured=bool(new_api_key),
            _api_key=new_api_key,
        )
        try:
            self._secret_store.prune({new_key_ref} if new_key_ref else set())
        except (SecretStoreError, OSError):
            logger.warning("Realtime configuration saved; obsolete secret cleanup deferred")
        return self._current_snapshot
