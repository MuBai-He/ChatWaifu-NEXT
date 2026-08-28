"""Builtin and official-SDK MCP Runtime Skill adapters."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import cast

from chatwaifu_protocol.base import JsonObject
from chatwaifu_protocol.skills import McpConnectionConfiguration, PluginManifest
from mcp.types import CallToolResult

from chatwaifu_runtime.runtime_skills.errors import SkillExecutionError
from chatwaifu_runtime.runtime_skills.transports import McpClientTransport, SandboxLauncher

BuiltinHandler = Callable[[JsonObject], Awaitable[JsonObject]]


class BuiltinAdapter:
    def __init__(self) -> None:
        self._handlers: dict[str, BuiltinHandler] = {}

    def register(self, name: str, handler: BuiltinHandler) -> None:
        if name in self._handlers:
            raise ValueError(f"duplicate builtin skill handler: {name}")
        self._handlers[name] = handler

    async def invoke(self, name: str, arguments: JsonObject) -> JsonObject:
        handler = self._handlers.get(name)
        if handler is None:
            raise SkillExecutionError("adapter_not_found", f"Builtin handler is missing: {name}")
        return await handler(arguments)


class McpStdioAdapter:
    """Invoke installed stdio plugins through the official MCP client SDK."""

    def __init__(self, sandbox_launcher: SandboxLauncher | None = None) -> None:
        self._transport = McpClientTransport(sandbox_launcher)

    async def invoke(
        self,
        *,
        plugin: PluginManifest,
        plugin_root: Path,
        tool: str,
        arguments: JsonObject,
        timeout_seconds: float,
    ) -> JsonObject:
        # The official stdio transport reserves a bounded process-reaping window.
        # Keep the persisted run terminal within its advertised deadline as well.
        operation_timeout = max(0.1, timeout_seconds - min(0.25, timeout_seconds * 0.1))
        try:
            async with asyncio.timeout(operation_timeout):
                async with self._transport.plugin_session(plugin, plugin_root) as (
                    session,
                    _,
                ):
                    result = await session.call_tool(tool, arguments)
        except TimeoutError as error:
            raise SkillExecutionError(
                "skill_timeout",
                f"Plugin tool exceeded {timeout_seconds:g}s timeout",
                retryable=True,
                details={"plugin_id": plugin.plugin_id, "tool": tool},
            ) from error
        except asyncio.CancelledError:
            raise
        except SkillExecutionError:
            raise
        except Exception as error:
            raise SkillExecutionError(
                "plugin_transport_error",
                "MCP plugin transport failed",
                retryable=True,
                details={"plugin_id": plugin.plugin_id, "tool": tool},
            ) from error
        return normalize_tool_result(result)


class McpConnectionAdapter:
    """Invoke discovered tools on a persisted MCP Host connection."""

    def __init__(self, transport: McpClientTransport) -> None:
        self._transport = transport

    async def invoke(
        self,
        *,
        config: McpConnectionConfiguration,
        bearer_token: str | None,
        working_root: Path,
        tool: str,
        arguments: JsonObject,
        timeout_seconds: float,
    ) -> JsonObject:
        try:
            async with asyncio.timeout(timeout_seconds):
                async with self._transport.connection_session(
                    config,
                    bearer_token=bearer_token,
                    working_root=working_root,
                ) as (session, _):
                    result = await session.call_tool(tool, arguments)
        except TimeoutError as error:
            raise SkillExecutionError(
                "skill_timeout",
                f"MCP tool exceeded {timeout_seconds:g}s timeout",
                retryable=True,
                details={"connection_id": str(config.connection_id), "tool": tool},
            ) from error
        except asyncio.CancelledError:
            raise
        except SkillExecutionError:
            raise
        except Exception as error:
            raise SkillExecutionError(
                "mcp_transport_error",
                "MCP connection failed while invoking a tool",
                retryable=True,
                details={"connection_id": str(config.connection_id), "tool": tool},
            ) from error
        return normalize_tool_result(result)


def normalize_tool_result(result: object) -> JsonObject:
    if not isinstance(result, CallToolResult):
        raise SkillExecutionError(
            "invalid_mcp_result", "MCP server returned an invalid tool result"
        )
    if result.is_error:
        raise SkillExecutionError("mcp_tool_failed", _content_text(result))
    structured_content = cast(object, result.structured_content)
    if isinstance(structured_content, dict):
        typed_content = cast(dict[str, object], structured_content)
        return cast(JsonObject, typed_content)
    serialized = result.model_dump(mode="json", by_alias=True, exclude_none=True)
    return cast(JsonObject, {"content": serialized.get("content", [])})


def _content_text(result: CallToolResult) -> str:
    texts: list[str] = []
    for item in result.content:
        text = getattr(item, "text", None)
        if isinstance(text, str):
            texts.append(text)
    return " ".join(texts) or "MCP tool reported an error"
