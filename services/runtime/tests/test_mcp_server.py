"""Protocol-level tests for the loopback ChatWaifu MCP server."""

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from typing import Any, cast
from uuid import UUID, uuid4

import mcp.types as mcp_types
import pytest
from chatwaifu_protocol.skills import SkillRunState
from chatwaifu_runtime.bootstrap.container import RuntimeContainer
from chatwaifu_runtime.config.settings import SecurityConfig, Settings
from chatwaifu_runtime.main import create_app
from fastapi import FastAPI
from httpx2 import ASGITransport, AsyncClient
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client
from pydantic import SecretStr


@pytest.mark.asyncio
async def test_mcp_lists_reads_prompts_and_calls_read_only_skill(
    runtime_settings: Settings,
) -> None:
    app = create_app(runtime_settings)
    async with app.router.lifespan_context(app):
        container = cast(RuntimeContainer, app.state.container)
        await container.runtime_skills.install_example_plugin("local-echo")

        async with _mcp_session(app) as session:
            tools = await session.list_tools()
            tool_names = {tool.name for tool in tools.tools}
            assert "runtime_status__read" in tool_names
            assert "local_echo__echo" in tool_names
            assert "local_echo__append_note" not in tool_names
            runtime_tool = next(tool for tool in tools.tools if tool.name == "runtime_status__read")
            assert runtime_tool.input_schema["properties"]["session_id"]["format"] == "uuid"

            resources = await session.list_resources()
            assert {str(resource.uri) for resource in resources.resources} == {
                "runtime://characters",
                "runtime://status",
            }
            status = await session.read_resource("runtime://status")
            status_content = status.contents[0]
            assert isinstance(status_content, mcp_types.TextResourceContents)
            assert '"mcp_transport": "streamable_http"' in status_content.text
            assert "api_key" not in status_content.text
            assert str(runtime_settings.data_dir) not in status_content.text

            characters = await session.read_resource("runtime://characters")
            character_content = characters.contents[0]
            assert isinstance(character_content, mcp_types.TextResourceContents)
            assert '"character_id": "default"' in character_content.text
            assert "system_prompt" not in character_content.text

            prompts = await session.list_prompts()
            assert [prompt.name for prompt in prompts.prompts] == ["chatwaifu-character-turn"]
            prompt = await session.get_prompt(
                "chatwaifu-character-turn",
                arguments={"character_id": "default", "user_message": "今天过得怎么样？"},
            )
            prompt_content = prompt.messages[0].content
            assert isinstance(prompt_content, mcp_types.TextContent)
            assert "今天过得怎么样？" in prompt_content.text
            assert "system_prompt" not in prompt_content.text

            result = await session.call_tool("runtime_status__read", {})
            assert result.is_error is False
            structured = cast(dict[str, Any], result.structured_content)
            assert structured["status"] == "succeeded"
            assert cast(dict[str, Any], structured["data"])["persistence"] == "sqlite_wal"

            hidden = await session.call_tool(
                "local_echo__append_note", {"session_id": str(uuid4()), "text": "no"}
            )
            assert hidden.is_error is True
            hidden_body = cast(dict[str, Any], hidden.structured_content)
            assert cast(dict[str, Any], hidden_body["error"])["code"] == "tool_unavailable"


@pytest.mark.asyncio
async def test_authenticated_mcp_requires_session_and_returns_pending_confirmation(
    runtime_settings: Settings,
) -> None:
    token = "test-only-mcp-admin-token"
    protected_settings = runtime_settings.model_copy(
        update={"security": SecurityConfig(admin_token=SecretStr(token))}
    )
    app = create_app(protected_settings)
    async with app.router.lifespan_context(app):
        container = cast(RuntimeContainer, app.state.container)
        await container.runtime_skills.install_example_plugin("local-echo")

        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://127.0.0.1:8765"
        ) as http:
            unauthorized = await http.post("/mcp", json={})
        assert unauthorized.status_code == 401
        assert token not in unauthorized.text

        async with _mcp_session(app, token=token) as session:
            tools = await session.list_tools()
            assert "local_echo__append_note" in {tool.name for tool in tools.tools}

            missing_session = await session.call_tool(
                "local_echo__append_note", {"text": "not written"}
            )
            assert missing_session.is_error is True
            missing_body = cast(dict[str, Any], missing_session.structured_content)
            assert cast(dict[str, Any], missing_body["error"])["code"] == "session_required"

            unknown_session = await session.call_tool(
                "local_echo__append_note",
                {"session_id": str(uuid4()), "text": "not written"},
            )
            assert unknown_session.is_error is True
            unknown_body = cast(dict[str, Any], unknown_session.structured_content)
            assert cast(dict[str, Any], unknown_body["error"])["code"] == "unknown_session"

            chat_session = await container.sessions.create_session("default")
            pending = await session.call_tool(
                "local_echo__append_note",
                {"session_id": str(chat_session.session_id), "text": "requires approval"},
            )
            assert pending.is_error is False
            pending_body = cast(dict[str, Any], pending.structured_content)
            assert pending_body["status"] == "pending_confirmation"
            assert pending_body["confirmation_request_id"]
            run = await container.runtime_skills.get_run(UUID(str(pending_body["skill_run_id"])))
            assert run.state is SkillRunState.WAITING_FOR_CONFIRMATION
            assert run.result is None


@pytest.mark.asyncio
async def test_mcp_tool_waits_on_run_lifecycle_instead_of_lossy_event_subscription(
    runtime_settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = create_app(runtime_settings)
    async with app.router.lifespan_context(app):
        container = cast(RuntimeContainer, app.state.container)

        def reject_subscription(*_args: object, **_kwargs: object) -> object:
            raise AssertionError("MCP tool execution must not subscribe to EventHub")

        monkeypatch.setattr(container.event_hub, "subscribe", reject_subscription)
        async with _mcp_session(app) as session:
            result = await session.call_tool("runtime_status__read", {})

        assert result.is_error is False
        body = cast(dict[str, Any], result.structured_content)
        assert body["status"] == "succeeded"


@pytest.mark.asyncio
async def test_mcp_rejects_non_loopback_host_and_origin(runtime_settings: Settings) -> None:
    app = create_app(runtime_settings)
    async with app.router.lifespan_context(app):
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://127.0.0.1:8765"
        ) as http:
            bad_host = await http.post(
                "/mcp",
                json={},
                headers={"Host": "example.com", "Content-Type": "application/json"},
            )
            bad_origin = await http.post(
                "/mcp",
                json={},
                headers={
                    "Origin": "https://example.com",
                    "Content-Type": "application/json",
                },
            )
        assert bad_host.status_code in (403, 421)
        assert bad_origin.status_code == 403


@asynccontextmanager
async def _mcp_session(app: FastAPI, *, token: str | None = None) -> AsyncGenerator[ClientSession]:
    if token is not None:
        auth_token = token
    else:
        auth_token = getattr(app.state.container, "capability_token", None)
    headers = {"Authorization": f"Bearer {auth_token}"} if auth_token is not None else None
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://127.0.0.1:8765",
        headers=headers,
    ) as http:
        async with streamable_http_client("http://127.0.0.1:8765/mcp", http_client=http) as streams:
            read_stream, write_stream = streams
            async with ClientSession(read_stream, write_stream) as session:
                await session.initialize()
                yield session
