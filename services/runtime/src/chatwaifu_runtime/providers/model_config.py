"""Runtime-persisted, role-scoped model configuration and provider routing."""

from __future__ import annotations

import hashlib
import json
import math
import os
from collections.abc import AsyncIterator, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal, cast
from uuid import uuid4

import httpx2
from pydantic import BaseModel, ConfigDict, Field, model_validator

from chatwaifu_runtime.config.settings import Settings
from chatwaifu_runtime.persistence.database import Database
from chatwaifu_runtime.providers.contracts import LlmProvider, LlmRequest
from chatwaifu_runtime.providers.demo_llm import DemoLlmProvider
from chatwaifu_runtime.providers.openai_compatible import (
    OpenAiCompatibleLlmProvider,
    openai_compatible_endpoint,
)

type ModelRole = Literal["chat", "memory_extraction", "memory_summary", "embedding"]
type ModelProviderKind = Literal["demo", "openai_compatible", "local_hash", "disabled"]

MODEL_ROLES: tuple[ModelRole, ...] = (
    "chat",
    "memory_extraction",
    "memory_summary",
    "embedding",
)


class ModelRoleConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    role: ModelRole
    provider: ModelProviderKind
    model: str = Field(min_length=1, max_length=256)
    base_url: str = Field(default="", max_length=2048)
    timeout_seconds: float = Field(default=60, gt=0, le=600)
    context_window: int = Field(default=8192, ge=1024, le=2_000_000)
    enabled: bool = True
    api_key_configured: bool = False
    updated_at: datetime

    @model_validator(mode="after")
    def validate_role_provider(self) -> ModelRoleConfig:
        if self.role == "embedding" and self.provider == "demo":
            raise ValueError("embedding role uses local_hash instead of demo")
        if self.role != "embedding" and self.provider == "local_hash":
            raise ValueError("local_hash is only valid for the embedding role")
        if self.provider == "openai_compatible" and not self.base_url:
            raise ValueError("openai_compatible provider requires base_url")
        if self.provider == "openai_compatible" and not self.base_url.startswith(
            ("http://", "https://")
        ):
            raise ValueError("openai_compatible base_url must use http or https")
        return self


class LocalModelSecretStore:
    """Write-only-through-HTTP local secret storage; never returned to the browser."""

    def __init__(self, path: Path) -> None:
        self._path = path

    def get(self, role: ModelRole) -> str | None:
        value = self._read().get(role)
        return value if isinstance(value, str) and value else None

    def set(self, role: ModelRole, value: str | None) -> None:
        secrets = self._read()
        if value:
            secrets[role] = value
        else:
            secrets.pop(role, None)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self._path.with_name(f".{self._path.name}.tmp")
        payload = json.dumps(secrets, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
        descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as secret_file:
                secret_file.write(payload)
                secret_file.flush()
                os.fsync(secret_file.fileno())
            os.replace(temporary, self._path)
            os.chmod(self._path, 0o600)
        finally:
            if temporary.exists():
                temporary.unlink()

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


class ModelConfigurationService:
    def __init__(self, database: Database, settings: Settings) -> None:
        self._database = database
        self._settings = settings
        self._secrets = LocalModelSecretStore(settings.config_dir / "model-secrets.json")
        self._configs: dict[ModelRole, ModelRoleConfig] = {}
        self.chat = ConfigurableChatModel(self)

    async def start(self) -> None:
        now = datetime.now(UTC)
        defaults = self._defaults(now)
        async with self._database.transaction() as connection:
            for role, config in defaults.items():
                await connection.execute(
                    """
                    INSERT OR IGNORE INTO model_role_configs(
                        role, provider, model, base_url, timeout_seconds,
                        context_window, enabled, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        role,
                        config.provider,
                        config.model,
                        config.base_url,
                        config.timeout_seconds,
                        config.context_window,
                        int(config.enabled),
                        now.isoformat(),
                    ),
                )
        await self.reload()

    async def reload(self) -> None:
        rows = await self._database.fetchall(
            """
            SELECT role, provider, model, base_url, timeout_seconds,
                   context_window, enabled, updated_at
            FROM model_role_configs ORDER BY role
            """
        )
        configs: dict[ModelRole, ModelRoleConfig] = {}
        for row in rows:
            role = cast(ModelRole, str(row["role"]))
            config = ModelRoleConfig.model_validate(
                {
                    "role": role,
                    "provider": str(row["provider"]),
                    "model": str(row["model"]),
                    "base_url": str(row["base_url"]),
                    "timeout_seconds": float(row["timeout_seconds"]),
                    "context_window": int(row["context_window"]),
                    "enabled": bool(row["enabled"]),
                    "api_key_configured": self._secrets.get(role) is not None,
                    "updated_at": datetime.fromisoformat(str(row["updated_at"])),
                }
            )
            configs[config.role] = config
        self._configs = configs

    @property
    def started(self) -> bool:
        return bool(self._configs)

    def get(self, role: ModelRole) -> ModelRoleConfig:
        return self._configs[role]

    def list(self) -> list[ModelRoleConfig]:
        return [self._configs[role] for role in MODEL_ROLES]

    async def update(
        self,
        config: ModelRoleConfig,
        *,
        api_key: str | None = None,
        clear_api_key: bool = False,
    ) -> ModelRoleConfig:
        now = datetime.now(UTC)
        async with self._database.transaction() as connection:
            await connection.execute(
                """
                INSERT INTO model_role_configs(
                    role, provider, model, base_url, timeout_seconds,
                    context_window, enabled, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(role) DO UPDATE SET
                    provider = excluded.provider,
                    model = excluded.model,
                    base_url = excluded.base_url,
                    timeout_seconds = excluded.timeout_seconds,
                    context_window = excluded.context_window,
                    enabled = excluded.enabled,
                    updated_at = excluded.updated_at
                """,
                (
                    config.role,
                    config.provider,
                    config.model,
                    config.base_url.rstrip("/"),
                    config.timeout_seconds,
                    config.context_window,
                    int(config.enabled),
                    now.isoformat(),
                ),
            )
        if clear_api_key:
            self._secrets.set(config.role, None)
        elif api_key is not None and api_key.strip():
            self._secrets.set(config.role, api_key.strip())
        await self.reload()
        return self.get(config.role)

    async def complete(
        self, role: Literal["memory_extraction", "memory_summary"], system: str, user: str
    ) -> str:
        config = self.get(role)
        if not config.enabled or config.provider == "disabled":
            return ""
        if config.provider == "demo":
            if role == "memory_extraction":
                return '{"memories": []}'
            compact = " ".join(user.split())
            return compact[-1200:]
        if config.provider != "openai_compatible":
            raise RuntimeError(f"unsupported completion provider for {role}: {config.provider}")
        headers = {"Content-Type": "application/json"}
        if key := self._secrets.get(role):
            headers["Authorization"] = f"Bearer {key}"
        async with httpx2.AsyncClient(timeout=config.timeout_seconds) as client:
            response = await client.post(
                openai_compatible_endpoint(config.base_url, "chat/completions"),
                headers=headers,
                json={
                    "model": config.model,
                    "messages": [
                        {"role": "system", "content": system},
                        {"role": "user", "content": user},
                    ],
                    "stream": False,
                    "temperature": 0,
                },
            )
            response.raise_for_status()
            payload = cast(object, response.json())
        choices: list[object] = []
        if isinstance(payload, dict):
            raw_choices = cast(dict[str, object], payload).get("choices")
            if isinstance(raw_choices, list):
                choices = cast(list[object], raw_choices)
        if not choices:
            raise RuntimeError(f"{role} model returned no choices")
        first = choices[0]
        if not isinstance(first, dict):
            raise RuntimeError(f"{role} model returned invalid choice")
        message = cast(dict[str, object], first).get("message")
        if not isinstance(message, dict):
            raise RuntimeError(f"{role} model returned invalid message")
        content = cast(dict[str, object], message).get("content")
        if not isinstance(content, str):
            raise RuntimeError(f"{role} model returned invalid content")
        return content

    async def embed(self, texts: Sequence[str]) -> list[list[float]]:
        config = self.get("embedding")
        if not config.enabled or config.provider == "disabled":
            return []
        if config.provider == "local_hash":
            return [_hash_embedding(text) for text in texts]
        if config.provider != "openai_compatible":
            raise RuntimeError(f"unsupported embedding provider: {config.provider}")
        headers = {"Content-Type": "application/json"}
        if key := self._secrets.get("embedding"):
            headers["Authorization"] = f"Bearer {key}"
        async with httpx2.AsyncClient(timeout=config.timeout_seconds) as client:
            response = await client.post(
                openai_compatible_endpoint(config.base_url, "embeddings"),
                headers=headers,
                json={"model": config.model, "input": list(texts)},
            )
            response.raise_for_status()
            payload = cast(object, response.json())
        raw_data: list[object] = []
        if isinstance(payload, dict):
            value = cast(dict[str, object], payload).get("data")
            if isinstance(value, list):
                raw_data = cast(list[object], value)
        data = [cast(dict[str, object], item) for item in raw_data if isinstance(item, dict)]
        ordered = sorted(data, key=lambda item: int(cast(Any, item.get("index", 0))))
        vectors = [item.get("embedding") for item in ordered]
        if len(vectors) != len(texts) or not all(isinstance(vector, list) for vector in vectors):
            raise RuntimeError("embedding provider returned invalid vector count")
        return [
            [float(cast(Any, value)) for value in cast(list[object], vector)] for vector in vectors
        ]

    def embedding_fingerprint(self) -> str:
        config = self.get("embedding")
        return f"{config.provider}:{config.model}"

    async def probe(self, role: ModelRole) -> dict[str, object]:
        if role == "embedding":
            vectors = await self.embed(["ChatWaifu model configuration probe"])
            if not vectors:
                return {"status": "disabled", "dimensions": 0}
            return {"status": "ok", "dimensions": len(vectors[0])}
        if role == "memory_extraction" or role == "memory_summary":
            result = await self.complete(
                role,
                "Return a compact valid response for a connectivity probe.",
                "probe",
            )
            return {"status": "ok" if result else "disabled", "characters": len(result)}
        provider = self.chat_provider()
        chunks: list[str] = []
        async for chunk in provider.stream(
            LlmRequest(
                generation_id=uuid4(),
                user_text="Reply with OK.",
                system_prompt="This is a connectivity probe.",
            )
        ):
            chunks.append(chunk)
            if sum(len(item) for item in chunks) >= 256:
                break
        return {"status": "ok", "characters": sum(len(item) for item in chunks)}

    def chat_provider(self) -> LlmProvider:
        config = self.get("chat")
        if not config.enabled or config.provider in {"disabled", "local_hash"}:
            return DemoLlmProvider(self._settings.llm.demo_chunk_delay_ms)
        if config.provider == "demo":
            return DemoLlmProvider(self._settings.llm.demo_chunk_delay_ms)
        return OpenAiCompatibleLlmProvider(
            base_url=config.base_url,
            model=config.model,
            api_key=self._secrets.get("chat"),
            timeout_seconds=config.timeout_seconds,
        )

    def _defaults(self, now: datetime) -> dict[ModelRole, ModelRoleConfig]:
        llm = self._settings.llm
        chat_provider: ModelProviderKind = (
            "openai_compatible" if llm.provider == "openai_compatible" else "demo"
        )
        if llm.api_key and self._secrets.get("chat") is None:
            self._secrets.set("chat", llm.api_key.get_secret_value())
        return {
            "chat": ModelRoleConfig(
                role="chat",
                provider=chat_provider,
                model=llm.model,
                base_url=llm.base_url,
                api_key_configured=self._secrets.get("chat") is not None,
                timeout_seconds=llm.timeout_seconds,
                context_window=8192,
                updated_at=now,
            ),
            "memory_extraction": ModelRoleConfig(
                role="memory_extraction",
                provider="demo",
                model="deterministic-memory-v1",
                base_url="",
                timeout_seconds=llm.timeout_seconds,
                context_window=8192,
                updated_at=now,
            ),
            "memory_summary": ModelRoleConfig(
                role="memory_summary",
                provider="demo",
                model="deterministic-summary-v1",
                base_url="",
                timeout_seconds=llm.timeout_seconds,
                context_window=8192,
                updated_at=now,
            ),
            "embedding": ModelRoleConfig(
                role="embedding",
                provider="local_hash",
                model="local-hash-64-v1",
                base_url="",
                timeout_seconds=30,
                context_window=8192,
                updated_at=now,
            ),
        }


class ConfigurableChatModel:
    def __init__(self, configurations: ModelConfigurationService) -> None:
        self._configurations = configurations

    @property
    def kind(self) -> str:
        if not self._configurations.started:
            return "configurable"
        return self._configurations.get("chat").provider

    def stream(self, request: LlmRequest) -> AsyncIterator[str]:
        return self._configurations.chat_provider().stream(request)


def _hash_embedding(text: str, dimensions: int = 64) -> list[float]:
    vector = [0.0] * dimensions
    normalized = "".join(character.casefold() for character in text if not character.isspace())
    tokens = [normalized[index : index + 2] for index in range(max(1, len(normalized) - 1))]
    for token in tokens:
        digest = hashlib.blake2b(token.encode("utf-8"), digest_size=8).digest()
        slot = int.from_bytes(digest[:4], "big") % dimensions
        vector[slot] += -1.0 if digest[4] & 1 else 1.0
    norm = math.sqrt(sum(value * value for value in vector)) or 1.0
    return [value / norm for value in vector]
