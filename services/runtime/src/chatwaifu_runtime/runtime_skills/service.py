"""Permissioned Runtime Skill discovery, execution, plugin lifecycle, and audit."""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import cast
from uuid import UUID, uuid4

from chatwaifu_protocol.base import JsonObject, PrivacyLevel
from chatwaifu_protocol.errors import StructuredError
from chatwaifu_protocol.events import GenericCoreEvent
from chatwaifu_protocol.skills import (
    McpConnectionConfiguration,
    McpConnectionSnapshot,
    PluginSnapshot,
    SkillCapability,
    SkillDefinition,
    SkillInvocation,
    SkillResult,
    SkillRunSnapshot,
    SkillRunState,
)
from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError, ValidationError

from chatwaifu_runtime.eventing.publisher import EventPublisher
from chatwaifu_runtime.persistence.database import Database
from chatwaifu_runtime.providers.factory import ProviderSet
from chatwaifu_runtime.runtime_skills.adapters import (
    BuiltinAdapter,
    McpConnectionAdapter,
    McpStdioAdapter,
)
from chatwaifu_runtime.runtime_skills.errors import SkillExecutionError
from chatwaifu_runtime.runtime_skills.host_connections import McpConnectionManager
from chatwaifu_runtime.runtime_skills.permissions import PermissionBroker
from chatwaifu_runtime.runtime_skills.plugins import PluginManager
from chatwaifu_runtime.runtime_skills.registry import (
    RegistryEntry,
    SkillRegistry,
    load_plugin_manifest,
)
from chatwaifu_runtime.runtime_skills.sandbox import RuntimeSandboxLauncher
from chatwaifu_runtime.runtime_skills.transports import McpClientTransport, SandboxLauncher

TERMINAL_STATES = {
    SkillRunState.SUCCEEDED,
    SkillRunState.FAILED,
    SkillRunState.CANCELLED,
    SkillRunState.EXPIRED,
}


class RuntimeSkillService:
    def __init__(
        self,
        root: Path,
        data_dir: Path,
        database: Database,
        publisher: EventPublisher,
        providers: ProviderSet,
        stt_provider: str,
        version: str,
        sandbox_launcher: SandboxLauncher | None = None,
    ) -> None:
        self._root = root
        self._database = database
        self._publisher = publisher
        self._providers = providers
        self._stt_provider = stt_provider
        self._version = version
        self._registry = SkillRegistry(root / "builtin")
        self._plugins = PluginManager(database, data_dir / "plugins", data_dir / "plugin-trash")
        self._permissions = PermissionBroker(database)
        self._builtin = BuiltinAdapter()
        self._builtin.register("runtime_status", self._runtime_status)
        launcher = sandbox_launcher or RuntimeSandboxLauncher()
        self._mcp = McpStdioAdapter(launcher)
        mcp_transport = McpClientTransport(launcher)
        self._mcp_connections = McpConnectionManager(database, data_dir, mcp_transport)
        self._mcp_connection_adapter = McpConnectionAdapter(mcp_transport)
        self._tasks: dict[UUID, asyncio.Task[None]] = {}

    async def start(self) -> None:
        await self._plugins.start()
        await self._mcp_connections.start()
        now = _now().isoformat()
        async with self._database.transaction() as connection:
            await connection.execute(
                """
                UPDATE skill_runs
                SET state = 'expired', error_json = ?, updated_at = ?, completed_at = ?
                WHERE state NOT IN ('succeeded', 'failed', 'cancelled', 'expired')
                """,
                (
                    StructuredError(
                        code="runtime_restarted",
                        message="Skill run expired because Runtime restarted",
                        retryable=True,
                        component="runtime.skills",
                    ).model_dump_json(),
                    now,
                    now,
                ),
            )
        await self._reload_registry()

    async def stop(self) -> None:
        tasks = list(self._tasks.values())
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self._tasks.clear()

    def list(self) -> list[SkillDefinition]:
        return self._registry.list()

    def instructions(self, skill_id: str) -> str:
        return self._registry.instructions(skill_id)

    async def list_plugins(self) -> list[PluginSnapshot]:
        return await self._plugins.list()

    async def list_mcp_connections(self) -> list[McpConnectionSnapshot]:
        return await self._mcp_connections.list()

    async def get_mcp_connection(self, connection_id: UUID) -> McpConnectionSnapshot:
        return await self._mcp_connections.get(connection_id)

    async def create_mcp_connection(
        self,
        config: McpConnectionConfiguration,
        *,
        bearer_token: str | None = None,
    ) -> McpConnectionSnapshot:
        snapshot = await self._mcp_connections.create(config, bearer_token=bearer_token)
        await self._reload_registry()
        return snapshot

    async def update_mcp_connection(
        self,
        config: McpConnectionConfiguration,
        *,
        bearer_token: str | None = None,
        clear_bearer_token: bool = False,
    ) -> McpConnectionSnapshot:
        snapshot = await self._mcp_connections.update(
            config,
            bearer_token=bearer_token,
            clear_bearer_token=clear_bearer_token,
        )
        await self._reload_registry()
        return snapshot

    async def delete_mcp_connection(self, connection_id: UUID) -> None:
        active = await self._database.fetchone(
            """
            SELECT 1 FROM skill_runs
            WHERE mcp_connection_id = ?
              AND state NOT IN ('succeeded', 'failed', 'cancelled', 'expired')
            LIMIT 1
            """,
            (str(connection_id),),
        )
        if active is not None:
            raise ValueError("MCP connection has an active skill run")
        await self._mcp_connections.delete(connection_id)
        await self._reload_registry()

    async def test_mcp_connection(self, connection_id: UUID) -> McpConnectionSnapshot:
        try:
            snapshot = await self._mcp_connections.test(connection_id)
        finally:
            await self._reload_registry()
        return snapshot

    async def read_mcp_resource(self, connection_id: UUID, uri: str) -> JsonObject:
        return await self._mcp_connections.read_resource(connection_id, uri)

    async def get_mcp_prompt(
        self, connection_id: UUID, name: str, arguments: dict[str, str]
    ) -> JsonObject:
        return await self._mcp_connections.get_prompt(connection_id, name, arguments)

    async def install_plugin(self, source: Path) -> PluginSnapshot:
        source = await asyncio.to_thread(lambda: source.expanduser().resolve())
        manifest = load_plugin_manifest(source)
        candidate_registry = SkillRegistry(self._root / "builtin")
        candidate_registry.reload([(manifest, source, True)])
        plugin = await self._plugins.install(source)
        await self._reload_registry()
        return plugin

    async def install_example_plugin(self, example_id: str) -> PluginSnapshot:
        if not example_id or any(
            character not in "abcdefghijklmnopqrstuvwxyz0123456789-_" for character in example_id
        ):
            raise ValueError("invalid example plugin id")
        source, examples_root = await asyncio.to_thread(_example_paths, self._root, example_id)
        if not source.is_relative_to(examples_root):
            raise ValueError("invalid example plugin path")
        return await self.install_plugin(source)

    async def set_plugin_enabled(self, plugin_id: str, enabled: bool) -> PluginSnapshot:
        plugin = await self._plugins.set_enabled(plugin_id, enabled)
        await self._reload_registry()
        return plugin

    async def uninstall_plugin(self, plugin_id: str) -> Path:
        active = await self._database.fetchone(
            """
            SELECT 1 FROM skill_runs
            WHERE plugin_id = ? AND state NOT IN ('succeeded', 'failed', 'cancelled', 'expired')
            LIMIT 1
            """,
            (plugin_id,),
        )
        if active is not None:
            raise ValueError("plugin has an active skill run")
        trash_path = await self._plugins.uninstall(plugin_id)
        await self._reload_registry()
        return trash_path

    async def invoke(
        self,
        session_id: UUID,
        invocation: SkillInvocation,
        *,
        principal: str = "local_user",
    ) -> SkillRunSnapshot:
        entry = self._registry.get(invocation.skill_id)
        if entry is None:
            raise KeyError("skill not found")
        if not entry.definition.enabled:
            raise ValueError("skill is disabled")
        capability = _capability(entry, invocation.capability)
        _validate_schema(capability.input_schema, invocation.arguments, "input")
        run_id = uuid4()
        now = _now().isoformat()
        async with self._database.transaction() as connection:
            await connection.execute(
                """
                INSERT INTO skill_runs(
                    skill_run_id, session_id, skill_id, skill_version, capability,
                    plugin_id, mcp_connection_id, state, arguments_json, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, 'created', ?, ?, ?)
                """,
                (
                    str(run_id),
                    str(session_id),
                    entry.definition.skill_id,
                    entry.definition.version,
                    capability.name,
                    entry.definition.plugin_id,
                    (
                        str(entry.definition.mcp_connection_id)
                        if entry.definition.mcp_connection_id
                        else None
                    ),
                    json.dumps(invocation.arguments, ensure_ascii=False),
                    now,
                    now,
                ),
            )
        missing = await self._permissions.missing_permissions(
            principal=principal,
            session_id=session_id,
            skill_id=entry.definition.skill_id,
            capability=capability,
        )
        if capability.confirmation_required or missing:
            request_id = await self._permissions.create_request(
                skill_run_id=run_id,
                principal=principal,
                skill_id=entry.definition.skill_id,
                capability=capability,
                missing_permissions=missing,
            )
            await self._emit(
                session_id,
                "skill.confirmation_requested",
                {
                    "skill_id": entry.definition.skill_id,
                    "capability": capability.name,
                    "request_id": str(request_id),
                    "permissions": missing,
                    "side_effect": capability.side_effect.value,
                },
                run_id,
            )
        else:
            self._schedule(run_id)
        return await self.get_run(run_id)

    async def decide_confirmation(
        self,
        request_id: UUID,
        decision: str,
        *,
        decided_by: str = "local_user",
    ) -> SkillRunSnapshot:
        row = await self._database.fetchone(
            """
            SELECT sr.session_id FROM permission_requests pr
            JOIN skill_runs sr ON sr.skill_run_id = pr.skill_run_id
            WHERE pr.request_id = ?
            """,
            (str(request_id),),
        )
        if row is None:
            raise KeyError("confirmation request not found")
        session_id = UUID(str(row["session_id"]))
        run_id, allowed = await self._permissions.decide(
            request_id=request_id,
            decision=decision,
            decided_by=decided_by,
            session_id=session_id,
        )
        if allowed:
            self._schedule(run_id)
        else:
            error = StructuredError(
                code="permission_denied",
                message="Skill invocation was denied",
                retryable=False,
                component="runtime.skills",
            )
            await self._finish_failed(run_id, error)
            await self._emit(
                session_id,
                "skill.run_failed",
                {"skill_run_id": str(run_id), "error": error.model_dump(mode="json")},
                run_id,
            )
        return await self.get_run(run_id)

    async def pending_confirmations(self, session_id: UUID) -> list[dict[str, object]]:
        return await self._permissions.list_pending(session_id)

    async def cancel(self, run_id: UUID) -> SkillRunSnapshot:
        snapshot = await self.get_run(run_id)
        if snapshot.state in TERMINAL_STATES:
            return snapshot
        now = _now().isoformat()
        async with self._database.transaction() as connection:
            await connection.execute(
                """
                UPDATE skill_runs SET cancel_requested = 1, state = 'cancelling', updated_at = ?
                WHERE skill_run_id = ?
                """,
                (now, str(run_id)),
            )
        task = self._tasks.get(run_id)
        if task is not None:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            current = await self.get_run(run_id)
            if current.state not in TERMINAL_STATES:
                await self._finish_cancelled(run_id)
        else:
            await self._finish_cancelled(run_id)
        return await self.get_run(run_id)

    async def get_run(self, run_id: UUID) -> SkillRunSnapshot:
        row = await self._database.fetchone(
            "SELECT * FROM skill_runs WHERE skill_run_id = ?", (str(run_id),)
        )
        if row is None:
            raise KeyError("skill run not found")
        return _snapshot(row)

    async def list_runs(self, session_id: UUID, limit: int = 50) -> list[SkillRunSnapshot]:
        rows = await self._database.fetchall(
            """
            SELECT * FROM skill_runs WHERE session_id = ?
            ORDER BY created_at DESC LIMIT ?
            """,
            (str(session_id), limit),
        )
        return [_snapshot(row) for row in rows]

    async def run_status(self, session_id: UUID) -> SkillResult:
        snapshot = await self.invoke(
            session_id,
            SkillInvocation(skill_id="runtime.status", capability="read", arguments={}),
        )
        task = self._tasks.get(snapshot.skill_run_id)
        if task is not None:
            await task
        completed = await self.get_run(snapshot.skill_run_id)
        if completed.result is None:
            message = completed.error.message if completed.error else "runtime.status failed"
            raise RuntimeError(message)
        return completed.result

    async def _reload_registry(self) -> None:
        self._registry.reload(
            await self._plugins.registry_sources(),
            await self._mcp_connections.list(),
        )

    def _schedule(self, run_id: UUID) -> None:
        if run_id in self._tasks:
            raise RuntimeError("skill run is already scheduled")
        task = asyncio.create_task(self._execute(run_id), name=f"skill-run:{run_id}")
        self._tasks[run_id] = task
        task.add_done_callback(lambda _: self._tasks.pop(run_id, None))

    async def _execute(self, run_id: UUID) -> None:
        row = await self._database.fetchone(
            "SELECT * FROM skill_runs WHERE skill_run_id = ?", (str(run_id),)
        )
        if row is None:
            return
        session_id = UUID(str(row["session_id"]))
        entry = self._registry.get(str(row["skill_id"]))
        if entry is None or not entry.definition.enabled:
            await self._finish_failed(
                run_id,
                StructuredError(
                    code="skill_unavailable",
                    message="Skill was removed or disabled before execution",
                    retryable=True,
                    component="runtime.skills",
                ),
            )
            return
        capability = _capability(entry, str(row["capability"]))
        arguments = cast(JsonObject, json.loads(str(row["arguments_json"])))
        now = _now().isoformat()
        async with self._database.transaction() as connection:
            await connection.execute(
                """
                UPDATE skill_runs SET state = 'running', started_at = COALESCE(started_at, ?),
                    updated_at = ? WHERE skill_run_id = ?
                """,
                (now, now, str(run_id)),
            )
        await self._emit(
            session_id,
            "skill.run_started",
            {
                "skill_id": entry.definition.skill_id,
                "capability": capability.name,
                "skill_run_id": str(run_id),
            },
            run_id,
        )
        tool_call_id = await self._tool_call_started(
            session_id, run_id, entry, capability, arguments
        )
        try:
            if entry.adapter.kind == "builtin":
                data = await asyncio.wait_for(
                    self._builtin.invoke(entry.adapter.target, arguments),
                    timeout=capability.timeout_seconds,
                )
            elif entry.adapter.kind == "mcp":
                if entry.plugin is None or entry.plugin_root is None:
                    raise SkillExecutionError(
                        "plugin_missing", "Plugin adapter metadata is missing"
                    )
                data = await self._mcp.invoke(
                    plugin=entry.plugin,
                    plugin_root=entry.plugin_root,
                    tool=capability.adapter_tool or entry.adapter.target,
                    arguments=arguments,
                    timeout_seconds=capability.timeout_seconds,
                )
            else:
                connection_id = entry.definition.mcp_connection_id
                if connection_id is None:
                    raise SkillExecutionError(
                        "mcp_connection_missing", "MCP connection metadata is missing"
                    )
                connection = await self._mcp_connections.get(connection_id)
                config = McpConnectionConfiguration.model_validate(
                    connection.model_dump(mode="python")
                )
                bearer_token = await asyncio.to_thread(
                    self._mcp_connections.bearer_token, connection_id
                )
                data = await self._mcp_connection_adapter.invoke(
                    config=config,
                    bearer_token=bearer_token,
                    working_root=self._mcp_connections.working_root(connection_id),
                    tool=capability.adapter_tool or capability.name,
                    arguments=arguments,
                    timeout_seconds=capability.timeout_seconds,
                )
            _validate_schema(capability.output_schema, data, "output")
            result = SkillResult(
                status="succeeded",
                data=data,
                spoken_summary=_spoken_summary(data),
                provenance=[
                    f"skill:{entry.definition.skill_id}@{entry.definition.version}",
                    f"adapter:{entry.adapter.kind}",
                ],
            )
            await self._tool_call_finished(session_id, run_id, tool_call_id, response=data)
            completed_at = _now().isoformat()
            async with self._database.transaction() as connection:
                await connection.execute(
                    """
                    UPDATE skill_runs SET state = 'succeeded', progress = 1,
                        result_json = ?, updated_at = ?, completed_at = ?
                    WHERE skill_run_id = ?
                    """,
                    (result.model_dump_json(), completed_at, completed_at, str(run_id)),
                )
            await self._emit(
                session_id,
                "skill.run_completed",
                {
                    "skill_id": entry.definition.skill_id,
                    "skill_run_id": str(run_id),
                    "result": result.model_dump(mode="json"),
                },
                run_id,
            )
        except asyncio.CancelledError:
            await self._tool_call_finished(
                session_id,
                run_id,
                tool_call_id,
                error=StructuredError(
                    code="skill_cancelled",
                    message="Skill invocation was cancelled",
                    retryable=True,
                    component="runtime.skills",
                ),
            )
            await self._finish_cancelled(run_id)
            await self._emit(
                session_id,
                "skill.run_cancelled",
                {"skill_id": entry.definition.skill_id, "skill_run_id": str(run_id)},
                run_id,
            )
            raise
        except TimeoutError:
            await self._fail_execution(
                run_id,
                session_id,
                tool_call_id,
                SkillExecutionError(
                    "skill_timeout",
                    f"Skill exceeded {capability.timeout_seconds:g}s timeout",
                    retryable=True,
                ).structured,
            )
        except SkillExecutionError as error:
            await self._fail_execution(run_id, session_id, tool_call_id, error.structured)
        except Exception as error:
            structured = StructuredError(
                code="skill_internal_error",
                message="Skill execution failed safely",
                retryable=False,
                component="runtime.skills",
                details={"exception_type": type(error).__name__},
            )
            await self._fail_execution(run_id, session_id, tool_call_id, structured)

    async def _fail_execution(
        self,
        run_id: UUID,
        session_id: UUID,
        tool_call_id: UUID,
        error: StructuredError,
    ) -> None:
        await self._tool_call_finished(session_id, run_id, tool_call_id, error=error)
        await self._finish_failed(run_id, error)
        await self._emit(
            session_id,
            "skill.run_failed",
            {"skill_run_id": str(run_id), "error": error.model_dump(mode="json")},
            run_id,
        )

    async def _finish_failed(self, run_id: UUID, error: StructuredError) -> None:
        now = _now().isoformat()
        async with self._database.transaction() as connection:
            await connection.execute(
                """
                UPDATE skill_runs SET state = 'failed', error_json = ?,
                    updated_at = ?, completed_at = ? WHERE skill_run_id = ?
                """,
                (error.model_dump_json(), now, now, str(run_id)),
            )

    async def _finish_cancelled(self, run_id: UUID) -> None:
        now = _now().isoformat()
        async with self._database.transaction() as connection:
            await connection.execute(
                """
                UPDATE skill_runs SET state = 'cancelled', updated_at = ?, completed_at = ?
                WHERE skill_run_id = ?
                """,
                (now, now, str(run_id)),
            )

    async def _tool_call_started(
        self,
        session_id: UUID,
        run_id: UUID,
        entry: RegistryEntry,
        capability: SkillCapability,
        arguments: JsonObject,
    ) -> UUID:
        call_id = uuid4()
        now = _now().isoformat()
        async with self._database.transaction() as connection:
            await connection.execute(
                """
                INSERT INTO skill_tool_calls(
                    tool_call_id, skill_run_id, adapter, method, request_json,
                    status, started_at
                ) VALUES (?, ?, ?, ?, ?, 'running', ?)
                """,
                (
                    str(call_id),
                    str(run_id),
                    entry.adapter.kind,
                    capability.adapter_tool or entry.adapter.target,
                    json.dumps(arguments, ensure_ascii=False),
                    now,
                ),
            )
        await self._emit(
            session_id,
            "tool.call_started",
            {
                "tool_call_id": str(call_id),
                "adapter": entry.adapter.kind,
                "method": capability.adapter_tool or entry.adapter.target,
            },
            run_id,
        )
        return call_id

    async def _tool_call_finished(
        self,
        session_id: UUID,
        run_id: UUID,
        call_id: UUID,
        *,
        response: JsonObject | None = None,
        error: StructuredError | None = None,
    ) -> None:
        now = _now().isoformat()
        async with self._database.transaction() as connection:
            await connection.execute(
                """
                UPDATE skill_tool_calls SET status = ?, response_json = ?, error_json = ?,
                    completed_at = ? WHERE tool_call_id = ?
                """,
                (
                    "failed" if error else "succeeded",
                    json.dumps(response, ensure_ascii=False) if response is not None else None,
                    error.model_dump_json() if error else None,
                    now,
                    str(call_id),
                ),
            )
        await self._emit(
            session_id,
            "tool.call_failed" if error else "tool.call_completed",
            {
                "tool_call_id": str(call_id),
                "status": "failed" if error else "succeeded",
                "error": error.model_dump(mode="json") if error else None,
            },
            run_id,
        )

    async def _runtime_status(self, _: JsonObject) -> JsonObject:
        providers = self._providers.public_status()
        return cast(
            JsonObject,
            {
                "runtime_version": self._version,
                "llm_provider": providers["llm"],
                "tts_provider": providers["tts"],
                "stt_provider": self._stt_provider,
                "transport": "pipecat_smallwebrtc",
                "persistence": "sqlite_wal",
            },
        )

    async def _emit(
        self,
        session_id: UUID,
        event_type: str,
        payload: dict[str, object],
        skill_run_id: UUID,
    ) -> None:
        event = GenericCoreEvent.model_validate(
            {
                "event_id": uuid4(),
                "event_type": event_type,
                "session_id": session_id,
                "skill_run_id": skill_run_id,
                "occurred_at": _now(),
                "source": "runtime.skills",
                "privacy": PrivacyLevel.LOCAL,
                "payload": payload,
            }
        )
        await self._publisher.emit(event)


def _snapshot(row: object) -> SkillRunSnapshot:
    values = cast(dict[str, object], row)
    return SkillRunSnapshot.model_validate(
        {
            "skill_run_id": values["skill_run_id"],
            "skill_id": values["skill_id"],
            "skill_version": values["skill_version"],
            "capability": values["capability"],
            "plugin_id": values["plugin_id"],
            "mcp_connection_id": values["mcp_connection_id"],
            "session_id": values["session_id"],
            "state": values["state"],
            "progress": values["progress"],
            "confirmation_request_id": values["confirmation_request_id"],
            "result": json.loads(str(values["result_json"])) if values["result_json"] else None,
            "error": json.loads(str(values["error_json"])) if values["error_json"] else None,
            "created_at": values["created_at"],
            "started_at": values["started_at"],
            "updated_at": values["updated_at"],
            "completed_at": values["completed_at"],
        }
    )


def _capability(entry: RegistryEntry, name: str) -> SkillCapability:
    for capability in entry.definition.capabilities:
        if capability.name == name:
            return capability
    raise KeyError(f"capability not found: {name}")


def _validate_schema(schema: JsonObject, value: object, boundary: str) -> None:
    try:
        Draft202012Validator.check_schema(schema)
        Draft202012Validator(schema).validate(value)  # pyright: ignore[reportUnknownMemberType]
    except SchemaError as error:
        raise SkillExecutionError(
            "invalid_capability_schema", f"Skill {boundary} schema is invalid: {error.message}"
        ) from error
    except ValidationError as error:
        raise SkillExecutionError(
            f"invalid_skill_{boundary}",
            f"Skill {boundary} failed schema validation: {error.message}",
            details={"path": list(error.absolute_path)},
        ) from error


def _spoken_summary(data: JsonObject) -> str | None:
    summary = data.get("spoken_summary")
    return summary if isinstance(summary, str) else None


def _now() -> datetime:
    return datetime.now(UTC)


def _example_paths(skills_root: Path, example_id: str) -> tuple[Path, Path]:
    examples_root = (skills_root.parent / "plugins" / "examples").resolve()
    return (examples_root / example_id).resolve(), examples_root
