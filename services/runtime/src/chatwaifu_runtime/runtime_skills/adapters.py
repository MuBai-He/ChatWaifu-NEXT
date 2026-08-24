"""Builtin and isolated MCP stdio Runtime Skill adapters."""

from __future__ import annotations

import asyncio
import json
import os
import signal
import sys
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import cast

from chatwaifu_protocol.base import JsonObject
from chatwaifu_protocol.skills import PluginManifest

from chatwaifu_runtime.runtime_skills.errors import SkillExecutionError

BuiltinHandler = Callable[[JsonObject], Awaitable[JsonObject]]
MCP_PROTOCOL_VERSION = "2025-11-25"
MAX_RPC_LINE_BYTES = 1024 * 1024


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
    """One isolated child process per invocation; no process state crosses runs."""

    async def invoke(
        self,
        *,
        plugin: PluginManifest,
        plugin_root: Path,
        tool: str,
        arguments: JsonObject,
        timeout_seconds: float,
    ) -> JsonObject:
        command = _resolve_command(plugin, plugin_root)
        environment = _clean_environment(plugin.plugin_id)
        process = await asyncio.create_subprocess_exec(
            *command,
            cwd=plugin_root,
            env=environment,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            start_new_session=True,
            limit=MAX_RPC_LINE_BYTES,
        )
        try:
            return await asyncio.wait_for(
                self._run_protocol(process, tool=tool, arguments=arguments),
                timeout=timeout_seconds,
            )
        except TimeoutError as error:
            raise SkillExecutionError(
                "skill_timeout",
                f"Plugin tool exceeded {timeout_seconds:g}s timeout",
                retryable=True,
                details={"plugin_id": plugin.plugin_id, "tool": tool},
            ) from error
        except asyncio.CancelledError:
            await _notify_cancel(process)
            raise
        finally:
            await _terminate(process)

    async def _run_protocol(
        self,
        process: asyncio.subprocess.Process,
        *,
        tool: str,
        arguments: JsonObject,
    ) -> JsonObject:
        await _send(
            process,
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": MCP_PROTOCOL_VERSION,
                    "capabilities": {},
                    "clientInfo": {"name": "chatwaifu-runtime", "version": "0.1.0"},
                },
            },
        )
        initialized = await _response(process, 1)
        negotiated = initialized.get("protocolVersion")
        if negotiated != MCP_PROTOCOL_VERSION:
            raise SkillExecutionError(
                "mcp_version_mismatch", f"Plugin negotiated unsupported MCP version: {negotiated}"
            )
        await _send(process, {"jsonrpc": "2.0", "method": "notifications/initialized"})
        await _send(
            process,
            {
                "jsonrpc": "2.0",
                "id": 2,
                "method": "tools/call",
                "params": {"name": tool, "arguments": arguments},
            },
        )
        result = await _response(process, 2)
        if result.get("isError") is True:
            raise SkillExecutionError("plugin_tool_failed", _content_text(result))
        structured = result.get("structuredContent")
        if not isinstance(structured, dict):
            raise SkillExecutionError(
                "invalid_plugin_output", "MCP tool response requires structuredContent"
            )
        return cast(JsonObject, structured)


def _resolve_command(plugin: PluginManifest, root: Path) -> list[str]:
    raw = plugin.transport.command
    if not raw or raw[0] != "python":
        raise SkillExecutionError(
            "unsupported_plugin_command", "The local demo only permits Python stdio plugins"
        )
    if len(raw) < 2:
        raise SkillExecutionError("invalid_plugin_command", "Python plugin entrypoint is missing")
    entrypoint = (root / raw[1]).resolve()
    if not entrypoint.is_relative_to(root.resolve()) or not entrypoint.is_file():
        raise SkillExecutionError("unsafe_plugin_command", "Plugin entrypoint escapes install root")
    return [sys.executable, "-I", "-B", str(entrypoint), *raw[2:]]


def _clean_environment(plugin_id: str) -> dict[str, str]:
    environment = {
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "LANG": os.environ.get("LANG", "C.UTF-8"),
        "LC_ALL": os.environ.get("LC_ALL", "C.UTF-8"),
        "PYTHONUNBUFFERED": "1",
        "CHATWAIFU_PLUGIN_ID": plugin_id,
    }
    return environment


async def _send(process: asyncio.subprocess.Process, message: dict[str, object]) -> None:
    if process.stdin is None:
        raise SkillExecutionError("plugin_transport_closed", "Plugin stdin is unavailable")
    encoded = json.dumps(message, ensure_ascii=False, separators=(",", ":")).encode() + b"\n"
    if len(encoded) > MAX_RPC_LINE_BYTES:
        raise SkillExecutionError("plugin_request_too_large", "Plugin request exceeds 1 MiB")
    process.stdin.write(encoded)
    await process.stdin.drain()


async def _response(process: asyncio.subprocess.Process, request_id: int) -> JsonObject:
    if process.stdout is None:
        raise SkillExecutionError("plugin_transport_closed", "Plugin stdout is unavailable")
    for _ in range(64):
        line = await process.stdout.readline()
        if not line:
            stderr = await _stderr_text(process)
            raise SkillExecutionError(
                "plugin_exited", f"Plugin exited before responding{': ' + stderr if stderr else ''}"
            )
        try:
            loaded: object = json.loads(line)
        except json.JSONDecodeError as error:
            raise SkillExecutionError(
                "invalid_mcp_message", "Plugin emitted invalid JSON-RPC"
            ) from error
        if not isinstance(loaded, dict):
            raise SkillExecutionError("invalid_mcp_message", "Plugin emitted invalid JSON-RPC")
        message = cast(dict[str, object], loaded)
        if message.get("id") != request_id:
            continue
        rpc_error = message.get("error")
        if isinstance(rpc_error, dict):
            typed_error = cast(dict[str, object], rpc_error)
            raise SkillExecutionError(
                "plugin_rpc_error",
                str(typed_error.get("message", "Plugin JSON-RPC request failed")),
                details={"rpc_code": typed_error.get("code", -32603)},
            )
        result = message.get("result")
        if not isinstance(result, dict):
            raise SkillExecutionError(
                "invalid_mcp_message", "Plugin response result must be an object"
            )
        return cast(JsonObject, result)
    raise SkillExecutionError("mcp_message_limit", "Plugin emitted too many unrelated messages")


async def _notify_cancel(process: asyncio.subprocess.Process) -> None:
    try:
        await _send(
            process,
            {
                "jsonrpc": "2.0",
                "method": "notifications/cancelled",
                "params": {"requestId": 2, "reason": "runtime_cancelled"},
            },
        )
    except (BrokenPipeError, ConnectionError, SkillExecutionError):
        return


async def _terminate(process: asyncio.subprocess.Process) -> None:
    if process.returncode is not None:
        return
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except ProcessLookupError:
        return
    try:
        await asyncio.wait_for(process.wait(), timeout=1.0)
    except TimeoutError:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            return
        await process.wait()


async def _stderr_text(process: asyncio.subprocess.Process) -> str:
    if process.stderr is None:
        return ""
    try:
        data = await asyncio.wait_for(process.stderr.read(4096), timeout=0.1)
    except TimeoutError:
        return ""
    return data.decode(errors="replace").strip()


def _content_text(result: JsonObject) -> str:
    content = result.get("content")
    if isinstance(content, list):
        texts = [item.get("text") for item in content if isinstance(item, dict)]
        rendered = " ".join(value for value in texts if isinstance(value, str))
        if rendered:
            return rendered
    return "Plugin tool reported an error"
