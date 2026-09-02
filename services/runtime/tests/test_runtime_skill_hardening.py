"""Security and lifecycle regressions for Runtime Skills."""

from __future__ import annotations

import asyncio
import hashlib
import json
import shutil
import sqlite3
import time
import urllib.request
from collections.abc import Iterable
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from pathlib import Path
from threading import Barrier
from typing import Protocol, cast
from uuid import uuid4

import mcp.types as mcp_types
import pytest
from chatwaifu_protocol.base import JsonObject, SideEffect
from chatwaifu_protocol.skills import (
    McpConnectionConfiguration,
    PluginSnapshot,
    SkillCapability,
    SkillDefinition,
    SkillInvocation,
    SkillRunState,
)
from chatwaifu_runtime.bootstrap.container import RuntimeContainer
from chatwaifu_runtime.config.settings import Settings, StorageConfig
from chatwaifu_runtime.mcp_server import allocate_mcp_tool_names
from chatwaifu_runtime.persistence.database import Database
from chatwaifu_runtime.persistence.sqlite_runtime_skills import SQLiteRuntimeSkillRepository
from chatwaifu_runtime.runtime_skills import permissions as permission_policy
from chatwaifu_runtime.runtime_skills import service as runtime_skill_service
from chatwaifu_runtime.runtime_skills.adapters import normalize_tool_result
from chatwaifu_runtime.runtime_skills.audit import (
    confirmation_argument_preview,
    sanitize_audit_payload,
)
from chatwaifu_runtime.runtime_skills.errors import SkillExecutionError
from chatwaifu_runtime.runtime_skills.host_connections import (
    McpConnectionManager,
    McpConnectionSecretStore,
    McpSecretMutationJournal,
)
from chatwaifu_runtime.runtime_skills.transports import (
    McpClientTransport,
    PreparedStdioCommand,
    mcp_connection_sandbox_subject_id,
    plugin_sandbox_subject_id,
)
from fastapi.testclient import TestClient
from httpx2 import Response


class RuntimeHttpClient(Protocol):
    def get(self, url: str) -> Response: ...

    def post(self, url: str, *, json: object) -> Response: ...

    def put(self, url: str, *, json: object) -> Response: ...


class _RecordingSandboxLauncher:
    def __init__(self) -> None:
        self.reconciled: list[tuple[str, ...]] = []
        self.revoked: list[str] = []

    def prepare(
        self,
        command: PreparedStdioCommand,
        *,
        trust_level: str,
        sandbox_mode: str,
        network_policy: str,
    ) -> PreparedStdioCommand:
        del trust_level, sandbox_mode, network_policy
        return command

    def revoke(self, subject_id: str) -> None:
        self.revoked.append(subject_id)

    def reconcile(self, active_subject_ids: Iterable[str]) -> None:
        self.reconciled.append(tuple(active_subject_ids))


def _prepare_sandbox_lifecycle_plugin(destination: Path) -> None:
    shutil.copytree(
        Path(__file__).resolve().parents[3] / "plugins" / "examples" / "local-echo",
        destination,
    )
    manifest_path = destination / "plugin.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["transport"].update(
        {
            "trust_level": "untrusted",
            "sandbox_mode": "required",
            "network_policy": "deny",
        }
    )
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def test_audit_payload_is_schema_aware_redacted_and_bounded() -> None:
    secret = "do-not-persist-this-token"
    payload: JsonObject = {
        "api_key": secret,
        "profile": {"display_name": "Nene", "credential": secret},
        "nested": {"password": secret},
        "providerClientSecret": secret,
        "file_content": "private file body",
        "email": "nene@example.invalid",
        "ref_secret": secret,
    }
    schema: JsonObject = {
        "type": "object",
        "properties": {
            "api_key": {"type": "string", "writeOnly": True},
            "profile": {
                "type": "object",
                "properties": {
                    "display_name": {
                        "type": "string",
                        "x-chatwaifu-audit-public": True,
                    },
                    "credential": {"type": "string", "x-chatwaifu-sensitive": True},
                },
            },
            "file_content": {
                "type": "string",
                "x-chatwaifu-audit-public": True,
            },
            "email": {
                "type": "string",
                "format": "email",
                "x-chatwaifu-audit-public": True,
            },
            "ref_secret": {
                "allOf": [
                    {"$ref": "#/$defs/write_only_value"},
                    {"x-chatwaifu-audit-public": True},
                ]
            },
        },
        "$defs": {"write_only_value": {"type": "string", "writeOnly": True}},
    }

    sanitized = sanitize_audit_payload(payload, schema, allow_schema_public=True)
    summary = cast(dict[str, object], sanitized)
    encoded = json.dumps(sanitized, ensure_ascii=False)
    assert secret not in encoded
    assert "private file body" not in encoded
    assert "nene@example.invalid" not in encoded
    public = cast(dict[str, object], summary["public"])
    assert cast(dict[str, object], public["profile"])["display_name"] == "Nene"
    assert "api_key" not in summary
    assert "ref_secret" not in public
    assert "sha256" not in summary

    untrusted = cast(dict[str, object], sanitize_audit_payload(payload, schema))
    assert "public" not in untrusted

    bounded = sanitize_audit_payload({"text": "x" * 4_096}, max_bytes=128)
    assert cast(dict[str, object], bounded)["_audit_summary"] is True
    assert len(json.dumps(bounded).encode()) <= 128


def test_confirmation_argument_preview_is_informative_secret_safe_and_bounded() -> None:
    secret = "do-not-show-this-token"
    arguments: JsonObject = {
        "query": "搜索 Python 的最新消息",
        "message": "把这段普通文本发送给搜索服务",
        "url": "https://search.example.invalid/",
        "api_key": secret,
        "sessionToken": secret,
        "nested": {"providerClientSecret": secret},
        "opaque_field": secret,
        "positional": [secret, "visible item"],
    }
    schema: JsonObject = {
        "type": "object",
        "properties": {
            "query": {"type": "string"},
            "message": {"type": "string"},
            "url": {"type": "string", "format": "uri"},
            "api_key": {"type": "string", "writeOnly": True},
            "nested": {
                "type": "object",
                "properties": {"providerClientSecret": {"type": "string"}},
            },
            "opaque_field": {"type": "string"},
            "positional": {
                "type": "array",
                "prefixItems": [
                    {"type": "string", "writeOnly": True},
                    {"type": "string"},
                ],
            },
        },
        "patternProperties": {"^opaque_": {"type": "string", "x-chatwaifu-sensitive": True}},
    }

    preview = confirmation_argument_preview(arguments, schema)
    text = cast(str, preview["text"])

    assert "搜索 Python 的最新消息" in text
    assert "把这段普通文本发送给搜索服务" in text
    assert "https://search.example.invalid/" in text
    assert "visible item" in text
    assert secret not in text
    assert text.count("[REDACTED]") == 5
    assert preview["redacted"] is True
    assert preview["truncated"] is False

    bounded = confirmation_argument_preview(
        {"message": "宁宁" * 4_096},
        {"type": "object", "properties": {"message": {"type": "string"}}},
        max_bytes=256,
    )
    assert bounded["truncated"] is True
    assert len(cast(str, bounded["text"]).encode("utf-8")) <= 256


def test_external_schema_references_are_rejected_without_io(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    io_attempts: list[object] = []

    def fail_urlopen(*args: object, **_kwargs: object) -> object:
        io_attempts.extend(args)
        raise AssertionError("schema validation attempted external I/O")

    monkeypatch.setattr(urllib.request, "urlopen", fail_urlopen)

    for keyword, reference in (
        ("$ref", "https://schemas.example.invalid/runtime-skill.json"),
        ("$dynamicRef", "file:///tmp/private-runtime-skill-schema.json"),
        ("$recursiveRef", "../outside-runtime-skill-schema.json"),
    ):
        with pytest.raises(SkillExecutionError) as raised:
            runtime_skill_service._validate_schema(  # pyright: ignore[reportPrivateUsage]
                {"type": "object", "allOf": [{keyword: reference}]},
                {},
                "input",
            )
        assert raised.value.structured.code == "invalid_capability_schema"

    runtime_skill_service._validate_schema(  # pyright: ignore[reportPrivateUsage]
        {
            "$defs": {"value": {"type": "string"}},
            "$ref": "#/$defs/value",
        },
        "local-only",
        "input",
    )
    assert io_attempts == []


@pytest.mark.parametrize("boundary", ["input", "output"])
@pytest.mark.parametrize("value", [float("nan"), float("inf"), float("-inf")])
def test_runtime_skill_schema_gateway_rejects_non_finite_numbers(
    boundary: str, value: float
) -> None:
    with pytest.raises(SkillExecutionError) as raised:
        runtime_skill_service._validate_schema(  # pyright: ignore[reportPrivateUsage]
            {
                "type": "object",
                "properties": {"nested": {"type": "array", "items": {"type": "number"}}},
            },
            {"nested": [value]},
            boundary,
        )

    assert raised.value.structured.code == f"invalid_skill_{boundary}"
    assert raised.value.structured.details == {"path": ["nested", 0]}


@pytest.mark.asyncio
async def test_runtime_skill_gateway_rejects_oversized_provider_call_id_before_persistence(
    runtime_settings: Settings,
) -> None:
    container = RuntimeContainer(runtime_settings)
    await container.start()
    try:
        session = await container.sessions.create_session("default")
        with pytest.raises(ValueError, match="provider tool call id exceeded"):
            await container.runtime_skills.invoke(
                session.session_id,
                SkillInvocation(skill_id="runtime.status", capability="read", arguments={}),
                origin="agent",
                provider_tool_call_id="x" * 257,
            )
        with pytest.raises(SkillExecutionError) as non_finite:
            await container.runtime_skills.invoke(
                session.session_id,
                SkillInvocation(
                    skill_id="runtime.status",
                    capability="read",
                    arguments={"value": float("nan")},
                ),
                origin="agent",
                provider_tool_call_id="call_finite_guard",
            )

        assert non_finite.value.structured.code == "invalid_skill_input"
        assert await container.runtime_skills.list_runs(session.session_id) == []
    finally:
        await container.stop()


def test_mcp_error_mapping_does_not_echo_untrusted_content() -> None:
    secret = "server-echoed-secret"
    result = mcp_types.CallToolResult(
        content=[mcp_types.TextContent(type="text", text=secret)],
        is_error=True,
    )

    with pytest.raises(SkillExecutionError) as raised:
        normalize_tool_result(result)

    assert raised.value.structured.code == "mcp_tool_failed"
    assert secret not in raised.value.structured.message


def test_mcp_tool_name_allocator_is_stable_and_collision_free() -> None:
    first = _definition("same.name", "read")
    second = _definition("same-name", "read")
    long_prefix = "x" * 127
    third = _definition(f"{long_prefix}a", "read")
    fourth = _definition(f"{long_prefix}b", "read")
    inputs = [
        (definition, definition.capabilities[0]) for definition in (first, second, third, fourth)
    ]

    first_allocation = allocate_mcp_tool_names(inputs)
    second_allocation = allocate_mcp_tool_names(inputs)
    names = [name for _, _, name in first_allocation]

    assert names == [name for _, _, name in second_allocation]
    assert len(names) == len(set(names))
    assert all(len(name) <= 128 for name in names)


def test_background_invocation_requires_declared_support(client: TestClient) -> None:
    http = cast(RuntimeHttpClient, client)
    session = cast(dict[str, object], http.post("/v1/sessions", json={}).json())
    response = http.post(
        f"/v1/sessions/{session['session_id']}/skill-runs",
        json={
            "skill_id": "runtime.status",
            "capability": "read",
            "arguments": {},
            "background": True,
        },
    )

    assert response.status_code == 409
    assert "background" in response.text


def test_secret_arguments_never_enter_run_or_tool_audit_rows(
    client: TestClient,
    runtime_settings: Settings,
    tmp_path: Path,
) -> None:
    http = cast(RuntimeHttpClient, client)
    source = tmp_path / "secure-echo"
    fixture = Path(__file__).resolve().parents[3] / "plugins" / "examples" / "local-echo"
    shutil.copytree(fixture, source)
    plugin_manifest = json.loads((source / "plugin.json").read_text(encoding="utf-8"))
    plugin_manifest["plugin_id"] = "secure.echo"
    (source / "plugin.json").write_text(
        json.dumps(plugin_manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    skill_manifest = (source / "chatwaifu.yaml").read_text(encoding="utf-8")
    skill_manifest = skill_manifest.replace('"skill_id": "local.echo"', '"skill_id": "secure.echo"')
    skill_manifest = skill_manifest.replace(
        '"additionalProperties": false,', '"additionalProperties": true,', 1
    )
    skill_manifest = skill_manifest.replace(
        '"type": "string", "minLength": 1, "maxLength": 2000',
        '"type": "string", "minLength": 1, "maxLength": 2000, "x-chatwaifu-audit-public": true',
        1,
    )
    skill_manifest = skill_manifest.replace(
        '"echo": { "type": "string" }',
        '"echo": { "type": "string", "x-chatwaifu-audit-public": true }',
        1,
    )
    (source / "chatwaifu.yaml").write_text(skill_manifest, encoding="utf-8")
    instructions = (source / "SKILL.md").read_text(encoding="utf-8")
    (source / "SKILL.md").write_text(
        instructions.replace("id: local.echo", "id: secure.echo", 1),
        encoding="utf-8",
    )
    installed = http.post("/v1/plugins/install", json={"source_path": str(source)})
    assert installed.status_code == 201, installed.text
    session = cast(dict[str, object], http.post("/v1/sessions", json={}).json())
    secret = "audit-secret-must-never-persist"
    private_text = "private-file-body-and-user@example.invalid"
    invoked = cast(
        dict[str, object],
        http.post(
            f"/v1/sessions/{session['session_id']}/skill-runs",
            json={
                "skill_id": "secure.echo",
                "capability": "echo",
                "arguments": {
                    "text": "safe-runtime-result",
                    "api_key": secret,
                    "file_content": private_text,
                    "email": "user@example.invalid",
                },
            },
        ).json(),
    )
    run_id = str(invoked["skill_run_id"])
    deadline = time.monotonic() + 2
    state: object = invoked["state"]
    completed = invoked
    while state not in {"succeeded", "failed", "cancelled", "expired"}:
        assert time.monotonic() < deadline
        time.sleep(0.01)
        completed = cast(dict[str, object], http.get(f"/v1/skill-runs/{run_id}").json())
        state = completed["state"]
    assert state == "succeeded"
    runtime_data = cast(dict[str, object], cast(dict[str, object], completed["result"])["data"])
    assert runtime_data["echo"] == "safe-runtime-result"

    with sqlite3.connect(runtime_settings.database_path) as connection:
        run_row = connection.execute(
            "SELECT arguments_json, execution_plan_json, result_json FROM skill_runs "
            "WHERE skill_run_id = ?",
            (run_id,),
        ).fetchone()
        tool_row = connection.execute(
            "SELECT request_json, response_json, error_json FROM skill_tool_calls "
            "WHERE skill_run_id = ?",
            (run_id,),
        ).fetchone()
        event_rows = connection.execute(
            "SELECT payload_json, envelope_json FROM events WHERE payload_json LIKE ?",
            (f"%{run_id}%",),
        ).fetchall()
        outbox_rows = connection.execute(
            "SELECT envelope_json FROM outbox WHERE envelope_json LIKE ?",
            (f"%{run_id}%",),
        ).fetchall()
    durable = json.dumps([run_row, tool_row, event_rows, outbox_rows], ensure_ascii=False)
    assert secret not in durable
    assert private_text not in durable
    assert "user@example.invalid" not in durable
    assert "safe-runtime-result" not in durable
    assert "_audit_summary" in durable
    canonical_arguments = json.dumps(
        {
            "api_key": secret,
            "email": "user@example.invalid",
            "file_content": private_text,
            "text": "safe-runtime-result",
        },
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    assert hashlib.sha256(canonical_arguments).hexdigest() not in durable


@pytest.mark.asyncio
async def test_permission_setup_failure_compensates_created_skill_run(
    runtime_settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    container = RuntimeContainer(runtime_settings)
    await container.start()
    try:
        session = await container.sessions.create_session("default")

        async def fail_permission_lookup(**_kwargs: object) -> list[str]:
            raise RuntimeError("injected permission store failure")

        monkeypatch.setattr(
            container.runtime_skills._permissions,  # pyright: ignore[reportPrivateUsage]
            "missing_permissions",
            fail_permission_lookup,
        )
        with pytest.raises(RuntimeError, match="permission store failure"):
            await container.runtime_skills.invoke(
                session.session_id,
                SkillInvocation(
                    skill_id="runtime.status",
                    capability="read",
                    arguments={},
                ),
            )

        rows = await container.database.fetchall(
            "SELECT state, error_json FROM skill_runs WHERE session_id = ?",
            (str(session.session_id),),
        )
        assert len(rows) == 1
        assert rows[0]["state"] == "failed"
        error = json.loads(str(rows[0]["error_json"]))
        assert error["code"] == "skill_invocation_setup_failed"
        assert (
            container.runtime_skills._pending_arguments  # pyright: ignore[reportPrivateUsage]
            == {}
        )
    finally:
        await container.stop()


@pytest.mark.asyncio
async def test_runtime_reconciles_and_revokes_durable_sandbox_subjects(
    runtime_settings: Settings,
) -> None:
    connection_id = uuid4()
    first = RuntimeContainer(runtime_settings)
    first_launcher = _RecordingSandboxLauncher()
    first.runtime_skills._sandbox_launcher = first_launcher  # pyright: ignore[reportPrivateUsage]
    await first.start()
    try:
        assert first_launcher.reconciled == [()]
        plugin_source = runtime_settings.data_dir / "sandbox-lifecycle-plugin"
        await asyncio.to_thread(_prepare_sandbox_lifecycle_plugin, plugin_source)
        await first.runtime_skills.install_plugin(plugin_source)
        await first.runtime_skills.create_mcp_connection(
            McpConnectionConfiguration(
                connection_id=connection_id,
                name="Sandbox lifecycle fixture",
                transport="stdio",
                command=["fixture-server"],
                trust_level="untrusted",
                sandbox_mode="required",
                network_policy="deny",
            )
        )
    finally:
        await first.stop()

    second = RuntimeContainer(runtime_settings)
    second_launcher = _RecordingSandboxLauncher()
    second.runtime_skills._sandbox_launcher = (  # pyright: ignore[reportPrivateUsage]
        second_launcher
    )
    await second.start()
    try:
        assert second_launcher.reconciled == [
            (
                plugin_sandbox_subject_id("local.echo"),
                mcp_connection_sandbox_subject_id(connection_id),
            )
        ]

        await second.runtime_skills.set_plugin_enabled("local.echo", False)
        await second.runtime_skills.set_plugin_enabled("local.echo", True)
        await second.runtime_skills.update_mcp_connection(
            McpConnectionConfiguration(
                connection_id=connection_id,
                name="Updated sandbox lifecycle fixture",
                transport="stdio",
                command=["fixture-server"],
                trust_level="untrusted",
                sandbox_mode="required",
                network_policy="deny",
            )
        )
        await second.runtime_skills.uninstall_plugin("local.echo")
        await second.runtime_skills.delete_mcp_connection(connection_id)

        assert second_launcher.revoked == [
            plugin_sandbox_subject_id("local.echo"),
            mcp_connection_sandbox_subject_id(connection_id),
            plugin_sandbox_subject_id("local.echo"),
            mcp_connection_sandbox_subject_id(connection_id),
        ]
    finally:
        await second.stop()


def test_confirmation_expiry_marks_run_terminal_and_rejects_decision(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    http = cast(RuntimeHttpClient, client)
    session = cast(dict[str, object], http.post("/v1/sessions", json={}).json())
    assert (
        http.post("/v1/plugins/install-example", json={"example_id": "local-echo"}).status_code
        == 201
    )
    waiting = cast(
        dict[str, object],
        http.post(
            f"/v1/sessions/{session['session_id']}/skill-runs",
            json={
                "skill_id": "local.echo",
                "capability": "append_note",
                "arguments": {"text": "expires safely"},
            },
        ).json(),
    )
    future = datetime.now(UTC) + timedelta(minutes=10)
    monkeypatch.setattr(permission_policy, "_now", lambda: future)

    decided = http.post(
        f"/v1/skill-confirmations/{waiting['confirmation_request_id']}",
        json={"decision": "allow_once"},
    )

    assert decided.status_code == 409
    run = cast(dict[str, object], http.get(f"/v1/skill-runs/{waiting['skill_run_id']}").json())
    assert run["state"] == "expired"


def test_confirmation_decision_has_single_atomic_winner(client: TestClient) -> None:
    http = cast(RuntimeHttpClient, client)
    session = cast(dict[str, object], http.post("/v1/sessions", json={}).json())
    assert (
        http.post("/v1/plugins/install-example", json={"example_id": "local-echo"}).status_code
        == 201
    )
    waiting = cast(
        dict[str, object],
        http.post(
            f"/v1/sessions/{session['session_id']}/skill-runs",
            json={
                "skill_id": "local.echo",
                "capability": "append_note",
                "arguments": {"text": "exactly once"},
            },
        ).json(),
    )
    barrier = Barrier(2)

    def decide(_: int) -> int:
        barrier.wait(timeout=5)
        return http.post(
            f"/v1/skill-confirmations/{waiting['confirmation_request_id']}",
            json={"decision": "allow_once"},
        ).status_code

    with ThreadPoolExecutor(max_workers=2) as executor:
        statuses = list(executor.map(decide, range(2)))

    assert sorted(statuses) == [200, 409]


def test_confirmation_ttl_expires_without_a_poll_or_decision(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    http = cast(RuntimeHttpClient, client)
    monkeypatch.setattr(runtime_skill_service, "CONFIRMATION_TTL_SECONDS", 0.02)
    session = cast(dict[str, object], http.post("/v1/sessions", json={}).json())
    assert (
        http.post("/v1/plugins/install-example", json={"example_id": "local-echo"}).status_code
        == 201
    )
    waiting = cast(
        dict[str, object],
        http.post(
            f"/v1/sessions/{session['session_id']}/skill-runs",
            json={
                "skill_id": "local.echo",
                "capability": "append_note",
                "arguments": {"text": "automatic expiry"},
            },
        ).json(),
    )

    deadline = time.monotonic() + 1
    state: object = waiting["state"]
    while state != "expired" and time.monotonic() < deadline:
        time.sleep(0.01)
        run = cast(
            dict[str, object],
            http.get(f"/v1/skill-runs/{waiting['skill_run_id']}").json(),
        )
        state = run["state"]

    assert state == "expired"


@pytest.mark.asyncio
async def test_cancelling_pending_confirmation_atomically_invalidates_decision_and_waiter(
    runtime_settings: Settings,
) -> None:
    container = RuntimeContainer(runtime_settings)
    await container.start()
    try:
        service = container.runtime_skills
        await service.install_example_plugin("local-echo")
        session = await container.sessions.create_session("default")
        waiting = await service.invoke(
            session.session_id,
            SkillInvocation(
                skill_id="local.echo",
                capability="append_note",
                arguments={"text": "must never execute"},
            ),
        )
        assert waiting.state is SkillRunState.WAITING_FOR_CONFIRMATION
        assert waiting.confirmation_request_id is not None
        terminal_waiter = asyncio.create_task(service.wait_for_terminal(waiting.skill_run_id))

        cancelled = await service.cancel(waiting.skill_run_id)
        terminal = await asyncio.wait_for(terminal_waiter, timeout=1)

        assert cancelled.state is SkillRunState.CANCELLED
        assert terminal.state is SkillRunState.CANCELLED
        request = await service._repository.permission_request(  # pyright: ignore[reportPrivateUsage]
            waiting.confirmation_request_id
        )
        assert request is not None
        assert request["state"] == "expired"
        with pytest.raises(ValueError, match="no longer pending"):
            await service.decide_confirmation(waiting.confirmation_request_id, "allow_once")
    finally:
        await container.stop()


@pytest.mark.asyncio
async def test_bounded_cancel_prevents_late_adapter_success_from_resurrecting_run(
    runtime_settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    container = RuntimeContainer(runtime_settings)
    release_adapter = asyncio.Event()
    await container.start()
    try:
        service = container.runtime_skills
        session = await container.sessions.create_session("default")
        adapter_started = asyncio.Event()
        cancellation_swallowed = asyncio.Event()
        original_invoke = service._builtin.invoke  # pyright: ignore[reportPrivateUsage]

        async def stubborn_invoke(name: str, arguments: JsonObject) -> JsonObject:
            adapter_started.set()
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                cancellation_swallowed.set()
            await release_adapter.wait()
            return await original_invoke(name, arguments)

        monkeypatch.setattr(
            service._builtin,  # pyright: ignore[reportPrivateUsage]
            "invoke",
            stubborn_invoke,
        )
        running = await service.invoke(
            session.session_id,
            SkillInvocation(skill_id="runtime.status", capability="read", arguments={}),
        )
        await asyncio.wait_for(adapter_started.wait(), timeout=1)

        started_at = time.monotonic()
        cancelled = await asyncio.wait_for(service.cancel(running.skill_run_id), timeout=1)
        elapsed = time.monotonic() - started_at

        assert elapsed < 0.75
        assert cancelled.state is SkillRunState.CANCELLED
        assert cancelled.result is None
        await asyncio.wait_for(cancellation_swallowed.wait(), timeout=1)

        release_adapter.set()
        task = service._tasks.get(running.skill_run_id)  # pyright: ignore[reportPrivateUsage]
        if task is not None:
            await asyncio.wait_for(task, timeout=1)
        late = await service.wait_for_terminal(running.skill_run_id)
        assert late.state is SkillRunState.CANCELLED
        assert late.result is None
    finally:
        release_adapter.set()
        await container.stop()


@pytest.mark.asyncio
async def test_wait_for_terminal_observes_both_future_and_already_committed_transition(
    runtime_settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    container = RuntimeContainer(runtime_settings)
    release_adapter = asyncio.Event()
    await container.start()
    try:
        service = container.runtime_skills
        session = await container.sessions.create_session("default")
        adapter_started = asyncio.Event()
        original_invoke = service._builtin.invoke  # pyright: ignore[reportPrivateUsage]

        async def delayed_invoke(name: str, arguments: JsonObject) -> JsonObject:
            adapter_started.set()
            await release_adapter.wait()
            return await original_invoke(name, arguments)

        monkeypatch.setattr(
            service._builtin,  # pyright: ignore[reportPrivateUsage]
            "invoke",
            delayed_invoke,
        )
        running = await service.invoke(
            session.session_id,
            SkillInvocation(skill_id="runtime.status", capability="read", arguments={}),
        )
        await asyncio.wait_for(adapter_started.wait(), timeout=1)
        waiter = asyncio.create_task(service.wait_for_terminal(running.skill_run_id))
        await asyncio.sleep(0)
        release_adapter.set()

        completed = await asyncio.wait_for(waiter, timeout=1)
        already_committed = await asyncio.wait_for(
            service.wait_for_terminal(running.skill_run_id), timeout=0.1
        )

        assert completed.state is SkillRunState.SUCCEEDED
        assert already_committed.state is SkillRunState.SUCCEEDED
    finally:
        release_adapter.set()
        await container.stop()


def test_plugin_mutation_invalidates_waiting_approval(
    client: TestClient,
) -> None:
    http = cast(RuntimeHttpClient, client)
    session = cast(dict[str, object], http.post("/v1/sessions", json={}).json())
    installed = cast(
        dict[str, object],
        http.post("/v1/plugins/install-example", json={"example_id": "local-echo"}).json(),
    )
    waiting = cast(
        dict[str, object],
        http.post(
            f"/v1/sessions/{session['session_id']}/skill-runs",
            json={
                "skill_id": "local.echo",
                "capability": "append_note",
                "arguments": {"text": "must not execute after mutation"},
            },
        ).json(),
    )
    confirmation_response = http.get(f"/v1/sessions/{session['session_id']}/skill-confirmations")
    assert confirmation_response.headers["cache-control"] == "no-store"
    confirmations = cast(dict[str, object], confirmation_response.json())
    confirmation = cast(list[dict[str, object]], confirmations["items"])[0]
    argument_preview = cast(dict[str, object], confirmation["argument_preview"])
    assert "must not execute after mutation" in cast(str, argument_preview["text"])
    assert len(cast(str, argument_preview["text"]).encode("utf-8")) <= 4 * 1024
    server = Path(str(installed["install_path"])) / "server.py"
    server.chmod(0o644)
    server.write_text(
        server.read_text(encoding="utf-8") + "\n# changed after approval\n",
        encoding="utf-8",
    )

    decided = http.post(
        f"/v1/skill-confirmations/{waiting['confirmation_request_id']}",
        json={"decision": "allow_once"},
    )

    body = cast(dict[str, object], decided.json())
    assert decided.status_code == 200
    assert body["state"] == "failed"
    assert cast(dict[str, object], body["error"])["code"] == "approval_context_changed"


@pytest.mark.asyncio
@pytest.mark.parametrize("mutation", ["disable", "uninstall"])
async def test_plugin_lifecycle_mutation_waits_for_approved_execution_lease(
    runtime_settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
    mutation: str,
) -> None:
    container = RuntimeContainer(runtime_settings)
    mutation_task: asyncio.Task[object] | None = None
    allow_prelaunch = asyncio.Event()
    await container.start()
    try:
        service = container.runtime_skills
        installed = await service.install_example_plugin("local-echo")
        package_root = Path(installed.install_path)
        session = await container.sessions.create_session("default")
        prelaunch_reached = asyncio.Event()
        mutation_started = asyncio.Event()
        adapter_launched = asyncio.Event()
        original_set_backend = service._plugins.set_sandbox_backend  # pyright: ignore[reportPrivateUsage]

        async def pause_before_final_plan_check(
            plugin_id: str,
            backend: str | None,
            limits: tuple[str, ...] = (),
        ) -> object:
            snapshot = await original_set_backend(plugin_id, backend, limits)
            prelaunch_reached.set()
            await allow_prelaunch.wait()
            return snapshot

        async def invoke_approved_plugin(**kwargs: object) -> JsonObject:
            adapter_launched.set()
            arguments = cast(JsonObject, kwargs["arguments"])
            return {
                "written": True,
                "spoken_summary": str(arguments["text"]),
            }

        monkeypatch.setattr(
            service._plugins,  # pyright: ignore[reportPrivateUsage]
            "set_sandbox_backend",
            pause_before_final_plan_check,
        )
        monkeypatch.setattr(
            service._mcp,  # pyright: ignore[reportPrivateUsage]
            "invoke",
            invoke_approved_plugin,
        )

        waiting = await service.invoke(
            session.session_id,
            SkillInvocation(
                skill_id="local.echo",
                capability="append_note",
                arguments={"text": "approved package A"},
            ),
        )
        assert waiting.confirmation_request_id is not None
        await service.decide_confirmation(waiting.confirmation_request_id, "allow_once")
        await asyncio.wait_for(prelaunch_reached.wait(), timeout=1)

        async def mutate_plugin() -> object:
            mutation_started.set()
            if mutation == "disable":
                return await service.set_plugin_enabled("local.echo", False)
            return await service.uninstall_plugin("local.echo")

        mutation_task = asyncio.create_task(mutate_plugin())
        await asyncio.wait_for(mutation_started.wait(), timeout=1)

        # Execution has already checked approved package A and is paused at the
        # final pre-launch boundary. Lifecycle mutation cannot enter that gap.
        assert not mutation_task.done()
        assert await asyncio.to_thread(package_root.is_dir)
        assert (await service.list_plugins())[0].enabled is True

        allow_prelaunch.set()
        await asyncio.wait_for(adapter_launched.wait(), timeout=1)
        mutation_result = await asyncio.wait_for(mutation_task, timeout=1)
        completed = await service.get_run(waiting.skill_run_id)

        assert completed.state.value == "succeeded"
        if mutation == "disable":
            assert cast(PluginSnapshot, mutation_result).enabled is False
            assert await asyncio.to_thread(package_root.is_dir)
        else:
            assert isinstance(mutation_result, Path)
            assert not await asyncio.to_thread(package_root.exists)
            assert await service.list_plugins() == []
    finally:
        allow_prelaunch.set()
        if mutation_task is not None and not mutation_task.done():
            mutation_task.cancel()
            await asyncio.gather(mutation_task, return_exceptions=True)
        await container.stop()


def test_reusable_grant_is_bound_to_plugin_subject_and_revoked_on_change(
    client: TestClient,
    runtime_settings: Settings,
    tmp_path: Path,
) -> None:
    http = cast(RuntimeHttpClient, client)
    source = tmp_path / "grant-echo"
    fixture = Path(__file__).resolve().parents[3] / "plugins" / "examples" / "local-echo"
    shutil.copytree(fixture, source)
    plugin_manifest = json.loads((source / "plugin.json").read_text(encoding="utf-8"))
    plugin_manifest["plugin_id"] = "grant.echo"
    (source / "plugin.json").write_text(
        json.dumps(plugin_manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    skill_manifest = (source / "chatwaifu.yaml").read_text(encoding="utf-8")
    skill_manifest = skill_manifest.replace('"skill_id": "local.echo"', '"skill_id": "grant.echo"')
    skill_manifest = skill_manifest.replace(
        '"required_permissions": [],',
        '"required_permissions": ["plugin.echo.read"],',
        1,
    )
    (source / "chatwaifu.yaml").write_text(skill_manifest, encoding="utf-8")
    instructions = (source / "SKILL.md").read_text(encoding="utf-8")
    (source / "SKILL.md").write_text(
        instructions.replace("id: local.echo", "id: grant.echo", 1), encoding="utf-8"
    )
    assert http.post("/v1/plugins/install", json={"source_path": str(source)}).status_code == 201
    session_id = str(
        cast(dict[str, object], http.post("/v1/sessions", json={}).json())["session_id"]
    )

    first = cast(
        dict[str, object],
        http.post(
            f"/v1/sessions/{session_id}/skill-runs",
            json={
                "skill_id": "grant.echo",
                "capability": "echo",
                "arguments": {"text": "first"},
            },
        ).json(),
    )
    assert first["state"] == "waiting_for_confirmation"
    assert (
        http.post(
            f"/v1/skill-confirmations/{first['confirmation_request_id']}",
            json={"decision": "allow_always"},
        ).status_code
        == 200
    )
    _wait_for_run_http(http, str(first["skill_run_id"]))

    granted = cast(
        dict[str, object],
        http.post(
            f"/v1/sessions/{session_id}/skill-runs",
            json={
                "skill_id": "grant.echo",
                "capability": "echo",
                "arguments": {"text": "second"},
            },
        ).json(),
    )
    assert granted["state"] != "waiting_for_confirmation"
    _wait_for_run_http(http, str(granted["skill_run_id"]))

    assert http.put("/v1/plugins/grant.echo/enabled", json={"enabled": False}).status_code == 200
    assert http.put("/v1/plugins/grant.echo/enabled", json={"enabled": True}).status_code == 200
    invalidated = cast(
        dict[str, object],
        http.post(
            f"/v1/sessions/{session_id}/skill-runs",
            json={
                "skill_id": "grant.echo",
                "capability": "echo",
                "arguments": {"text": "third"},
            },
        ).json(),
    )
    assert invalidated["state"] == "waiting_for_confirmation"

    with sqlite3.connect(runtime_settings.database_path) as connection:
        row = connection.execute(
            "SELECT skill_version, plugin_fingerprint, subject_fingerprint, revoked_at "
            "FROM permission_grants WHERE plugin_id = 'grant.echo'"
        ).fetchone()
    assert row is not None
    assert all(row[index] for index in range(4))


@pytest.mark.asyncio
async def test_interrupted_secret_update_is_compensated_on_startup(tmp_path: Path) -> None:
    database_path = tmp_path / "runtime.db"
    database = Database(database_path, StorageConfig(database_path=database_path))
    await database.open()
    repository = SQLiteRuntimeSkillRepository(database)
    data_root = tmp_path / "data"
    manager = McpConnectionManager(repository, data_root, McpClientTransport())
    await manager.start()
    connection_id = uuid4()
    config = McpConnectionConfiguration(
        connection_id=connection_id,
        name="journal fixture",
        transport="stdio",
        command=["fixture"],
        trust_level="trusted",
        sandbox_mode="disabled",
        network_policy="allow",
    )
    try:
        await manager.create(config, bearer_token="old-token")
        revision = await manager.revision(connection_id)
        journal = McpSecretMutationJournal(data_root / "mcp-secret-mutations.json")
        secrets = McpConnectionSecretStore(data_root / "mcp-secrets.json")
        journal.prepare(
            connection_id,
            operation="update",
            previous_token="old-token",
            next_token="new-token",
            previous_revision=revision,
        )
        secrets.set(connection_id, "new-token")

        restarted = McpConnectionManager(repository, data_root, McpClientTransport())
        await restarted.start()

        assert restarted.bearer_token(connection_id) == "old-token"
        assert journal.entries() == {}
    finally:
        await database.close()


def _definition(skill_id: str, capability_name: str) -> SkillDefinition:
    return SkillDefinition(
        skill_id=skill_id,
        version="1.0.0",
        name=skill_id,
        description="collision fixture",
        capabilities=[
            SkillCapability(
                name=capability_name,
                description="read",
                input_schema={"type": "object", "properties": {}},
                output_schema={"type": "object"},
                side_effect=SideEffect.READ,
            )
        ],
    )


def _wait_for_run_http(
    http: RuntimeHttpClient, run_id: str, *, timeout: float = 2
) -> dict[str, object]:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        run = cast(dict[str, object], http.get(f"/v1/skill-runs/{run_id}").json())
        if run["state"] in {"succeeded", "failed", "cancelled", "expired"}:
            return run
        time.sleep(0.01)
    raise AssertionError(f"skill run did not finish: {run_id}")
