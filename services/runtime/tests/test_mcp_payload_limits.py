"""Shared MCP JSON response-size boundary tests."""

from __future__ import annotations

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Literal, cast
from uuid import uuid4

import mcp.types as mcp_types
import pytest
from chatwaifu_protocol.skills import McpConnectionConfiguration
from chatwaifu_runtime.config.settings import StorageConfig
from chatwaifu_runtime.persistence.database import Database
from chatwaifu_runtime.persistence.sqlite_runtime_skills import SQLiteRuntimeSkillRepository
from chatwaifu_runtime.runtime_skills.adapters import normalize_tool_result
from chatwaifu_runtime.runtime_skills.errors import SkillExecutionError
from chatwaifu_runtime.runtime_skills.host_connections import McpConnectionManager
from chatwaifu_runtime.runtime_skills.transports import (
    MAX_MCP_JSON_PAYLOAD_BYTES,
    McpClientTransport,
)
from mcp import ClientSession
from mcp.types import InitializeResult

_OVERSIZED_TEXT = "x" * (MAX_MCP_JSON_PAYLOAD_BYTES + 1_024)


def test_tool_result_rejects_oversized_normalized_json() -> None:
    result = mcp_types.CallToolResult(
        content=[],
        structured_content={"value": _OVERSIZED_TEXT},
    )

    with pytest.raises(SkillExecutionError) as raised:
        normalize_tool_result(result)

    assert raised.value.structured.code == "mcp_response_limit"


@pytest.mark.asyncio
async def test_manager_rejects_oversized_resource_prompt_and_discovery(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "payload-limits.db"
    database = Database(database_path, StorageConfig(database_path=database_path))
    await database.open()
    transport = _OversizedTransport()
    manager = McpConnectionManager(
        SQLiteRuntimeSkillRepository(database), tmp_path / "data", transport
    )
    await manager.start()
    config = McpConnectionConfiguration(
        connection_id=uuid4(),
        name="Oversized response fixture",
        transport="streamable_http",
        url="http://127.0.0.1:1/mcp",
        allow_remote=False,
        sandbox_mode="disabled",
        network_policy="loopback",
        timeout_seconds=5,
    )
    await manager.create(config)
    try:
        transport.response = "resource"
        with pytest.raises(SkillExecutionError) as resource_error:
            await manager.read_resource(config.connection_id, "fixture://oversized")
        assert resource_error.value.structured.code == "mcp_response_limit"

        transport.response = "prompt"
        with pytest.raises(SkillExecutionError) as prompt_error:
            await manager.get_prompt(config.connection_id, "oversized", {})
        assert prompt_error.value.structured.code == "mcp_response_limit"

        transport.response = "discovery"
        with pytest.raises(SkillExecutionError) as discovery_error:
            await manager.test(config.connection_id)
        assert discovery_error.value.structured.code == "mcp_response_limit"
        failed = await manager.get(config.connection_id)
        assert failed.status == "error"
        assert failed.last_error is not None
        assert "2097152-byte limit" in failed.last_error
    finally:
        await database.close()


class _OversizedSession:
    def __init__(self, transport: _OversizedTransport) -> None:
        self._transport = transport

    async def read_resource(self, _uri: str) -> mcp_types.ReadResourceResult:
        return mcp_types.ReadResourceResult(
            contents=[
                mcp_types.TextResourceContents(
                    uri="fixture://oversized",
                    mime_type="text/plain",
                    text=_OVERSIZED_TEXT,
                )
            ]
        )

    async def get_prompt(self, _name: str, _arguments: dict[str, str]) -> mcp_types.GetPromptResult:
        return mcp_types.GetPromptResult(
            messages=[
                mcp_types.PromptMessage(
                    role="user",
                    content=mcp_types.TextContent(type="text", text=_OVERSIZED_TEXT),
                )
            ]
        )

    async def list_tools(
        self, *, params: mcp_types.PaginatedRequestParams | None = None
    ) -> mcp_types.ListToolsResult:
        del params
        return mcp_types.ListToolsResult(
            tools=[
                mcp_types.Tool(
                    name="oversized",
                    description="Oversized discovery schema",
                    input_schema={
                        "type": "object",
                        "properties": {"value": {"type": "string", "description": _OVERSIZED_TEXT}},
                    },
                )
            ]
        )


class _OversizedTransport(McpClientTransport):
    def __init__(self) -> None:
        super().__init__()
        self.response: Literal["resource", "prompt", "discovery"] = "resource"
        self._session = _OversizedSession(self)

    @asynccontextmanager
    async def connection_session(
        self,
        config: McpConnectionConfiguration,
        *,
        bearer_token: str | None,
        working_root: Path,
    ) -> AsyncGenerator[tuple[ClientSession, InitializeResult]]:
        del config, bearer_token, working_root
        capabilities = mcp_types.ServerCapabilities(
            tools=mcp_types.ToolsCapability() if self.response == "discovery" else None
        )
        initialized = InitializeResult(
            protocol_version="2025-11-25",
            capabilities=capabilities,
            server_info=mcp_types.Implementation(name="oversized-fixture", version="1.0.0"),
        )
        yield cast(ClientSession, self._session), initialized
