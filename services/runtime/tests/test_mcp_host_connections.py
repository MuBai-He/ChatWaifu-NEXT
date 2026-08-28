"""Persistence and concurrency tests for MCP Host connection state."""

from __future__ import annotations

import shutil
import sys
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from pathlib import Path
from threading import Barrier
from typing import Protocol, cast
from uuid import UUID, uuid4

import pytest
from chatwaifu_protocol.skills import McpConnectionConfiguration
from chatwaifu_runtime.config.settings import Settings, StorageConfig
from chatwaifu_runtime.persistence.database import Database
from chatwaifu_runtime.runtime_skills.errors import SkillExecutionError
from chatwaifu_runtime.runtime_skills.host_connections import (
    McpConnectionManager,
    McpConnectionSecretStore,
)
from chatwaifu_runtime.runtime_skills.transports import (
    McpClientTransport,
    PreparedStdioCommand,
)
from fastapi.testclient import TestClient
from httpx2 import Response


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


@pytest.mark.asyncio
async def test_manager_persists_and_clears_actual_stdio_sandbox_backend(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "manager.db"
    database = Database(database_path, StorageConfig(database_path=database_path))
    await database.open()
    manager = McpConnectionManager(
        database,
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
        assert failed.sandbox_backend is None

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
