"""Persistent MCP Host connections, capability discovery, and resource access."""

from __future__ import annotations

import asyncio
import json
import os
import shutil
from collections.abc import AsyncGenerator, Awaitable, Callable, Sequence
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path
from threading import RLock
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

from chatwaifu_runtime.runtime_skills.errors import SkillExecutionError
from chatwaifu_runtime.runtime_skills.repository import McpConnectionRepository, Record
from chatwaifu_runtime.runtime_skills.transports import (
    McpClientTransport,
    enforce_mcp_json_payload_limit,
)

MAX_DISCOVERED_ITEMS = 2_000
McpConnectionMutationHook = Callable[[], Awaitable[None]]


class McpConnectionSecretStore:
    """Mode-0600 bearer-token storage that is write-only through the HTTP API."""

    def __init__(self, path: Path) -> None:
        self._path = path
        self._journal_path = path.with_name(f".{path.name}.journal")
        self._lock = RLock()

    def get(self, connection_id: UUID) -> str | None:
        with self._lock:
            value = self._read().get(str(connection_id))
            return value if isinstance(value, str) and value else None

    def set(self, connection_id: UUID, value: str | None) -> None:
        with self._lock:
            secrets = self._read()
            if value:
                secrets[str(connection_id)] = value
            else:
                secrets.pop(str(connection_id), None)
            self._write_journaled(secrets)

    def recover(self) -> None:
        """Finish a secret-store replacement interrupted by process termination."""

        with self._lock:
            if not self._journal_path.exists():
                return
            target = self._read_path(self._journal_path)
            self._write_main(target)
            self._journal_path.unlink(missing_ok=True)

    def configured_ids(self) -> set[str]:
        with self._lock:
            return set(self._read())

    def retain(self, valid_ids: set[str]) -> None:
        """Remove orphaned secrets after the authoritative DB is loaded."""

        with self._lock:
            current = self._read()
            retained = {key: value for key, value in current.items() if key in valid_ids}
            if retained != current:
                self._write_journaled(retained)

    def _read(self) -> dict[str, str]:
        if self._journal_path.exists():
            # A complete journal is always the intended next state.  Recovery is
            # idempotent and happens before serving a secret.
            target = self._read_path(self._journal_path)
            self._write_main(target)
            self._journal_path.unlink(missing_ok=True)
        if not self._path.exists():
            return {}
        return self._read_path(self._path)

    def _read_path(self, path: Path) -> dict[str, str]:
        try:
            value: object = json.loads(path.read_text(encoding="utf-8"))
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

    def _write_journaled(self, secrets: dict[str, str]) -> None:
        self._write_file(self._journal_path, secrets)
        self._write_main(secrets)
        self._journal_path.unlink(missing_ok=True)

    def _write_main(self, secrets: dict[str, str]) -> None:
        temporary = self._path.with_name(f".{self._path.name}.tmp")
        try:
            self._write_file(temporary, secrets)
            os.replace(temporary, self._path)
            os.chmod(self._path, 0o600)
        finally:
            temporary.unlink(missing_ok=True)

    @staticmethod
    def _write_file(path: Path, secrets: dict[str, str]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps(secrets, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as secret_file:
            secret_file.write(payload)
            secret_file.flush()
            os.fsync(secret_file.fileno())
        os.chmod(path, 0o600)


class McpSecretMutationJournal:
    """Durable intent log spanning the secret file and SQLite configuration.

    A file replacement and a database transaction cannot share one atomic commit.
    The journal records both the previous and intended token until the database
    mutation commits. Startup can therefore finish or compensate an interrupted
    create, update, or delete without guessing from the configured flag.
    """

    def __init__(self, path: Path) -> None:
        self._store = McpConnectionSecretStore(path)

    def prepare(
        self,
        connection_id: UUID,
        *,
        operation: str,
        previous_token: str | None,
        next_token: str | None,
        previous_revision: int | None,
    ) -> None:
        if operation not in {"create", "update", "delete"}:
            raise ValueError("invalid MCP secret mutation operation")
        record = json.dumps(
            {
                "operation": operation,
                "previous_token": previous_token,
                "next_token": next_token,
                "previous_revision": previous_revision,
            },
            ensure_ascii=False,
            sort_keys=True,
        )
        self._store.set(connection_id, record)

    def discard(self, connection_id: UUID) -> None:
        self._store.set(connection_id, None)

    def entries(self) -> dict[UUID, dict[str, object]]:
        result: dict[UUID, dict[str, object]] = {}
        for raw_id in self._store.configured_ids():
            try:
                connection_id = UUID(raw_id)
                serialized = self._store.get(connection_id)
                value: object = json.loads(serialized or "")
            except (ValueError, json.JSONDecodeError) as error:
                raise SkillExecutionError(
                    "mcp_secret_journal_corrupt",
                    "MCP secret mutation journal is invalid; refusing startup",
                ) from error
            if not isinstance(value, dict):
                raise SkillExecutionError(
                    "mcp_secret_journal_corrupt",
                    "MCP secret mutation journal is invalid; refusing startup",
                )
            record = cast(dict[str, object], value)
            operation = record.get("operation")
            previous_token = record.get("previous_token")
            next_token = record.get("next_token")
            previous_revision = record.get("previous_revision")
            if (
                operation not in {"create", "update", "delete"}
                or (previous_token is not None and not isinstance(previous_token, str))
                or (next_token is not None and not isinstance(next_token, str))
                or (previous_revision is not None and not isinstance(previous_revision, int))
            ):
                raise SkillExecutionError(
                    "mcp_secret_journal_corrupt",
                    "MCP secret mutation journal has invalid entries; refusing startup",
                )
            result[connection_id] = record
        return result


class McpConnectionManager:
    def __init__(
        self,
        repository: McpConnectionRepository,
        data_root: Path,
        transport: McpClientTransport,
    ) -> None:
        self._repository = repository
        self._root = data_root / "mcp-connections"
        self._secrets = McpConnectionSecretStore(data_root / "mcp-secrets.json")
        self._secret_mutations = McpSecretMutationJournal(data_root / "mcp-secret-mutations.json")
        self._transport = transport
        self._operation_locks: dict[UUID, asyncio.Lock] = {}
        self._operation_locks_guard = asyncio.Lock()

    async def start(self) -> None:
        await asyncio.to_thread(self._root.mkdir, parents=True, exist_ok=True)
        await asyncio.to_thread(self._secrets.recover)
        rows = await self._repository.list_mcp_connection_records()
        await self._recover_secret_mutations(rows)
        valid_ids = {str(row["connection_id"]) for row in rows}
        await asyncio.to_thread(self._secrets.retain, valid_ids)
        configured_ids = await asyncio.to_thread(self._secrets.configured_ids)
        await self._repository.reconcile_mcp_secret_flags(configured_ids)

    @asynccontextmanager
    async def operation_lease(self, connection_id: UUID) -> AsyncGenerator[None]:
        """Serialize invocation, mutation, discovery, and removal for one connection."""

        async with self._operation_locks_guard:
            lock = self._operation_locks.setdefault(connection_id, asyncio.Lock())
        async with lock:
            yield

    async def revision(self, connection_id: UUID) -> int:
        revision = await self._repository.mcp_connection_revision(connection_id)
        if revision is None:
            raise KeyError("MCP connection not found")
        return revision

    async def list(self) -> list[McpConnectionSnapshot]:
        rows = await self._repository.list_mcp_connection_records()
        return [self._snapshot(row) for row in rows]

    async def get(self, connection_id: UUID) -> McpConnectionSnapshot:
        row = await self._repository.mcp_connection_record(connection_id)
        if row is None:
            raise KeyError("MCP connection not found")
        return self._snapshot(row)

    async def create(
        self,
        config: McpConnectionConfiguration,
        *,
        bearer_token: str | None = None,
    ) -> McpConnectionSnapshot:
        async with self.operation_lease(config.connection_id):
            token = bearer_token.strip() if bearer_token and bearer_token.strip() else None
            previous_token = await asyncio.to_thread(self._secrets.get, config.connection_id)
            if previous_token is not None:
                raise ValueError("MCP connection secret already exists")
            if token is not None:
                await asyncio.to_thread(
                    self._secret_mutations.prepare,
                    config.connection_id,
                    operation="create",
                    previous_token=None,
                    next_token=token,
                    previous_revision=None,
                )
                await asyncio.to_thread(self._secrets.set, config.connection_id, token)
            now = _now()
            capabilities = _empty_capabilities(config.connection_id)
            try:
                await self._repository.insert_mcp_connection(
                    {
                        "connection_id": str(config.connection_id),
                        "name": config.name,
                        "transport": config.transport,
                        "command_json": json.dumps(config.command, ensure_ascii=False),
                        "url": config.url,
                        "allow_remote": int(config.allow_remote),
                        "enabled": int(config.enabled),
                        "timeout_seconds": config.timeout_seconds,
                        "trust_level": config.trust_level,
                        "sandbox_mode": config.sandbox_mode,
                        "network_policy": config.network_policy,
                        "bearer_token_configured": int(token is not None),
                        "status": "untested" if config.enabled else "disabled",
                        "capabilities_json": capabilities.model_dump_json(),
                        "created_at": now.isoformat(),
                        "updated_at": now.isoformat(),
                    }
                )
            except BaseException:
                if token is not None:
                    await self._compensate_secret(config.connection_id, None)
                raise
            if token is not None:
                await asyncio.to_thread(self._secret_mutations.discard, config.connection_id)
            return await self.get(config.connection_id)

    async def update(
        self,
        config: McpConnectionConfiguration,
        *,
        bearer_token: str | None = None,
        clear_bearer_token: bool = False,
        before_change: McpConnectionMutationHook | None = None,
    ) -> McpConnectionSnapshot:
        async with self.operation_lease(config.connection_id):
            current = await self.get(config.connection_id)
            if before_change is not None:
                await before_change()
            previous_token = await asyncio.to_thread(self._secrets.get, config.connection_id)
            now = _now()
            configured = current.bearer_token_configured
            next_token = previous_token
            if clear_bearer_token:
                configured = False
                next_token = None
            elif bearer_token is not None and bearer_token.strip():
                configured = True
                next_token = bearer_token.strip()
            if next_token != previous_token:
                await asyncio.to_thread(
                    self._secret_mutations.prepare,
                    config.connection_id,
                    operation="update",
                    previous_token=previous_token,
                    next_token=next_token,
                    previous_revision=await self.revision(config.connection_id),
                )
                await asyncio.to_thread(self._secrets.set, config.connection_id, next_token)
            try:
                updated = await self._repository.update_mcp_connection(
                    {
                        "connection_id": str(config.connection_id),
                        "name": config.name,
                        "transport": config.transport,
                        "command_json": json.dumps(config.command, ensure_ascii=False),
                        "url": config.url,
                        "allow_remote": int(config.allow_remote),
                        "enabled": int(config.enabled),
                        "timeout_seconds": config.timeout_seconds,
                        "trust_level": config.trust_level,
                        "sandbox_mode": config.sandbox_mode,
                        "network_policy": config.network_policy,
                        "bearer_token_configured": int(configured),
                        "status": "untested" if config.enabled else "disabled",
                        "capabilities_json": _empty_capabilities(
                            config.connection_id
                        ).model_dump_json(),
                        "updated_at": now.isoformat(),
                    }
                )
                if not updated:
                    raise KeyError("MCP connection not found")
            except BaseException:
                if next_token != previous_token:
                    await self._compensate_secret(config.connection_id, previous_token)
                raise
            if next_token != previous_token:
                await asyncio.to_thread(self._secret_mutations.discard, config.connection_id)
            return await self.get(config.connection_id)

    async def delete(
        self,
        connection_id: UUID,
        *,
        before_change: McpConnectionMutationHook | None = None,
    ) -> None:
        async with self.operation_lease(connection_id):
            await self.get(connection_id)
            if before_change is not None:
                await before_change()
            previous_token = await asyncio.to_thread(self._secrets.get, connection_id)
            if previous_token is not None:
                await asyncio.to_thread(
                    self._secret_mutations.prepare,
                    connection_id,
                    operation="delete",
                    previous_token=previous_token,
                    next_token=None,
                    previous_revision=await self.revision(connection_id),
                )
                await asyncio.to_thread(self._secrets.set, connection_id, None)
            try:
                if not await self._repository.delete_mcp_connection(connection_id):
                    raise KeyError("MCP connection not found")
            except BaseException:
                if previous_token is not None:
                    await self._compensate_secret(connection_id, previous_token)
                raise
            if previous_token is not None:
                await asyncio.to_thread(self._secret_mutations.discard, connection_id)
            working_root = self.working_root(connection_id)
            if await asyncio.to_thread(working_root.exists):
                await asyncio.to_thread(shutil.rmtree, working_root)

    async def test(self, connection_id: UUID) -> McpConnectionSnapshot:
        async with self.operation_lease(connection_id):
            return await self._test_unlocked(connection_id)

    async def _test_unlocked(self, connection_id: UUID) -> McpConnectionSnapshot:
        current = await self.get(connection_id)
        config = _configuration(current)
        sandbox_backend: str | None = None
        sandbox_limits: tuple[str, ...] = ()
        try:
            sandbox_backend, sandbox_limits = self._transport.connection_sandbox_status(
                config,
                working_root=self.working_root(connection_id),
            )
            capabilities = await self.discover(config)
        except asyncio.CancelledError:
            raise
        except Exception as error:
            message = (
                error.structured.message
                if isinstance(error, SkillExecutionError)
                else f"MCP connection test failed ({type(error).__name__})"
            )
            now = _now()
            await self._repository.mark_mcp_test_error(
                connection_id,
                message,
                sandbox_backend,
                json.dumps(sandbox_limits),
                now.isoformat(),
            )
            raise
        now = _now()
        await self._repository.mark_mcp_test_ready(
            connection_id,
            capabilities.model_dump_json(),
            sandbox_backend,
            json.dumps(sandbox_limits),
            now.isoformat(),
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
        snapshot = McpCapabilitySnapshot(
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
        enforce_mcp_json_payload_limit(
            snapshot.model_dump(mode="json", exclude_none=True),
            boundary="capability discovery",
        )
        return snapshot

    async def read_resource(
        self,
        connection_id: UUID,
        uri: str,
        *,
        expected_revision: int | None = None,
    ) -> JsonObject:
        async with self.operation_lease(connection_id):
            if (
                expected_revision is not None
                and await self.revision(connection_id) != expected_revision
            ):
                raise SkillExecutionError(
                    "approval_context_changed",
                    "MCP connection changed after approval; invoke it again",
                    retryable=True,
                )
            return await self._read_resource_unlocked(connection_id, uri)

    async def _read_resource_unlocked(self, connection_id: UUID, uri: str) -> JsonObject:
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
        serialized = cast(
            JsonObject, result.model_dump(mode="json", by_alias=True, exclude_none=True)
        )
        enforce_mcp_json_payload_limit(serialized, boundary="resource read")
        return serialized

    async def get_prompt(
        self,
        connection_id: UUID,
        name: str,
        arguments: dict[str, str],
        *,
        expected_revision: int | None = None,
    ) -> JsonObject:
        async with self.operation_lease(connection_id):
            if (
                expected_revision is not None
                and await self.revision(connection_id) != expected_revision
            ):
                raise SkillExecutionError(
                    "approval_context_changed",
                    "MCP connection changed after approval; invoke it again",
                    retryable=True,
                )
            return await self._get_prompt_unlocked(connection_id, name, arguments)

    async def _get_prompt_unlocked(
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
        serialized = cast(
            JsonObject, result.model_dump(mode="json", by_alias=True, exclude_none=True)
        )
        enforce_mcp_json_payload_limit(serialized, boundary="prompt result")
        return serialized

    def bearer_token(self, connection_id: UUID) -> str | None:
        return self._secrets.get(connection_id)

    def working_root(self, connection_id: UUID) -> Path:
        return self._root / str(connection_id)

    async def _compensate_secret(self, connection_id: UUID, token: str | None) -> None:
        """Restore the secret first; retain the journal if restoration fails."""

        await asyncio.to_thread(self._secrets.set, connection_id, token)
        await asyncio.to_thread(self._secret_mutations.discard, connection_id)

    async def _recover_secret_mutations(self, rows: list[Record]) -> None:
        entries = await asyncio.to_thread(self._secret_mutations.entries)
        if not entries:
            return
        by_id = {UUID(str(row["connection_id"])): row for row in rows}
        for connection_id, mutation in entries.items():
            operation = str(mutation["operation"])
            row = by_id.get(connection_id)
            previous_revision = mutation.get("previous_revision")
            committed = (
                (operation == "create" and row is not None)
                or (operation == "delete" and row is None)
                or (
                    operation == "update"
                    and row is not None
                    and isinstance(previous_revision, int)
                    and int(cast(int | str, row["revision"])) > previous_revision
                )
            )
            selected = mutation["next_token"] if committed else mutation["previous_token"]
            token = str(selected) if isinstance(selected, str) and selected else None
            await asyncio.to_thread(self._secrets.set, connection_id, token)
            await asyncio.to_thread(self._secret_mutations.discard, connection_id)

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
                "sandbox_limits_enforced": json.loads(str(values["sandbox_limits_json"])),
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
