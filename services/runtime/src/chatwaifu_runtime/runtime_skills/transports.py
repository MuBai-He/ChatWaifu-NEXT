"""Official MCP client transports with SSRF and soft-isolation boundaries."""

from __future__ import annotations

import asyncio
import ipaddress
import os
import shutil
import socket
import sys
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol
from urllib.parse import urlsplit

import httpx2
from chatwaifu_protocol.skills import McpConnectionConfiguration, PluginManifest
from mcp import ClientSession, StdioServerParameters
from mcp.client._transport import TransportStreams
from mcp.client.sse import sse_client
from mcp.client.stdio import stdio_client
from mcp.client.streamable_http import streamable_http_client
from mcp.types import InitializeResult

from chatwaifu_runtime.runtime_skills.errors import SkillExecutionError


@dataclass(frozen=True, slots=True)
class PreparedStdioCommand:
    command: str
    args: tuple[str, ...]
    cwd: Path
    env: dict[str, str]
    sandbox_backend: str | None = None


class SandboxLauncher(Protocol):
    """Injection point for a real platform sandbox; no enforcement is implied."""

    def prepare(
        self,
        command: PreparedStdioCommand,
        *,
        trust_level: str,
        sandbox_mode: str,
        network_policy: str,
    ) -> PreparedStdioCommand: ...


class NoopSandboxLauncher:
    def prepare(
        self,
        command: PreparedStdioCommand,
        *,
        trust_level: str,
        sandbox_mode: str,
        network_policy: str,
    ) -> PreparedStdioCommand:
        del trust_level
        if sandbox_mode == "required":
            raise SkillExecutionError(
                "sandbox_unavailable",
                "This MCP connection requires an OS sandbox, but no sandbox backend is active",
            )
        if network_policy == "loopback" and sandbox_mode != "disabled":
            raise SkillExecutionError(
                "sandbox_network_policy_unavailable",
                "The active sandbox cannot enforce a loopback-only network policy",
            )
        return command


class McpClientTransport:
    def __init__(self, sandbox_launcher: SandboxLauncher | None = None) -> None:
        self._sandbox = sandbox_launcher or NoopSandboxLauncher()

    @asynccontextmanager
    async def plugin_session(
        self, plugin: PluginManifest, plugin_root: Path
    ) -> AsyncGenerator[tuple[ClientSession, InitializeResult]]:
        command = _plugin_command(plugin, plugin_root, self._sandbox)
        async with self._stdio_session(command) as initialized:
            yield initialized

    @asynccontextmanager
    async def connection_session(
        self,
        config: McpConnectionConfiguration,
        *,
        bearer_token: str | None,
        working_root: Path,
    ) -> AsyncGenerator[tuple[ClientSession, InitializeResult]]:
        if not config.enabled:
            raise SkillExecutionError("mcp_connection_disabled", "MCP connection is disabled")
        if config.transport == "stdio":
            command = _connection_command(config, working_root, self._sandbox)
            async with self._stdio_session(command) as initialized:
                yield initialized
            return

        assert config.url is not None
        await validate_mcp_url(
            config.url,
            allow_remote=config.allow_remote,
            timeout_seconds=config.timeout_seconds,
        )
        headers = {"Authorization": f"Bearer {bearer_token}"} if bearer_token else None
        timeout = httpx2.Timeout(config.timeout_seconds, read=config.timeout_seconds)
        if config.transport == "sse":
            async with sse_client(
                config.url,
                headers=headers,
                timeout=config.timeout_seconds,
                sse_read_timeout=config.timeout_seconds,
                httpx_client_factory=_secure_http_client,
            ) as streams:
                async with self._client_session(streams, config.timeout_seconds) as initialized:
                    yield initialized
            return

        async with httpx2.AsyncClient(
            headers=headers,
            timeout=timeout,
            follow_redirects=False,
            trust_env=False,
        ) as http_client:
            async with streamable_http_client(
                config.url,
                http_client=http_client,
                terminate_on_close=True,
            ) as streams:
                async with self._client_session(streams, config.timeout_seconds) as initialized:
                    yield initialized

    @asynccontextmanager
    async def _stdio_session(
        self, command: PreparedStdioCommand
    ) -> AsyncGenerator[tuple[ClientSession, InitializeResult]]:
        parameters = StdioServerParameters(
            command=command.command,
            args=list(command.args),
            cwd=command.cwd,
            env=command.env,
        )
        async with stdio_client(parameters) as streams:
            async with self._client_session(streams, 30) as initialized:
                yield initialized

    @asynccontextmanager
    async def _client_session(
        self,
        streams: TransportStreams,
        timeout_seconds: float,
    ) -> AsyncGenerator[tuple[ClientSession, InitializeResult]]:
        read_stream, write_stream = streams
        session = ClientSession(
            read_stream,
            write_stream,
            read_timeout_seconds=timeout_seconds,
        )
        async with session:
            async with asyncio.timeout(timeout_seconds):
                initialized = await session.initialize()
            yield session, initialized


async def validate_mcp_url(
    url: str,
    *,
    allow_remote: bool,
    timeout_seconds: float = 5,
) -> None:
    parsed = urlsplit(url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise SkillExecutionError("invalid_mcp_url", "MCP URL must use http or https")
    if parsed.username or parsed.password or parsed.fragment:
        raise SkillExecutionError(
            "invalid_mcp_url", "MCP URL must not contain credentials or a fragment"
        )
    hostname = parsed.hostname.rstrip(".").lower()
    addresses: set[ipaddress.IPv4Address | ipaddress.IPv6Address] = set()
    try:
        addresses.add(ipaddress.ip_address(hostname))
    except ValueError:
        port = parsed.port or (443 if parsed.scheme == "https" else 80)
        try:
            async with asyncio.timeout(timeout_seconds):
                resolved = await asyncio.get_running_loop().getaddrinfo(
                    hostname,
                    port,
                    family=socket.AF_UNSPEC,
                    type=socket.SOCK_STREAM,
                )
        except (OSError, TimeoutError) as error:
            raise SkillExecutionError(
                "mcp_dns_failed", "MCP hostname could not be resolved", retryable=True
            ) from error
        for item in resolved:
            addresses.add(ipaddress.ip_address(str(item[4][0])))
    if not addresses:
        raise SkillExecutionError("mcp_dns_failed", "MCP hostname resolved to no addresses")
    if any(_forbidden_address(address) for address in addresses):
        raise SkillExecutionError(
            "remote_mcp_forbidden",
            "MCP URL resolved to an unspecified, multicast, reserved, or link-local address",
        )
    if not allow_remote and any(not address.is_loopback for address in addresses):
        raise SkillExecutionError(
            "remote_mcp_forbidden",
            "Remote MCP hosts require allow_remote=true; every address must be loopback by default",
        )


def _forbidden_address(address: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    if address.is_loopback:
        return False
    return (
        address.is_unspecified
        or address.is_multicast
        or address.is_reserved
        or address.is_link_local
    )


def _secure_http_client(
    headers: dict[str, str] | None = None,
    timeout: httpx2.Timeout | None = None,
    auth: httpx2.Auth | None = None,
) -> httpx2.AsyncClient:
    return httpx2.AsyncClient(
        headers=headers,
        timeout=timeout or httpx2.Timeout(5),
        auth=auth,
        follow_redirects=False,
        trust_env=False,
    )


def _plugin_command(
    plugin: PluginManifest,
    root: Path,
    sandbox: SandboxLauncher,
) -> PreparedStdioCommand:
    raw = plugin.transport.command
    resolved_root = root.resolve()
    command, arguments = _resolve_command(raw, resolved_root, restrict_to_root=True)
    prepared = PreparedStdioCommand(
        command=command,
        args=tuple(arguments),
        cwd=resolved_root,
        env=_clean_environment(plugin.plugin_id),
    )
    return sandbox.prepare(
        prepared,
        trust_level=plugin.transport.trust_level,
        sandbox_mode=plugin.transport.sandbox_mode,
        network_policy=plugin.transport.network_policy,
    )


def _connection_command(
    config: McpConnectionConfiguration,
    root: Path,
    sandbox: SandboxLauncher,
) -> PreparedStdioCommand:
    root.mkdir(parents=True, exist_ok=True)
    command, arguments = _resolve_command(config.command, root.resolve(), restrict_to_root=False)
    prepared = PreparedStdioCommand(
        command=command,
        args=tuple(arguments),
        cwd=root.resolve(),
        env=_clean_environment(str(config.connection_id)),
    )
    return sandbox.prepare(
        prepared,
        trust_level=config.trust_level,
        sandbox_mode=config.sandbox_mode,
        network_policy=config.network_policy,
    )


def _resolve_command(
    raw: list[str], root: Path, *, restrict_to_root: bool
) -> tuple[str, list[str]]:
    if not raw or not raw[0].strip():
        raise SkillExecutionError("invalid_mcp_command", "MCP command is required")
    executable = raw[0]
    arguments = list(raw[1:])

    if executable in {"python", "python3"}:
        executable = sys.executable
        if restrict_to_root and arguments and not arguments[0].startswith("-"):
            entrypoint = (root / arguments[0]).resolve()
            if not entrypoint.is_relative_to(root) or not entrypoint.is_file():
                raise SkillExecutionError(
                    "unsafe_plugin_command", "Plugin entrypoint escapes install root"
                )
            arguments[0] = str(entrypoint)
        arguments = ["-X", "utf8", "-I", "-B", "-u", *arguments]
        return executable, arguments

    candidate = Path(executable).expanduser()
    if candidate.is_absolute() or candidate.parent != Path("."):
        resolved = candidate if candidate.is_absolute() else (root / candidate).resolve()
        if restrict_to_root and not resolved.is_relative_to(root):
            raise SkillExecutionError(
                "unsafe_plugin_command", "Plugin executable escapes install root"
            )
        if not resolved.is_file():
            raise SkillExecutionError("mcp_command_not_found", "MCP executable was not found")
        return str(resolved), arguments

    discovered = shutil.which(executable, path=os.environ.get("PATH"))
    if discovered is None:
        raise SkillExecutionError(
            "mcp_command_not_found", f"MCP executable was not found: {executable}"
        )
    return discovered, arguments


def _clean_environment(subject_id: str) -> dict[str, str]:
    return {
        "PATH": os.environ.get("PATH", os.defpath),
        "LANG": os.environ.get("LANG", "C.UTF-8"),
        "LC_ALL": os.environ.get("LC_ALL", "C.UTF-8"),
        "CHATWAIFU_MCP_SUBJECT_ID": subject_id,
    }
