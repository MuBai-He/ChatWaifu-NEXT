"""Persistent MCP Host connections, capability discovery, and resource access."""

from __future__ import annotations

import asyncio
import json
import os
import shutil
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import cast
from uuid import UUID

from chatwaifu_protocol.base import JsonObject
from chatwaifu_protocol.skills import (
    McpCapabilitySnapshot,
    McpConnectionConfiguration,
    McpConnectionSnapshot,
    McpPromptDescriptor,
    McpResourceDescriptor,
    McpResourceTemplateDescriptor,
    McpToolDescriptor,
)
from mcp import ClientSession
from mcp.types import PaginatedRequestParams

from chatwaifu_runtime.persistence.database import Database
from chatwaifu_runtime.runtime_skills.errors import SkillExecutionError
from chatwaifu_runtime.runtime_skills.transports import McpClientTransport

MAX_DISCOVERED_ITEMS = 2_000


class McpConnectionSecretStore:
    """Mode-0600 bearer-token storage that is write-only through the HTTP API."""

    def __init__(self, path: Path) -> None:
        self._path = path

    def get(self, connection_id: UUID) -> str | None:
        value = self._read().get(str(connection_id))
        return value if isinstance(value, str) and value else None

    def set(self, connection_id: UUID, value: str | None) -> None:
        secrets = self._read()
        if value:
            secrets[str(connection_id)] = value
        else:
            secrets.pop(str(connection_id), None)
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
        except (json.JSONDecodeError, OSError) as error:
            raise SkillExecutionError(
                "mcp_secret_store_corrupt",
                "MCP secret storage is unreadable; refusing to overwrite it",
            ) from error
        if not isinstance(value, dict):
            raise SkillExecutionError(
                "mcp_secret_store_corrupt",
                "MCP secret storage has an invalid format; refusing to overwrite it",
            )
        typed = cast(dict[object, object], value)
        if any(
            not isinstance(key, str) or not isinstance(item, str) for key, item in typed.items()
        ):
            raise SkillExecutionError(
                "mcp_secret_store_corrupt",
                "MCP secret storage has invalid entries; refusing to overwrite it",
            )
        return cast(dict[str, str], typed)


class McpConnectionManager:
    def __init__(
        self,
        database: Database,
        data_root: Path,
        transport: McpClientTransport,
    ) -> None:
        self._database = database
        self._root = data_root / "mcp-connections"
        self._secrets = McpConnectionSecretStore(data_root / "mcp-secrets.json")
        self._transport = transport

    async def start(self) -> None:
        await asyncio.to_thread(self._root.mkdir, parents=True, exist_ok=True)

    async def list(self) -> list[McpConnectionSnapshot]:
        rows = await self._database.fetchall(
            "SELECT * FROM mcp_connections ORDER BY name COLLATE NOCASE, connection_id"
        )
        return [self._snapshot(row) for row in rows]

    async def get(self, connection_id: UUID) -> McpConnectionSnapshot:
        row = await self._database.fetchone(
            "SELECT * FROM mcp_connections WHERE connection_id = ?", (str(connection_id),)
        )
        if row is None:
            raise KeyError("MCP connection not found")
        return self._snapshot(row)

    async def create(
        self,
        config: McpConnectionConfiguration,
        *,
        bearer_token: str | None = None,
    ) -> McpConnectionSnapshot:
        if bearer_token and bearer_token.strip():
            await asyncio.to_thread(self._secrets.get, config.connection_id)
        now = _now()
        capabilities = _empty_capabilities(config.connection_id)
        async with self._database.transaction() as connection:
            await connection.execute(
                """
                INSERT INTO mcp_connections(
                    connection_id, name, transport, command_json, url, allow_remote,
                    enabled, timeout_seconds, trust_level, sandbox_mode, network_policy,
                    bearer_token_configured, status, capabilities_json, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    str(config.connection_id),
                    config.name,
                    config.transport,
                    json.dumps(config.command, ensure_ascii=False),
                    config.url,
                    int(config.allow_remote),
                    int(config.enabled),
                    config.timeout_seconds,
                    config.trust_level,
                    config.sandbox_mode,
                    config.network_policy,
                    int(bool(bearer_token and bearer_token.strip())),
                    "untested" if config.enabled else "disabled",
                    capabilities.model_dump_json(),
                    now.isoformat(),
                    now.isoformat(),
                ),
            )
        if bearer_token and bearer_token.strip():
            await asyncio.to_thread(self._secrets.set, config.connection_id, bearer_token.strip())
        return await self.get(config.connection_id)

    async def update(
        self,
        config: McpConnectionConfiguration,
        *,
        bearer_token: str | None = None,
        clear_bearer_token: bool = False,
    ) -> McpConnectionSnapshot:
        if clear_bearer_token or (bearer_token is not None and bearer_token.strip()):
            await asyncio.to_thread(self._secrets.get, config.connection_id)
        current = await self.get(config.connection_id)
        now = _now()
        configured = current.bearer_token_configured
        if clear_bearer_token:
            configured = False
        elif bearer_token is not None and bearer_token.strip():
            configured = True
        async with self._database.transaction() as connection:
            cursor = await connection.execute(
                """
                UPDATE mcp_connections SET
                    name = ?, transport = ?, command_json = ?, url = ?, allow_remote = ?,
                    enabled = ?, timeout_seconds = ?, trust_level = ?, sandbox_mode = ?,
                    network_policy = ?, bearer_token_configured = ?, status = ?,
                    capabilities_json = ?, last_error = NULL, last_tested_at = NULL,
                    updated_at = ?
                WHERE connection_id = ?
                """,
                (
                    config.name,
                    config.transport,
                    json.dumps(config.command, ensure_ascii=False),
                    config.url,
                    int(config.allow_remote),
                    int(config.enabled),
                    config.timeout_seconds,
                    config.trust_level,
                    config.sandbox_mode,
                    config.network_policy,
                    int(configured),
                    "untested" if config.enabled else "disabled",
                    _empty_capabilities(config.connection_id).model_dump_json(),
                    now.isoformat(),
                    str(config.connection_id),
                ),
            )
            if cursor.rowcount != 1:
                raise KeyError("MCP connection not found")
        if clear_bearer_token:
            await asyncio.to_thread(self._secrets.set, config.connection_id, None)
        elif bearer_token is not None and bearer_token.strip():
            await asyncio.to_thread(self._secrets.set, config.connection_id, bearer_token.strip())
        return await self.get(config.connection_id)

    async def delete(self, connection_id: UUID) -> None:
        await asyncio.to_thread(self._secrets.get, connection_id)
        async with self._database.transaction() as connection:
            cursor = await connection.execute(
                "DELETE FROM mcp_connections WHERE connection_id = ?", (str(connection_id),)
            )
            if cursor.rowcount != 1:
                raise KeyError("MCP connection not found")
        await asyncio.to_thread(self._secrets.set, connection_id, None)
        working_root = self.working_root(connection_id)
        if await asyncio.to_thread(working_root.exists):
            await asyncio.to_thread(shutil.rmtree, working_root)

    async def test(self, connection_id: UUID) -> McpConnectionSnapshot:
        current = await self.get(connection_id)
        config = _configuration(current)
        try:
            capabilities = await self.discover(config)
        except asyncio.CancelledError:
            raise
        except Exception as error:
            message = (
                error.structured.message if isinstance(error, SkillExecutionError) else str(error)
            )
            now = _now()
            async with self._database.transaction() as connection:
                await connection.execute(
                    """
                    UPDATE mcp_connections SET status = 'error', last_error = ?,
                        last_tested_at = ?, updated_at = ? WHERE connection_id = ?
                    """,
                    (message[:2_000], now.isoformat(), now.isoformat(), str(connection_id)),
                )
            raise
        now = _now()
        async with self._database.transaction() as connection:
            await connection.execute(
                """
                UPDATE mcp_connections SET status = 'ready', capabilities_json = ?,
                    last_error = NULL, last_tested_at = ?, updated_at = ?
                WHERE connection_id = ?
                """,
                (
                    capabilities.model_dump_json(),
                    now.isoformat(),
                    now.isoformat(),
                    str(connection_id),
                ),
            )
        return await self.get(connection_id)

    async def discover(self, config: McpConnectionConfiguration) -> McpCapabilitySnapshot:
        token = await asyncio.to_thread(self._secrets.get, config.connection_id)
        try:
            async with asyncio.timeout(config.timeout_seconds):
                async with self._transport.connection_session(
                    config,
                    bearer_token=token,
                    working_root=self.working_root(config.connection_id),
                ) as (session, initialized):
                    tools = (
                        await _list_tools(session)
                        if initialized.capabilities.tools is not None
                        else []
                    )
                    resources = (
                        await _list_resources(session)
                        if initialized.capabilities.resources is not None
                        else []
                    )
                    resource_templates = (
                        await _list_resource_templates(session)
                        if initialized.capabilities.resources is not None
                        else []
                    )
                    prompts = (
                        await _list_prompts(session)
                        if initialized.capabilities.prompts is not None
                        else []
                    )
        except TimeoutError as error:
            raise SkillExecutionError(
                "mcp_connection_timeout",
                f"MCP connection exceeded {config.timeout_seconds:g}s timeout",
                retryable=True,
            ) from error
        except asyncio.CancelledError:
            raise
        except SkillExecutionError:
            raise
        except Exception as error:
            raise SkillExecutionError(
                "mcp_connection_failed",
                "MCP connection test failed",
                retryable=True,
                details={"exception_type": type(error).__name__},
            ) from error
        return McpCapabilitySnapshot(
            connection_id=config.connection_id,
            protocol_version=initialized.protocol_version,
            server_name=initialized.server_info.name,
            server_version=initialized.server_info.version,
            tools=tools,
            resources=resources,
            resource_templates=resource_templates,
            prompts=prompts,
            discovered_at=_now(),
        )

    async def read_resource(self, connection_id: UUID, uri: str) -> JsonObject:
        current = await self.get(connection_id)
        config = _configuration(current)
        token = await asyncio.to_thread(self._secrets.get, connection_id)
        try:
            async with asyncio.timeout(config.timeout_seconds):
                async with self._transport.connection_session(
                    config,
                    bearer_token=token,
                    working_root=self.working_root(connection_id),
                ) as (session, _):
                    result = await session.read_resource(uri)
        except asyncio.CancelledError:
            raise
        except Exception as error:
            raise _operation_error("read resource", error) from error
        return cast(JsonObject, result.model_dump(mode="json", by_alias=True, exclude_none=True))

    async def get_prompt(
        self, connection_id: UUID, name: str, arguments: dict[str, str]
    ) -> JsonObject:
        current = await self.get(connection_id)
        config = _configuration(current)
        token = await asyncio.to_thread(self._secrets.get, connection_id)
        try:
            async with asyncio.timeout(config.timeout_seconds):
                async with self._transport.connection_session(
                    config,
                    bearer_token=token,
                    working_root=self.working_root(connection_id),
                ) as (session, _):
                    result = await session.get_prompt(name, arguments)
        except asyncio.CancelledError:
            raise
        except Exception as error:
            raise _operation_error("get prompt", error) from error
        return cast(JsonObject, result.model_dump(mode="json", by_alias=True, exclude_none=True))

    def bearer_token(self, connection_id: UUID) -> str | None:
        return self._secrets.get(connection_id)

    def working_root(self, connection_id: UUID) -> Path:
        return self._root / str(connection_id)

    def _snapshot(self, row: object) -> McpConnectionSnapshot:
        values = cast(dict[str, object], row)
        connection_id = UUID(str(values["connection_id"]))
        raw_capabilities = json.loads(str(values["capabilities_json"]))
        capabilities = McpCapabilitySnapshot.model_validate(
            {"connection_id": connection_id, **raw_capabilities}
        )
        return McpConnectionSnapshot.model_validate(
            {
                "connection_id": connection_id,
                "name": values["name"],
                "transport": values["transport"],
                "command": json.loads(str(values["command_json"])),
                "url": values["url"],
                "allow_remote": bool(values["allow_remote"]),
                "enabled": bool(values["enabled"]),
                "timeout_seconds": float(cast(float | int | str, values["timeout_seconds"])),
                "trust_level": values["trust_level"],
                "sandbox_mode": values["sandbox_mode"],
                "network_policy": values["network_policy"],
                "status": values["status"],
                "bearer_token_configured": bool(values["bearer_token_configured"]),
                "sandbox_backend": values["sandbox_backend"],
                "capabilities": capabilities,
                "last_error": values["last_error"],
                "last_tested_at": values["last_tested_at"],
                "created_at": values["created_at"],
                "updated_at": values["updated_at"],
            }
        )


async def _list_tools(session: ClientSession) -> list[McpToolDescriptor]:
    items: list[McpToolDescriptor] = []
    cursor: str | None = None
    while True:
        result = await session.list_tools(
            params=PaginatedRequestParams(cursor=cursor) if cursor else None
        )
        items.extend(
            McpToolDescriptor(
                name=tool.name,
                title=tool.title,
                description=tool.description,
                input_schema=cast(JsonObject, tool.input_schema),
                output_schema=cast(JsonObject, tool.output_schema) if tool.output_schema else None,
            )
            for tool in result.tools
        )
        _check_item_limit(items)
        cursor = result.next_cursor
        if not cursor:
            return items


async def _list_resources(session: ClientSession) -> list[McpResourceDescriptor]:
    items: list[McpResourceDescriptor] = []
    cursor: str | None = None
    while True:
        result = await session.list_resources(
            params=PaginatedRequestParams(cursor=cursor) if cursor else None
        )
        items.extend(
            McpResourceDescriptor(
                uri=str(resource.uri),
                name=resource.name,
                title=resource.title,
                description=resource.description,
                mime_type=resource.mime_type,
            )
            for resource in result.resources
        )
        _check_item_limit(items)
        cursor = result.next_cursor
        if not cursor:
            return items


async def _list_resource_templates(
    session: ClientSession,
) -> list[McpResourceTemplateDescriptor]:
    items: list[McpResourceTemplateDescriptor] = []
    cursor: str | None = None
    while True:
        result = await session.list_resource_templates(
            params=PaginatedRequestParams(cursor=cursor) if cursor else None
        )
        items.extend(
            McpResourceTemplateDescriptor(
                uri_template=template.uri_template,
                name=template.name,
                title=template.title,
                description=template.description,
                mime_type=template.mime_type,
            )
            for template in result.resource_templates
        )
        _check_item_limit(items)
        cursor = result.next_cursor
        if not cursor:
            return items


async def _list_prompts(session: ClientSession) -> list[McpPromptDescriptor]:
    items: list[McpPromptDescriptor] = []
    cursor: str | None = None
    while True:
        result = await session.list_prompts(
            params=PaginatedRequestParams(cursor=cursor) if cursor else None
        )
        items.extend(
            McpPromptDescriptor(
                name=prompt.name,
                title=prompt.title,
                description=prompt.description,
                arguments=[
                    cast(JsonObject, argument.model_dump(mode="json", by_alias=True))
                    for argument in (prompt.arguments or [])
                ],
            )
            for prompt in result.prompts
        )
        _check_item_limit(items)
        cursor = result.next_cursor
        if not cursor:
            return items


def _check_item_limit(items: Sequence[object]) -> None:
    if len(items) > MAX_DISCOVERED_ITEMS:
        raise SkillExecutionError(
            "mcp_capability_limit", "MCP server exposed too many capabilities"
        )


def _configuration(snapshot: McpConnectionSnapshot) -> McpConnectionConfiguration:
    return McpConnectionConfiguration.model_validate(snapshot.model_dump(mode="python"))


def _empty_capabilities(connection_id: UUID) -> McpCapabilitySnapshot:
    return McpCapabilitySnapshot(connection_id=connection_id)


def _operation_error(operation: str, error: Exception) -> SkillExecutionError:
    if isinstance(error, SkillExecutionError):
        return error
    if isinstance(error, TimeoutError):
        return SkillExecutionError(
            "mcp_connection_timeout", f"MCP {operation} timed out", retryable=True
        )
    return SkillExecutionError(
        "mcp_operation_failed",
        f"MCP {operation} failed",
        retryable=True,
        details={"exception_type": type(error).__name__},
    )


def _now() -> datetime:
    return datetime.now(UTC)
