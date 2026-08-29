"""Permissioned Runtime Skill discovery, execution, plugin lifecycle, and audit."""

from __future__ import annotations

import asyncio
import json
import logging
import secrets
from collections import OrderedDict
from datetime import UTC, datetime
from pathlib import Path
from typing import NoReturn, cast
from uuid import UUID, uuid4

from chatwaifu_protocol.base import JsonObject, JsonValue, PrivacyLevel
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
from pydantic import ValidationError as PydanticValidationError
from referencing import Registry
from referencing.exceptions import NoSuchResource, Unresolvable
from referencing.jsonschema import SchemaRegistry

from chatwaifu_runtime.eventing.publisher import EventPublisher
from chatwaifu_runtime.providers.factory import ProviderSet
from chatwaifu_runtime.runtime_skills.adapters import (
    BuiltinAdapter,
    McpConnectionAdapter,
    McpStdioAdapter,
)
from chatwaifu_runtime.runtime_skills.audit import payload_digest, sanitize_audit_payload
from chatwaifu_runtime.runtime_skills.errors import SkillExecutionError
from chatwaifu_runtime.runtime_skills.execution_plan import (
    ExecutionPlan,
    build_execution_plan,
    plan_matches_entry,
)
from chatwaifu_runtime.runtime_skills.host_connections import McpConnectionManager
from chatwaifu_runtime.runtime_skills.permissions import (
    CONFIRMATION_TTL_SECONDS,
    PermissionBroker,
)
from chatwaifu_runtime.runtime_skills.plugins import PluginManager
from chatwaifu_runtime.runtime_skills.registry import (
    RegistryEntry,
    SkillRegistry,
    load_plugin_manifest,
    mcp_prompt_skill_id,
    mcp_resource_skill_id,
)
from chatwaifu_runtime.runtime_skills.repository import RuntimeSkillRepository
from chatwaifu_runtime.runtime_skills.sandbox import RuntimeSandboxLauncher
from chatwaifu_runtime.runtime_skills.transports import (
    McpClientTransport,
    SandboxLauncher,
    mcp_connection_sandbox_subject_id,
    plugin_sandbox_subject_id,
)

TERMINAL_STATES = {
    SkillRunState.SUCCEEDED,
    SkillRunState.FAILED,
    SkillRunState.CANCELLED,
    SkillRunState.EXPIRED,
}
logger = logging.getLogger(__name__)
MAX_EPHEMERAL_RESULTS = 64


def _deny_schema_retrieval(uri: str) -> NoReturn:
    raise NoSuchResource(ref=uri)


_NO_RETRIEVAL_SCHEMA_REGISTRY = cast(
    SchemaRegistry,
    Registry(retrieve=_deny_schema_retrieval),  # pyright: ignore[reportUnknownArgumentType]
)


class RuntimeSkillService:
    def __init__(
        self,
        root: Path,
        data_dir: Path,
        repository: RuntimeSkillRepository,
        publisher: EventPublisher,
        providers: ProviderSet,
        stt_provider: str,
        version: str,
        sandbox_launcher: SandboxLauncher | None = None,
    ) -> None:
        self._root = root
        self._repository = repository
        self._publisher = publisher
        self._providers = providers
        self._stt_provider = stt_provider
        self._version = version
        self._registry = SkillRegistry(root / "builtin")
        self._plugins = PluginManager(
            repository,
            data_dir / "plugins",
            data_dir / "plugin-data",
            data_dir / "plugin-trash",
        )
        self._permissions = PermissionBroker(repository)
        self._builtin = BuiltinAdapter()
        self._builtin.register("runtime_status", self._runtime_status)
        launcher = sandbox_launcher or RuntimeSandboxLauncher()
        self._sandbox_launcher = launcher
        self._mcp = McpStdioAdapter(launcher)
        mcp_transport = McpClientTransport(launcher)
        self._mcp_connections = McpConnectionManager(repository, data_dir, mcp_transport)
        self._mcp_connection_adapter = McpConnectionAdapter(mcp_transport)
        self._tasks: dict[UUID, asyncio.Task[None]] = {}
        self._pending_arguments: dict[UUID, JsonObject] = {}
        self._ephemeral_results: OrderedDict[UUID, SkillResult] = OrderedDict()
        self._confirmation_expiry_tasks: dict[UUID, asyncio.Task[None]] = {}
        self._audit_digest_key = secrets.token_bytes(32)
        self._registry_reload_lock = asyncio.Lock()

    async def start(self) -> None:
        await self._plugins.start()
        await self._mcp_connections.start()
        plugin_subjects = [
            plugin_sandbox_subject_id(plugin.plugin_id)
            for plugin in await self._plugins.list()
            if plugin.enabled and plugin.sandbox_mode != "disabled"
        ]
        connection_subjects = [
            mcp_connection_sandbox_subject_id(connection.connection_id)
            for connection in await self._mcp_connections.list()
            if connection.enabled
            and connection.transport == "stdio"
            and connection.sandbox_mode != "disabled"
        ]
        await asyncio.to_thread(
            self._sandbox_launcher.reconcile,
            (*plugin_subjects, *connection_subjects),
        )
        await self._repository.expire_nonterminal_runs(
            StructuredError(
                code="runtime_restarted",
                message="Skill run expired because Runtime restarted",
                retryable=True,
                component="runtime.skills",
            ),
            _now().isoformat(),
        )
        await self._reload_registry_serialized()

    async def stop(self) -> None:
        tasks = [*self._tasks.values(), *self._confirmation_expiry_tasks.values()]
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self._tasks.clear()
        self._confirmation_expiry_tasks.clear()
        self._pending_arguments.clear()
        self._ephemeral_results.clear()

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
        await self._reload_registry_serialized()
        return snapshot

    async def update_mcp_connection(
        self,
        config: McpConnectionConfiguration,
        *,
        bearer_token: str | None = None,
        clear_bearer_token: bool = False,
    ) -> McpConnectionSnapshot:
        async def revoke_current_sandbox() -> None:
            await asyncio.to_thread(
                self._sandbox_launcher.revoke,
                mcp_connection_sandbox_subject_id(config.connection_id),
            )

        snapshot = await self._mcp_connections.update(
            config,
            bearer_token=bearer_token,
            clear_bearer_token=clear_bearer_token,
            before_change=revoke_current_sandbox,
        )
        await self._reload_registry_serialized()
        return snapshot

    async def delete_mcp_connection(self, connection_id: UUID) -> None:
        async def require_no_active_run() -> None:
            if await self._repository.has_active_reference("mcp_connection_id", str(connection_id)):
                raise ValueError("MCP connection has an active skill run")
            await asyncio.to_thread(
                self._sandbox_launcher.revoke,
                mcp_connection_sandbox_subject_id(connection_id),
            )

        await self._mcp_connections.delete(connection_id, before_change=require_no_active_run)
        await self._reload_registry_serialized()

    async def test_mcp_connection(self, connection_id: UUID) -> McpConnectionSnapshot:
        try:
            snapshot = await self._mcp_connections.test(connection_id)
        finally:
            await self._reload_registry_serialized()
        return snapshot

    async def read_mcp_resource(
        self, session_id: UUID, connection_id: UUID, uri: str
    ) -> SkillRunSnapshot:
        return await self.invoke(
            session_id,
            SkillInvocation(
                skill_id=mcp_resource_skill_id(connection_id),
                capability="read",
                arguments={"uri": uri},
            ),
        )

    async def get_mcp_prompt(
        self,
        session_id: UUID,
        connection_id: UUID,
        name: str,
        arguments: dict[str, str],
    ) -> SkillRunSnapshot:
        return await self.invoke(
            session_id,
            SkillInvocation(
                skill_id=mcp_prompt_skill_id(connection_id),
                capability="get",
                arguments=cast(JsonObject, {"name": name, "arguments": arguments}),
            ),
        )

    async def install_plugin(self, source: Path) -> PluginSnapshot:
        source = await asyncio.to_thread(lambda: source.expanduser().resolve())
        manifest = load_plugin_manifest(source)
        candidate_registry = SkillRegistry(self._root / "builtin")
        candidate_registry.reload([(manifest, source, True)])
        return await self._plugins.install(
            source,
            after_change=self._reload_registry_serialized,
        )

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
        async def apply_lifecycle_change() -> None:
            await self._reload_registry_serialized()
            if not enabled:
                await asyncio.to_thread(
                    self._sandbox_launcher.revoke,
                    plugin_sandbox_subject_id(plugin_id),
                )

        return await self._plugins.set_enabled(
            plugin_id,
            enabled,
            after_change=apply_lifecycle_change,
        )

    async def uninstall_plugin(self, plugin_id: str) -> Path:
        async def require_no_active_run() -> None:
            if await self._repository.has_active_reference("plugin_id", plugin_id):
                raise ValueError("plugin has an active skill run")
            await asyncio.to_thread(
                self._sandbox_launcher.revoke,
                plugin_sandbox_subject_id(plugin_id),
            )

        return await self._plugins.uninstall(
            plugin_id,
            before_change=require_no_active_run,
            after_change=self._reload_registry_serialized,
        )

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
        if invocation.background and not entry.definition.background_allowed:
            raise ValueError("skill does not allow background execution")
        mcp_revision = (
            await self._mcp_connections.revision(entry.definition.mcp_connection_id)
            if entry.definition.mcp_connection_id is not None
            else None
        )
        plan = build_execution_plan(
            entry,
            capability,
            invocation.arguments,
            audit_digest_key=self._audit_digest_key,
            mcp_connection_revision=mcp_revision,
            background_requested=invocation.background,
        )
        run_id = uuid4()
        now = _now().isoformat()
        self._pending_arguments[run_id] = invocation.arguments
        try:
            await self._repository.create_run(
                {
                    "skill_run_id": str(run_id),
                    "session_id": str(session_id),
                    "skill_id": entry.definition.skill_id,
                    "skill_version": entry.definition.version,
                    "capability": capability.name,
                    "plugin_id": entry.definition.plugin_id,
                    "mcp_connection_id": (
                        str(entry.definition.mcp_connection_id)
                        if entry.definition.mcp_connection_id
                        else None
                    ),
                    "arguments_json": json.dumps(plan.arguments_summary, ensure_ascii=False),
                    "execution_plan_json": plan.model_dump_json(),
                    "execution_plan_fingerprint": plan.fingerprint(),
                    "created_at": now,
                    "updated_at": now,
                }
            )
        except BaseException:
            self._pending_arguments.pop(run_id, None)
            raise
        try:
            missing = await self._permissions.missing_permissions(
                principal=principal,
                session_id=session_id,
                plan=plan,
            )
            if capability.confirmation_required or missing:
                request_id = await self._permissions.create_request(
                    skill_run_id=run_id,
                    principal=principal,
                    plan=plan,
                    missing_permissions=missing,
                )
                self._schedule_confirmation_expiry(request_id, run_id)
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
        except BaseException as setup_error:
            await self._compensate_created_run(run_id, setup_error)
        return await self.get_run(run_id)

    async def decide_confirmation(
        self,
        request_id: UUID,
        decision: str,
        *,
        decided_by: str = "local_user",
    ) -> SkillRunSnapshot:
        run_row = await self._repository.run_for_confirmation(request_id)
        if run_row is None:
            raise KeyError("confirmation request not found")
        session_id = UUID(str(run_row["session_id"]))
        run_id = UUID(str(run_row["skill_run_id"]))
        try:
            await self._resolve_current_plan(run_row)
        except SkillExecutionError as error:
            self._cancel_confirmation_expiry(request_id)
            await self._permissions.expire_for_run(run_id)
            await self._finish_failed(run_id, error.structured)
            await self._emit(
                session_id,
                "skill.run_failed",
                {
                    "skill_run_id": str(run_id),
                    "error": error.structured.model_dump(mode="json"),
                },
                run_id,
            )
            self._pending_arguments.pop(run_id, None)
            return await self.get_run(run_id)
        try:
            run_id, allowed = await self._permissions.decide(
                request_id=request_id,
                decision=decision,
                decided_by=decided_by,
                session_id=session_id,
            )
        except (KeyError, ValueError):
            await self._prune_terminal_arguments()
            raise
        self._cancel_confirmation_expiry(request_id)
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
            self._pending_arguments.pop(run_id, None)
        return await self.get_run(run_id)

    async def pending_confirmations(self, session_id: UUID) -> list[dict[str, object]]:
        pending = await self._permissions.list_pending(session_id)
        await self._prune_terminal_arguments()
        return pending

    async def _prune_terminal_arguments(self) -> None:
        for run_id in tuple(self._pending_arguments):
            if run_id in self._tasks:
                continue
            try:
                snapshot = await self.get_run(run_id)
            except KeyError:
                self._pending_arguments.pop(run_id, None)
                continue
            if snapshot.state in TERMINAL_STATES:
                self._pending_arguments.pop(run_id, None)

    async def cancel(self, run_id: UUID) -> SkillRunSnapshot:
        snapshot = await self.get_run(run_id)
        if snapshot.state in TERMINAL_STATES:
            return snapshot
        row = await self._repository.run(run_id)
        if row is None:
            raise KeyError("skill run not found")
        plan_value = row["execution_plan_json"]
        if plan_value:
            try:
                plan = ExecutionPlan.model_validate_json(str(plan_value))
            except PydanticValidationError as error:
                raise ValueError("skill execution plan is invalid") from error
            if snapshot.state is SkillRunState.RUNNING and not plan.interruptible:
                raise ValueError("skill run is not interruptible")
        await self._repository.mark_run_cancelling(run_id, _now().isoformat())
        task = self._tasks.get(run_id)
        if task is not None:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            current = await self.get_run(run_id)
            if current.state not in TERMINAL_STATES:
                await self._finish_cancelled(run_id)
        else:
            await self._finish_cancelled(run_id)
        self._pending_arguments.pop(run_id, None)
        return await self.get_run(run_id)

    async def get_run(self, run_id: UUID) -> SkillRunSnapshot:
        row = await self._repository.run(run_id)
        if row is None:
            raise KeyError("skill run not found")
        return _snapshot(row, result_override=self._ephemeral_results.get(run_id))

    async def list_runs(self, session_id: UUID, limit: int = 50) -> list[SkillRunSnapshot]:
        rows = await self._repository.runs_for_session(session_id, limit)
        return [
            _snapshot(
                row,
                result_override=self._ephemeral_results.get(
                    UUID(str(cast(dict[str, object], row)["skill_run_id"]))
                ),
            )
            for row in rows
        ]

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

    async def _reload_registry_serialized(self) -> None:
        async with self._registry_reload_lock:
            await self._reload_registry()

    def _schedule(self, run_id: UUID) -> None:
        if run_id in self._tasks:
            raise RuntimeError("skill run is already scheduled")
        task = asyncio.create_task(self._execute(run_id), name=f"skill-run:{run_id}")
        self._tasks[run_id] = task
        task.add_done_callback(lambda _: self._execution_finished(run_id))

    def _schedule_confirmation_expiry(self, request_id: UUID, run_id: UUID) -> None:
        task = asyncio.create_task(
            self._expire_confirmation_after_ttl(run_id),
            name=f"skill-confirmation-expiry:{request_id}",
        )
        self._confirmation_expiry_tasks[request_id] = task
        task.add_done_callback(
            lambda completed: self._confirmation_expiry_finished(request_id, completed)
        )

    def _cancel_confirmation_expiry(self, request_id: UUID) -> None:
        task = self._confirmation_expiry_tasks.pop(request_id, None)
        if task is not None:
            task.cancel()

    async def _expire_confirmation_after_ttl(self, run_id: UUID) -> None:
        await asyncio.sleep(CONFIRMATION_TTL_SECONDS)
        await self._permissions.expire_for_run(run_id)
        self._pending_arguments.pop(run_id, None)

    def _confirmation_expiry_finished(self, request_id: UUID, task: asyncio.Task[None]) -> None:
        self._confirmation_expiry_tasks.pop(request_id, None)
        if not task.cancelled():
            error = task.exception()
            if error is not None:
                logger.error(
                    "Runtime Skill confirmation expiry failed",
                    exc_info=(type(error), error, error.__traceback__),
                )

    def _execution_finished(self, run_id: UUID) -> None:
        self._tasks.pop(run_id, None)
        self._pending_arguments.pop(run_id, None)

    async def _resolve_current_plan(
        self, row: object
    ) -> tuple[ExecutionPlan, RegistryEntry, SkillCapability]:
        plan = self._load_execution_plan(row)
        entry = self._registry.get(plan.skill_id)
        if entry is None or not entry.definition.enabled:
            raise SkillExecutionError(
                "approval_context_changed",
                "Skill changed or was disabled after approval; invoke it again",
                retryable=True,
            )
        try:
            capability = _capability(entry, plan.capability.name)
        except KeyError as error:
            raise SkillExecutionError(
                "approval_context_changed",
                "Skill capability changed after approval; invoke it again",
                retryable=True,
            ) from error
        revision = (
            await self._mcp_connections.revision(plan.mcp_connection_id)
            if plan.mcp_connection_id is not None
            else None
        )
        try:
            matches = plan_matches_entry(
                plan,
                entry,
                capability,
                audit_digest_key=self._audit_digest_key,
                mcp_connection_revision=revision,
            )
        except SkillExecutionError as error:
            raise SkillExecutionError(
                "approval_context_changed",
                "Plugin package changed after approval; invoke it again",
                retryable=True,
            ) from error
        if not matches:
            raise SkillExecutionError(
                "approval_context_changed",
                "Skill or MCP connection changed after approval; invoke it again",
                retryable=True,
            )
        return plan, entry, plan.capability

    def _load_execution_plan(self, row: object) -> ExecutionPlan:
        values = cast(dict[str, object], row)
        serialized = values["execution_plan_json"]
        if not serialized:
            raise SkillExecutionError(
                "execution_plan_missing",
                "Skill run has no immutable execution plan; invoke it again",
                retryable=True,
            )
        try:
            plan = ExecutionPlan.model_validate_json(str(serialized))
        except PydanticValidationError as error:
            raise SkillExecutionError(
                "execution_plan_corrupt",
                "Skill execution plan failed validation",
            ) from error
        persisted_fingerprint = values["execution_plan_fingerprint"]
        if persisted_fingerprint != plan.fingerprint():
            raise SkillExecutionError(
                "execution_plan_corrupt",
                "Skill execution plan failed its integrity check",
            )
        return plan

    async def _execute(self, run_id: UUID) -> None:
        row = await self._repository.run(run_id)
        if row is None:
            return
        session_id = UUID(str(row["session_id"]))
        try:
            persisted_plan = self._load_execution_plan(row)
        except SkillExecutionError as error:
            await self._fail_before_tool(run_id, session_id, error.structured)
            return
        if persisted_plan.plugin_id is not None:
            async with self._plugins.operation_lease(persisted_plan.plugin_id):
                await self._execute_current_plan(run_id, row, session_id)
            return
        await self._execute_current_plan(run_id, row, session_id)

    async def _execute_current_plan(self, run_id: UUID, row: object, session_id: UUID) -> None:
        try:
            plan, entry, capability = await self._resolve_current_plan(row)
        except SkillExecutionError as error:
            await self._fail_before_tool(run_id, session_id, error.structured)
            return
        arguments = self._pending_arguments.get(run_id)
        if arguments is None or plan.arguments_digest != payload_digest(
            arguments, key=self._audit_digest_key
        ):
            await self._fail_before_tool(
                run_id,
                session_id,
                StructuredError(
                    code="invocation_payload_unavailable",
                    message="Sensitive invocation payload is no longer in memory; invoke it again",
                    retryable=True,
                    component="runtime.skills",
                ),
            )
            return
        now = _now().isoformat()
        await self._repository.mark_run_running(run_id, now)
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
        tool_call_id: UUID | None = None
        try:
            tool_call_id = await self._tool_call_started(
                session_id, run_id, entry, capability, arguments
            )
            if plan.adapter_kind == "builtin":
                data = await asyncio.wait_for(
                    self._builtin.invoke(plan.adapter_target, arguments),
                    timeout=capability.timeout_seconds,
                )
            elif plan.adapter_kind == "mcp":
                if entry.plugin is None or entry.plugin_root is None:
                    raise SkillExecutionError(
                        "plugin_missing", "Plugin adapter metadata is missing"
                    )
                plugin_data_root = self._plugins.data_root(entry.plugin.plugin_id)
                try:
                    sandbox_backend, sandbox_limits = self._mcp.sandbox_status(
                        entry.plugin, entry.plugin_root, plugin_data_root
                    )
                except BaseException:
                    await self._plugins.set_sandbox_backend(entry.plugin.plugin_id, None)
                    raise
                await self._plugins.set_sandbox_backend(
                    entry.plugin.plugin_id, sandbox_backend, sandbox_limits
                )
                # Recompute the package and registry identity after planning the
                # sandbox and immediately before process launch. The per-plugin
                # operation lease remains held until invocation returns.
                plan, entry, capability = await self._resolve_current_plan(row)
                if entry.plugin is None or entry.plugin_root is None:
                    raise SkillExecutionError(
                        "plugin_missing", "Plugin adapter metadata is missing"
                    )
                data = await self._mcp.invoke(
                    plugin=entry.plugin,
                    plugin_root=entry.plugin_root,
                    data_root=plugin_data_root,
                    tool=plan.adapter_target,
                    arguments=arguments,
                    timeout_seconds=capability.timeout_seconds,
                )
            else:
                connection_id = plan.mcp_connection_id
                if connection_id is None:
                    raise SkillExecutionError(
                        "mcp_connection_missing", "MCP connection metadata is missing"
                    )
                if capability.adapter_operation == "resource_read":
                    data = await self._mcp_connections.read_resource(
                        connection_id,
                        cast(str, arguments["uri"]),
                        expected_revision=plan.mcp_connection_revision,
                    )
                elif capability.adapter_operation == "prompt_get":
                    prompt_arguments = cast(dict[str, str], arguments["arguments"])
                    data = await self._mcp_connections.get_prompt(
                        connection_id,
                        cast(str, arguments["name"]),
                        prompt_arguments,
                        expected_revision=plan.mcp_connection_revision,
                    )
                else:
                    data = await self._invoke_mcp_connection_tool(
                        plan,
                        capability,
                        connection_id,
                        arguments,
                    )
            _validate_schema(capability.output_schema, data, "output")
            result = SkillResult(
                status="succeeded",
                data=data,
                spoken_summary=_spoken_summary(data),
                provenance=[
                    f"skill:{entry.definition.skill_id}@{entry.definition.version}",
                    f"adapter:{plan.adapter_kind}",
                ],
            )
            persisted_result = result.model_copy(
                update={
                    "data": sanitize_audit_payload(
                        data,
                        capability.output_schema,
                        allow_schema_public=plan.audit_public_fields_allowed,
                    ),
                    "spoken_summary": None,
                    "ui_cards": [],
                    "avatar_cues": [],
                }
            )
            await self._tool_call_finished(
                session_id,
                run_id,
                tool_call_id,
                response=data,
                response_schema=capability.output_schema,
                allow_schema_public=plan.audit_public_fields_allowed,
            )
            completed_at = _now().isoformat()
            self._remember_result(run_id, result)
            await self._repository.complete_run(
                run_id, persisted_result.model_dump_json(), completed_at
            )
            await self._emit(
                session_id,
                "skill.run_completed",
                {
                    "skill_id": entry.definition.skill_id,
                    "skill_run_id": str(run_id),
                    "result": persisted_result.model_dump(mode="json"),
                },
                run_id,
            )
        except asyncio.CancelledError:
            if tool_call_id is not None:
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

    async def _invoke_mcp_connection_tool(
        self,
        plan: ExecutionPlan,
        capability: SkillCapability,
        connection_id: UUID,
        arguments: JsonObject,
    ) -> JsonObject:
        async with self._mcp_connections.operation_lease(connection_id):
            if await self._mcp_connections.revision(connection_id) != plan.mcp_connection_revision:
                raise SkillExecutionError(
                    "approval_context_changed",
                    "MCP connection changed after approval; invoke it again",
                    retryable=True,
                )
            connection = await self._mcp_connections.get(connection_id)
            config = McpConnectionConfiguration.model_validate(connection.model_dump(mode="python"))
            bearer_token = await asyncio.to_thread(
                self._mcp_connections.bearer_token, connection_id
            )
            return await self._mcp_connection_adapter.invoke(
                config=config,
                bearer_token=bearer_token,
                working_root=self._mcp_connections.working_root(connection_id),
                tool=plan.adapter_target,
                arguments=arguments,
                timeout_seconds=capability.timeout_seconds,
            )

    async def _fail_execution(
        self,
        run_id: UUID,
        session_id: UUID,
        tool_call_id: UUID | None,
        error: StructuredError,
    ) -> None:
        if tool_call_id is not None:
            await self._tool_call_finished(session_id, run_id, tool_call_id, error=error)
        await self._finish_failed(run_id, error)
        await self._emit(
            session_id,
            "skill.run_failed",
            {"skill_run_id": str(run_id), "error": error.model_dump(mode="json")},
            run_id,
        )

    async def _fail_before_tool(
        self,
        run_id: UUID,
        session_id: UUID,
        error: StructuredError,
    ) -> None:
        await self._finish_failed(run_id, error)
        await self._emit(
            session_id,
            "skill.run_failed",
            {"skill_run_id": str(run_id), "error": error.model_dump(mode="json")},
            run_id,
        )

    async def _compensate_created_run(
        self,
        run_id: UUID,
        primary: BaseException,
    ) -> NoReturn:
        """Never leave a created run nonterminal when permission setup fails."""

        self._pending_arguments.pop(run_id, None)
        failures: list[BaseException] = []
        try:
            await self._permissions.expire_for_run(run_id)
        except BaseException as error:
            failures.append(error)
        try:
            await self._repository.fail_run(
                run_id,
                StructuredError(
                    code="skill_invocation_setup_failed",
                    message="Skill invocation setup failed safely",
                    retryable=True,
                    component="runtime.skills",
                    details={"exception_type": type(primary).__name__},
                ).model_dump_json(),
                _now().isoformat(),
            )
        except BaseException as error:
            failures.append(error)
        if failures:
            raise BaseExceptionGroup(
                "Skill invocation setup and compensation both failed",
                [primary, *failures],
            ) from None
        raise primary

    async def _finish_failed(self, run_id: UUID, error: StructuredError) -> None:
        self._ephemeral_results.pop(run_id, None)
        now = _now().isoformat()
        await self._repository.fail_run(run_id, error.model_dump_json(), now)

    async def _finish_cancelled(self, run_id: UUID) -> None:
        self._ephemeral_results.pop(run_id, None)
        now = _now().isoformat()
        await self._repository.cancel_run(run_id, now)

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
        audit_arguments = sanitize_audit_payload(
            arguments,
            capability.input_schema,
            allow_schema_public=entry.audit_public_fields_allowed,
        )
        await self._repository.start_tool_call(
            {
                "tool_call_id": str(call_id),
                "skill_run_id": str(run_id),
                "adapter": entry.adapter.kind,
                "method": capability.adapter_tool or entry.adapter.target,
                "request_json": json.dumps(audit_arguments, ensure_ascii=False),
                "started_at": now,
            }
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
        response: JsonValue = None,
        response_schema: JsonObject | None = None,
        allow_schema_public: bool = False,
        error: StructuredError | None = None,
    ) -> None:
        now = _now().isoformat()
        audit_response = (
            sanitize_audit_payload(
                response,
                response_schema,
                allow_schema_public=allow_schema_public,
            )
            if response is not None
            else None
        )
        audit_error = (
            sanitize_audit_payload(error.model_dump(mode="json")) if error is not None else None
        )
        await self._repository.finish_tool_call(
            {
                "status": "failed" if error else "succeeded",
                "response_json": (
                    json.dumps(audit_response, ensure_ascii=False)
                    if audit_response is not None
                    else None
                ),
                "error_json": (
                    json.dumps(audit_error, ensure_ascii=False) if audit_error is not None else None
                ),
                "completed_at": now,
                "tool_call_id": str(call_id),
            }
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

    def _remember_result(self, run_id: UUID, result: SkillResult) -> None:
        self._ephemeral_results[run_id] = result
        self._ephemeral_results.move_to_end(run_id)
        while len(self._ephemeral_results) > MAX_EPHEMERAL_RESULTS:
            self._ephemeral_results.popitem(last=False)

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


def _snapshot(row: object, *, result_override: SkillResult | None = None) -> SkillRunSnapshot:
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
            "result": (
                result_override.model_dump(mode="json")
                if result_override is not None
                else (json.loads(str(values["result_json"])) if values["result_json"] else None)
            ),
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
        _reject_nonlocal_schema_references(schema)
        Draft202012Validator.check_schema(schema)
        Draft202012Validator(
            schema,
            registry=_NO_RETRIEVAL_SCHEMA_REGISTRY,
        ).validate(value)  # pyright: ignore[reportUnknownMemberType]
    except SchemaError as error:
        raise SkillExecutionError(
            "invalid_capability_schema", f"Skill {boundary} schema is invalid: {error.message}"
        ) from error
    except ValidationError as error:
        raise SkillExecutionError(
            f"invalid_skill_{boundary}",
            f"Skill {boundary} failed schema validation",
            details={"path": list(error.absolute_path)},
        ) from error
    except Unresolvable as error:
        raise SkillExecutionError(
            "invalid_capability_schema",
            f"Skill {boundary} schema contains an unresolved local reference",
        ) from error


def _reject_nonlocal_schema_references(value: object) -> None:
    if isinstance(value, dict):
        document = cast(dict[str, object], value)
        for keyword in ("$ref", "$dynamicRef", "$recursiveRef"):
            reference = document.get(keyword)
            if isinstance(reference, str) and not reference.startswith("#"):
                raise SkillExecutionError(
                    "invalid_capability_schema",
                    "Runtime Skill schemas may only use local fragment references",
                )
        for child in document.values():
            _reject_nonlocal_schema_references(child)
    elif isinstance(value, list):
        for child in cast(list[object], value):
            _reject_nonlocal_schema_references(child)


def _spoken_summary(data: JsonValue) -> str | None:
    if not isinstance(data, dict):
        return None
    summary = data.get("spoken_summary")
    return summary if isinstance(summary, str) else None


def _now() -> datetime:
    return datetime.now(UTC)


def _example_paths(skills_root: Path, example_id: str) -> tuple[Path, Path]:
    examples_root = (skills_root.parent / "plugins" / "examples").resolve()
    return (examples_root / example_id).resolve(), examples_root
