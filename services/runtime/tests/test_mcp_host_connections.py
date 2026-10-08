"""Persistence and concurrency tests for MCP Host connection state."""

from __future__ import annotations

import os
import shutil
import sys
from collections.abc import Iterable
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from dataclasses import replace
from pathlib import Path
from threading import Barrier
from typing import Protocol, cast
from unittest.mock import AsyncMock, MagicMock
from uuid import UUID, uuid4

import mcp.types as mcp_types
import pytest
from chatwaifu_protocol.skills import McpConnectionConfiguration
from chatwaifu_runtime.config.settings import Settings, StorageConfig
from chatwaifu_runtime.persistence.database import Database
from chatwaifu_runtime.persistence.sqlite_runtime_skills import SQLiteRuntimeSkillRepository
from chatwaifu_runtime.runtime_skills.errors import SkillExecutionError
from chatwaifu_runtime.runtime_skills.host_connections import (
    McpConnectionManager,
    McpConnectionSecretStore,
    _list_resource_templates,  # pyright: ignore[reportPrivateUsage]
)
from chatwaifu_runtime.runtime_skills.transports import (
    McpClientTransport,
    PreparedStdioCommand,
)
from fastapi.testclient import TestClient
from httpx2 import Response
from mcp.shared.exceptions import MCPError


class RuntimeHttpClient(Protocol):
    def post(self, url: str, *, json: object) -> Response: ...

    def put(self, url: str, *, json: object) -> Response: ...


class _ReportingSandboxLauncher:
    def prepare(
        self,
        command: PreparedStdioCommand,
        *,
        trust_level: str,
        sandbox_mode: str,
        network_policy: str,
    ) -> PreparedStdioCommand:
        del trust_level, sandbox_mode, network_policy
        return replace(command, sandbox_backend="test_enforcing_backend")

    def revoke(self, subject_id: str) -> None:
        del subject_id

    def reconcile(self, active_subject_ids: Iterable[str]) -> None:
        del active_subject_ids


@pytest.mark.asyncio
async def test_manager_persists_and_clears_actual_stdio_sandbox_backend(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "manager.db"
    database = Database(database_path, StorageConfig(database_path=database_path))
    await database.open()
    manager = McpConnectionManager(
        SQLiteRuntimeSkillRepository(database),
        tmp_path / "data",
        McpClientTransport(_ReportingSandboxLauncher()),
    )
    await manager.start()
    connection_id = uuid4()
    working_root = manager.working_root(connection_id)
    working_root.mkdir(parents=True)
    fixture = Path(__file__).parent / "fixtures" / "mcp_stdio_server.py"
    server = working_root / "server.py"
    shutil.copy2(fixture, server)
    config = McpConnectionConfiguration(
        connection_id=connection_id,
        name="Manager sandbox fixture",
        transport="stdio",
        command=[sys.executable, str(server)],
        trust_level="untrusted",
        sandbox_mode="required",
        network_policy="deny",
        timeout_seconds=5,
    )
    try:
        await manager.create(config)
        ready = await manager.test(connection_id)
        assert ready.status == "ready"
        assert ready.sandbox_backend == "test_enforcing_backend"

        server.unlink()
        with pytest.raises(SkillExecutionError):
            await manager.test(connection_id)
        failed = await manager.get(connection_id)
        assert failed.status == "error"
        # Discovery failed after the launcher selected this backend; retain the
        # observed isolation fact instead of reverting to "untested".
        assert failed.sandbox_backend == "test_enforcing_backend"

        shutil.copy2(fixture, server)
        restored = await manager.test(connection_id)
        assert restored.sandbox_backend == "test_enforcing_backend"
        updated = await manager.update(config.model_copy(update={"name": "Updated fixture"}))
        assert updated.status == "untested"
        assert updated.sandbox_backend is None
    finally:
        await database.close()


def test_secret_store_serializes_concurrent_read_modify_write(tmp_path: Path) -> None:
    store = McpConnectionSecretStore(tmp_path / "mcp-secrets.json")
    entries = [(uuid4(), f"token-{index}") for index in range(24)]
    barrier = Barrier(len(entries))

    def write(entry: tuple[UUID, str]) -> None:
        barrier.wait(timeout=5)
        store.set(*entry)

    with ThreadPoolExecutor(max_workers=len(entries)) as executor:
        list(executor.map(write, entries))

    assert {connection_id: store.get(connection_id) for connection_id, _ in entries} == {
        connection_id: token for connection_id, token in entries
    }
    if os.name != "nt":
        assert (tmp_path / "mcp-secrets.json").stat().st_mode & 0o777 == 0o600


@pytest.mark.skipif(
    sys.platform != "darwin" or shutil.which("sandbox-exec") is None,
    reason="macOS Seatbelt is unavailable",
)
def test_api_reports_actual_macos_seatbelt_backend(
    client: TestClient,
    runtime_settings: Settings,
) -> None:
    http = cast(RuntimeHttpClient, client)
    payload: dict[str, object] = {
        "name": "Seatbelt API fixture",
        "transport": "stdio",
        "command": [sys.executable, "server.py"],
        "trust_level": "untrusted",
        "sandbox_mode": "required",
        "network_policy": "deny",
        "timeout_seconds": 5,
    }
    created = http.post("/v1/mcp/connections", json=payload)
    assert created.status_code == 201, created.text
    created_body = cast(dict[str, object], created.json())
    connection_id = UUID(str(created_body["connection_id"]))
    assert created_body["sandbox_backend"] is None

    working_root = runtime_settings.data_dir / "mcp-connections" / str(connection_id)
    working_root.mkdir(parents=True, exist_ok=True)
    server = working_root / "server.py"
    shutil.copy2(Path(__file__).parent / "fixtures" / "mcp_stdio_server.py", server)
    payload["command"] = [sys.executable, str(server.resolve())]
    configured = http.put(f"/v1/mcp/connections/{connection_id}", json=payload)
    assert configured.status_code == 200, configured.text

    tested = http.post(f"/v1/mcp/connections/{connection_id}/test", json={})
    assert tested.status_code == 200, tested.text
    tested_body = cast(dict[str, object], tested.json())
    assert tested_body["status"] == "ready"
    assert tested_body["sandbox_backend"] == "macos_seatbelt"

    payload["name"] = "Seatbelt API fixture updated"
    updated = http.put(f"/v1/mcp/connections/{connection_id}", json=payload)
    assert updated.status_code == 200, updated.text
    updated_body = cast(dict[str, object], updated.json())
    assert updated_body["status"] == "untested"
    assert updated_body["sandbox_backend"] is None


@pytest.mark.asyncio
async def test_list_resource_templates_tolerates_missing_method_only_on_initial_page() -> None:
    session = AsyncMock()

    # Initial page: -32601 returns []
    session.list_resource_templates.side_effect = MCPError(code=-32601, message="Method not found")
    assert await _list_resource_templates(session) == []

    # Initial page: other error codes raise MCPError
    session.list_resource_templates.side_effect = MCPError(code=-32600, message="Invalid Request")
    with pytest.raises(MCPError) as exc_info:
        await _list_resource_templates(session)
    assert exc_info.value.code == -32600

    # Paginated: page 1 returns items + nextCursor; page 2 raises -32601 -> raises MCPError
    page1 = mcp_types.ListResourceTemplatesResult(
        resource_templates=[
            mcp_types.ResourceTemplate(
                uri_template="homeassistant://devices/{id}",
                name="Device Template",
            )
        ],
        next_cursor="cursor_page_2",
    )
    session.list_resource_templates.side_effect = [
        page1,
        MCPError(code=-32601, message="Method not found on cursor"),
    ]
    with pytest.raises(MCPError) as exc_info:
        await _list_resource_templates(session)
    assert exc_info.value.code == -32601

    # Normal pagination: page 1 + page 2 successfully collected
    page2 = mcp_types.ListResourceTemplatesResult(
        resource_templates=[
            mcp_types.ResourceTemplate(
                uri_template="homeassistant://entities/{id}",
                name="Entity Template",
            )
        ],
        next_cursor=None,
    )
    session.list_resource_templates.side_effect = [page1, page2]
    templates = await _list_resource_templates(session)
    assert len(templates) == 2
    assert templates[0].name == "Device Template"
    assert templates[1].name == "Entity Template"


@pytest.mark.asyncio
async def test_discover_preserves_tools_resources_and_prompts_when_templates_method_absent(
    tmp_path: Path,
) -> None:
    session = AsyncMock()
    server_info = MagicMock()
    server_info.name = "homeassistant"
    server_info.version = "2026.1.0"
    init_result = MagicMock(
        protocol_version="2025-03-26",
        server_info=server_info,
        capabilities=MagicMock(tools=MagicMock(), resources=MagicMock(), prompts=MagicMock()),
    )

    session.list_tools.return_value = mcp_types.ListToolsResult(
        tools=[
            mcp_types.Tool(
                name="intent__HassTurnOn",
                description="Turns on a device",
                input_schema={"type": "object"},
            )
        ]
    )
    session.list_resources.return_value = mcp_types.ListResourcesResult(
        resources=[
            mcp_types.Resource(
                uri="homeassistant://assist-context",
                name="Assist Context",
                description="Static assist context",
            )
        ]
    )
    session.list_prompts.return_value = mcp_types.ListPromptsResult(
        prompts=[mcp_types.Prompt(name="ha_prompt", description="HA Prompt")]
    )
    session.list_resource_templates.side_effect = MCPError(code=-32601, message="Method not found")

    transport = MagicMock()

    @asynccontextmanager
    async def fake_session(*args: object, **kwargs: object):
        yield (session, init_result)

    transport.connection_session = fake_session

    manager = McpConnectionManager(MagicMock(), tmp_path, transport)
    config = McpConnectionConfiguration(
        connection_id=uuid4(),
        name="HA Test",
        transport="streamable_http",
        url="http://192.168.10.216:8123/api/mcp",
        allow_remote=True,
        sandbox_mode="disabled",
        network_policy="allow",
        timeout_seconds=5,
    )

    snapshot = await manager.discover(config)
    assert len(snapshot.tools) == 1
    assert snapshot.tools[0].name == "intent__HassTurnOn"
    assert len(snapshot.resources) == 1
    assert snapshot.resources[0].uri == "homeassistant://assist-context"
    assert snapshot.resource_templates == []
    assert len(snapshot.prompts) == 1
    assert snapshot.prompts[0].name == "ha_prompt"


@pytest.mark.asyncio
@pytest.mark.parametrize("error_code", [-32600, -32000])
async def test_discover_fails_when_templates_fails_with_other_error(
    tmp_path: Path, error_code: int
) -> None:
    session = AsyncMock()
    server_info = MagicMock()
    server_info.name = "homeassistant"
    server_info.version = "2026.1.0"
    init_result = MagicMock(
        protocol_version="2025-03-26",
        server_info=server_info,
        capabilities=MagicMock(tools=MagicMock(), resources=MagicMock(), prompts=None),
    )
    session.list_tools.return_value = mcp_types.ListToolsResult(tools=[])
    session.list_resources.return_value = mcp_types.ListResourcesResult(resources=[])
    session.list_resource_templates.side_effect = MCPError(code=error_code, message="Other Error")

    transport = MagicMock()

    @asynccontextmanager
    async def fake_session(*args: object, **kwargs: object):
        yield (session, init_result)

    transport.connection_session = fake_session

    manager = McpConnectionManager(MagicMock(), tmp_path, transport)
    config = McpConnectionConfiguration(
        connection_id=uuid4(),
        name="HA Test",
        transport="streamable_http",
        url="http://192.168.10.216:8123/api/mcp",
        allow_remote=True,
        sandbox_mode="disabled",
        network_policy="allow",
        timeout_seconds=5,
    )
    with pytest.raises(SkillExecutionError) as exc_info:
        await manager.discover(config)
    assert exc_info.value.structured.code == "mcp_connection_failed"


@pytest.mark.asyncio
async def test_discover_fails_when_templates_fails_on_later_page(tmp_path: Path) -> None:
    session = AsyncMock()
    server_info = MagicMock()
    server_info.name = "homeassistant"
    server_info.version = "2026.1.0"
    init_result = MagicMock(
        protocol_version="2025-03-26",
        server_info=server_info,
        capabilities=MagicMock(tools=MagicMock(), resources=MagicMock(), prompts=None),
    )
    session.list_tools.return_value = mcp_types.ListToolsResult(tools=[])
    session.list_resources.return_value = mcp_types.ListResourcesResult(resources=[])
    page1 = mcp_types.ListResourceTemplatesResult(
        resource_templates=[
            mcp_types.ResourceTemplate(
                uri_template="homeassistant://devices/{id}",
                name="Device Template",
            )
        ],
        next_cursor="cursor_page_2",
    )
    session.list_resource_templates.side_effect = [
        page1,
        MCPError(code=-32601, message="Method not found on cursor"),
    ]

    transport = MagicMock()

    @asynccontextmanager
    async def fake_session(*args: object, **kwargs: object):
        yield (session, init_result)

    transport.connection_session = fake_session

    manager = McpConnectionManager(MagicMock(), tmp_path, transport)
    config = McpConnectionConfiguration(
        connection_id=uuid4(),
        name="HA Test",
        transport="streamable_http",
        url="http://192.168.10.216:8123/api/mcp",
        allow_remote=True,
        sandbox_mode="disabled",
        network_policy="allow",
        timeout_seconds=5,
    )
    with pytest.raises(SkillExecutionError) as exc_info:
        await manager.discover(config)
    assert exc_info.value.structured.code == "mcp_connection_failed"


@pytest.mark.asyncio
async def test_discover_fails_when_required_list_tools_returns_missing_method(
    tmp_path: Path,
) -> None:
    session = AsyncMock()
    server_info = MagicMock()
    server_info.name = "homeassistant"
    server_info.version = "2026.1.0"
    init_result = MagicMock(
        protocol_version="2025-03-26",
        server_info=server_info,
        capabilities=MagicMock(tools=MagicMock(), resources=None, prompts=None),
    )
    session.list_tools.side_effect = MCPError(code=-32601, message="Method not found")

    transport = MagicMock()

    @asynccontextmanager
    async def fake_session(*args: object, **kwargs: object):
        yield (session, init_result)

    transport.connection_session = fake_session

    manager = McpConnectionManager(MagicMock(), tmp_path, transport)
    config = McpConnectionConfiguration(
        connection_id=uuid4(),
        name="HA Test",
        transport="streamable_http",
        url="http://192.168.10.216:8123/api/mcp",
        allow_remote=True,
        sandbox_mode="disabled",
        network_policy="allow",
        timeout_seconds=5,
    )
    with pytest.raises(SkillExecutionError) as exc_info:
        await manager.discover(config)
    assert exc_info.value.structured.code == "mcp_connection_failed"
