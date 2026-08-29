"""Official MCP client transports with SSRF and soft-isolation boundaries."""

from __future__ import annotations

import asyncio
import ipaddress
import json
import os
import shutil
import socket
import ssl
import sys
import time
from collections.abc import AsyncGenerator, AsyncIterator, Iterable
from contextlib import asynccontextmanager
from dataclasses import dataclass
from functools import partial
from pathlib import Path
from typing import Any, Protocol, cast
from urllib.parse import urlsplit

import httpcore2
import httpx2
from chatwaifu_protocol.skills import McpConnectionConfiguration, PluginManifest
from mcp import ClientSession, StdioServerParameters
from mcp.client.sse import sse_client
from mcp.client.stdio import stdio_client
from mcp.client.streamable_http import streamable_http_client
from mcp.types import InitializeResult

from chatwaifu_runtime.runtime_skills.errors import SkillExecutionError

MAX_MCP_JSON_PAYLOAD_BYTES = 2 * 1024 * 1024
MAX_MCP_WIRE_RESPONSE_BYTES = MAX_MCP_JSON_PAYLOAD_BYTES


@dataclass(frozen=True, slots=True)
class PreparedStdioCommand:
    command: str
    args: tuple[str, ...]
    cwd: Path
    env: dict[str, str]
    sandbox_backend: str | None = None
    sandbox_limits_enforced: tuple[str, ...] = ()
    read_only_roots: tuple[Path, ...] = ()


@dataclass(frozen=True, slots=True)
class ValidatedMcpEndpoint:
    """One policy-checked origin and the immutable addresses allowed for its session."""

    hostname: str
    port: int
    addresses: tuple[ipaddress.IPv4Address | ipaddress.IPv6Address, ...]


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
        if network_policy == "loopback":
            raise SkillExecutionError(
                "sandbox_network_policy_unavailable",
                "Soft isolation cannot enforce a loopback-only network policy",
            )
        if sandbox_mode == "required":
            raise SkillExecutionError(
                "sandbox_unavailable",
                "This MCP connection requires an OS sandbox, but no sandbox backend is active",
            )
        if network_policy != "allow":
            raise SkillExecutionError(
                "sandbox_network_policy_unavailable",
                "Soft isolation cannot enforce the requested restricted network policy",
            )
        return command


class McpClientTransport:
    def __init__(self, sandbox_launcher: SandboxLauncher | None = None) -> None:
        self._sandbox = sandbox_launcher or NoopSandboxLauncher()

    def connection_sandbox_backend(
        self,
        config: McpConnectionConfiguration,
        *,
        working_root: Path,
    ) -> str | None:
        """Resolve the backend that a stdio connection would actually execute under.

        Network transports do not launch a local child process and therefore have no
        local sandbox backend. Preparing the stdio command applies the exact same
        fail-closed policy used by :meth:`connection_session`.
        """

        if config.transport != "stdio":
            return None
        return _connection_command(config, working_root, self._sandbox).sandbox_backend

    def connection_sandbox_status(
        self, config: McpConnectionConfiguration, *, working_root: Path
    ) -> tuple[str | None, tuple[str, ...]]:
        if config.transport != "stdio":
            return None, ()
        prepared = _connection_command(config, working_root, self._sandbox)
        return prepared.sandbox_backend, prepared.sandbox_limits_enforced

    def plugin_sandbox_backend(
        self, plugin: PluginManifest, plugin_root: Path, data_root: Path
    ) -> str | None:
        """Resolve the backend that will actually wrap an installed plugin."""

        return _plugin_command(plugin, plugin_root, data_root, self._sandbox).sandbox_backend

    def plugin_sandbox_status(
        self, plugin: PluginManifest, plugin_root: Path, data_root: Path
    ) -> tuple[str | None, tuple[str, ...]]:
        prepared = _plugin_command(plugin, plugin_root, data_root, self._sandbox)
        return prepared.sandbox_backend, prepared.sandbox_limits_enforced

    @asynccontextmanager
    async def plugin_session(
        self,
        plugin: PluginManifest,
        plugin_root: Path,
        data_root: Path,
        *,
        timeout_seconds: float,
    ) -> AsyncGenerator[tuple[ClientSession, InitializeResult]]:
        command = _plugin_command(plugin, plugin_root, data_root, self._sandbox)
        async with self._stdio_session(command, timeout_seconds) as initialized:
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
            async with self._stdio_session(command, config.timeout_seconds) as initialized:
                yield initialized
            return

        assert config.url is not None
        endpoint = await validate_mcp_url(
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
                httpx_client_factory=partial(_secure_http_client, endpoint=endpoint),
            ) as streams:
                async with self._client_session(streams, config.timeout_seconds) as initialized:
                    yield initialized
            return

        async with httpx2.AsyncClient(
            headers=headers,
            timeout=timeout,
            follow_redirects=False,
            trust_env=False,
            transport=PinnedAsyncHTTPTransport(endpoint),
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
        self, command: PreparedStdioCommand, timeout_seconds: float
    ) -> AsyncGenerator[tuple[ClientSession, InitializeResult]]:
        parameters = StdioServerParameters(
            command=command.command,
            args=list(command.args),
            cwd=command.cwd,
            env=command.env,
        )
        async with stdio_client(parameters) as streams:
            async with self._client_session(streams, timeout_seconds) as initialized:
                yield initialized

    @asynccontextmanager
    async def _client_session(
        self,
        streams: tuple[Any, ...],
        timeout_seconds: float,
    ) -> AsyncGenerator[tuple[ClientSession, InitializeResult]]:
        if len(streams) < 2:
            raise SkillExecutionError("mcp_transport_error", "MCP transport returned no streams")
        read_stream, write_stream = streams[:2]
        session = ClientSession(
            read_stream,
            write_stream,
            read_timeout_seconds=timeout_seconds,
        )
        async with session:
            async with asyncio.timeout(timeout_seconds):
                initialized = await session.initialize()
            yield session, initialized


def enforce_mcp_json_payload_limit(payload: object, *, boundary: str) -> None:
    """Reject an MCP response whose normalized JSON exceeds the shared limit."""

    try:
        encoded = json.dumps(
            payload,
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
        ).encode("utf-8")
    except (TypeError, ValueError) as error:
        raise SkillExecutionError(
            "invalid_mcp_result",
            f"MCP {boundary} response is not valid JSON",
        ) from error
    if len(encoded) > MAX_MCP_JSON_PAYLOAD_BYTES:
        raise SkillExecutionError(
            "mcp_response_limit",
            f"MCP {boundary} response exceeded the {MAX_MCP_JSON_PAYLOAD_BYTES}-byte limit",
            details={"boundary": boundary, "limit_bytes": MAX_MCP_JSON_PAYLOAD_BYTES},
        )


async def validate_mcp_url(
    url: str,
    *,
    allow_remote: bool,
    timeout_seconds: float = 5,
) -> ValidatedMcpEndpoint:
    """Validate an MCP origin and return the exact addresses its transport may use.

    Returning the resolved addresses is part of the security contract. Resolving once
    for policy and then letting the HTTP stack resolve the hostname again would leave
    a DNS-rebinding time-of-check/time-of-use window.
    """

    parsed = urlsplit(url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise SkillExecutionError("invalid_mcp_url", "MCP URL must use http or https")
    if parsed.username or parsed.password or parsed.fragment:
        raise SkillExecutionError(
            "invalid_mcp_url", "MCP URL must not contain credentials or a fragment"
        )
    hostname = _canonical_hostname(parsed.hostname)
    try:
        port = parsed.port or (443 if parsed.scheme == "https" else 80)
    except ValueError as error:
        raise SkillExecutionError("invalid_mcp_url", "MCP URL contains an invalid port") from error

    addresses: list[ipaddress.IPv4Address | ipaddress.IPv6Address] = []
    try:
        addresses.append(ipaddress.ip_address(hostname))
    except ValueError:
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
            address = ipaddress.ip_address(str(item[4][0]))
            if address not in addresses:
                addresses.append(address)
    if not addresses:
        raise SkillExecutionError("mcp_dns_failed", "MCP hostname resolved to no addresses")
    if any(_forbidden_address(address) for address in addresses):
        raise SkillExecutionError(
            "remote_mcp_forbidden",
            "MCP URL resolved to a private, metadata/link-local, reserved, or otherwise "
            "non-global address",
        )
    if not allow_remote and any(not address.is_loopback for address in addresses):
        raise SkillExecutionError(
            "remote_mcp_forbidden",
            "Remote MCP hosts require allow_remote=true; every address must be loopback by default",
        )
    return ValidatedMcpEndpoint(hostname=hostname, port=port, addresses=tuple(addresses))


def _forbidden_address(address: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    if address.is_loopback:
        return False
    # Only globally routable or explicit loopback endpoints are supported. This also
    # excludes RFC1918/ULA, link-local metadata services, CGNAT and benchmark ranges.
    return not address.is_global


def _canonical_hostname(hostname: str) -> str:
    candidate = hostname.rstrip(".")
    try:
        return ipaddress.ip_address(candidate).compressed
    except ValueError:
        try:
            return candidate.encode("idna").decode("ascii").lower()
        except UnicodeError as error:
            raise SkillExecutionError(
                "invalid_mcp_url", "MCP URL contains an invalid hostname"
            ) from error


SocketOption = (
    tuple[int, int, int] | tuple[int, int, bytes | bytearray] | tuple[int, int, None, int]
)


class PinnedNetworkBackend(httpcore2.AsyncNetworkBackend):
    """Connect an already-validated origin only to its policy-checked address set."""

    def __init__(
        self,
        endpoint: ValidatedMcpEndpoint,
        delegate: httpcore2.AsyncNetworkBackend | None = None,
    ) -> None:
        self._endpoint = endpoint
        self._delegate = delegate or cast(
            httpcore2.AsyncNetworkBackend,
            httpcore2.AnyIOBackend(),
        )

    async def connect_tcp(
        self,
        host: str,
        port: int,
        timeout: float | None = None,  # noqa: ASYNC109 - httpcore backend contract
        local_address: str | None = None,
        socket_options: Iterable[SocketOption] | None = None,
    ) -> httpcore2.AsyncNetworkStream:
        if _canonical_hostname(host) != self._endpoint.hostname or port != self._endpoint.port:
            raise httpcore2.ConnectError("Pinned MCP transport refused an unexpected origin")

        deadline = time.monotonic() + timeout if timeout is not None else None
        last_error: httpcore2.ConnectError | httpcore2.ConnectTimeout | None = None
        for address in self._endpoint.addresses:
            remaining = None if deadline is None else deadline - time.monotonic()
            if remaining is not None and remaining <= 0:
                raise httpcore2.ConnectTimeout("Timed out connecting to the pinned MCP endpoint")
            try:
                stream = await self._delegate.connect_tcp(
                    address.compressed,
                    port,
                    timeout=remaining,
                    local_address=local_address,
                    socket_options=socket_options,
                )
            except (httpcore2.ConnectError, httpcore2.ConnectTimeout) as error:
                last_error = error
                continue

            if not _peer_matches(stream.get_extra_info("server_addr"), address, port):
                await stream.aclose()
                raise httpcore2.ConnectError(
                    "MCP connection peer did not match the validated endpoint"
                )
            return stream

        if last_error is not None:
            raise last_error
        raise httpcore2.ConnectError("MCP endpoint has no pinned addresses")

    async def connect_unix_socket(
        self,
        path: str,
        timeout: float | None = None,  # noqa: ASYNC109 - httpcore backend contract
        socket_options: Iterable[SocketOption] | None = None,
    ) -> httpcore2.AsyncNetworkStream:
        del path, timeout, socket_options
        raise httpcore2.ConnectError("Pinned MCP HTTP transport does not support Unix sockets")

    async def sleep(self, seconds: float) -> None:
        await self._delegate.sleep(seconds)


def _peer_matches(
    peer: object,
    expected_address: ipaddress.IPv4Address | ipaddress.IPv6Address,
    expected_port: int,
) -> bool:
    if not isinstance(peer, tuple):
        return False
    peer_values = cast(tuple[object, ...], peer)
    if len(peer_values) < 2:
        return False
    raw_port = peer_values[1]
    if not isinstance(raw_port, (int, str)):
        return False
    try:
        peer_address = ipaddress.ip_address(str(peer_values[0]).split("%", 1)[0])
        peer_port = int(raw_port)
    except (TypeError, ValueError):
        return False
    if isinstance(peer_address, ipaddress.IPv6Address) and peer_address.ipv4_mapped is not None:
        peer_address = peer_address.ipv4_mapped
    return peer_address == expected_address and peer_port == expected_port


class PinnedAsyncHTTPTransport(httpx2.AsyncHTTPTransport):
    """HTTPX transport whose TCP dialer consumes a validated address pin."""

    def __init__(
        self,
        endpoint: ValidatedMcpEndpoint,
        *,
        verify: ssl.SSLContext | str | bool = True,
        network_backend: httpcore2.AsyncNetworkBackend | None = None,
    ) -> None:
        # HTTPX keeps the original URL origin in the pool. Therefore it still emits
        # the original Host header and httpcore uses that hostname for TLS SNI and
        # certificate verification; only the TCP destination is replaced by the pin.
        ssl_context = httpx2.create_ssl_context(verify=verify, trust_env=False)
        self._pool = httpcore2.AsyncConnectionPool(
            ssl_context=ssl_context,
            max_connections=10,
            max_keepalive_connections=10,
            keepalive_expiry=5,
            http1=True,
            http2=False,
            retries=0,
            network_backend=PinnedNetworkBackend(endpoint, network_backend),
        )

    async def handle_async_request(self, request: httpx2.Request) -> httpx2.Response:
        response = await super().handle_async_request(request)
        content_length = response.headers.get("content-length")
        if content_length is not None:
            try:
                declared_length = int(content_length)
            except ValueError:
                await response.aclose()
                raise SkillExecutionError(
                    "invalid_mcp_response",
                    "MCP response declared an invalid Content-Length",
                ) from None
            if declared_length > MAX_MCP_WIRE_RESPONSE_BYTES:
                await response.aclose()
                raise _wire_response_limit_error()
        # ``AsyncHTTPTransport`` always returns an async stream, but HTTPX's
        # public response annotation is deliberately wider because the same
        # response type is shared with the synchronous client.
        response.stream = _LimitedMcpResponseStream(cast(httpx2.AsyncByteStream, response.stream))
        return response


class _LimitedMcpResponseStream(httpx2.AsyncByteStream):
    """Bound network bytes before the MCP SDK buffers or deserializes them."""

    def __init__(self, inner: httpx2.AsyncByteStream) -> None:
        self._inner = inner
        self._received = 0

    async def __aiter__(self) -> AsyncIterator[bytes]:
        async for chunk in self._inner:
            self._received += len(chunk)
            if self._received > MAX_MCP_WIRE_RESPONSE_BYTES:
                await self._inner.aclose()
                raise _wire_response_limit_error()
            yield chunk

    async def aclose(self) -> None:
        await self._inner.aclose()


def _wire_response_limit_error() -> SkillExecutionError:
    return SkillExecutionError(
        "mcp_response_limit",
        f"MCP network response exceeded the {MAX_MCP_WIRE_RESPONSE_BYTES}-byte wire limit",
        details={"boundary": "network response", "limit_bytes": MAX_MCP_WIRE_RESPONSE_BYTES},
    )


def _secure_http_client(
    headers: dict[str, str] | None = None,
    timeout: httpx2.Timeout | None = None,
    auth: httpx2.Auth | None = None,
    *,
    endpoint: ValidatedMcpEndpoint,
) -> httpx2.AsyncClient:
    return httpx2.AsyncClient(
        headers=headers,
        timeout=timeout or httpx2.Timeout(5),
        auth=auth,
        follow_redirects=False,
        trust_env=False,
        transport=PinnedAsyncHTTPTransport(endpoint),
    )


def _plugin_command(
    plugin: PluginManifest,
    package_root: Path,
    data_root: Path,
    sandbox: SandboxLauncher,
) -> PreparedStdioCommand:
    raw = plugin.transport.command
    resolved_package = package_root.resolve()
    resolved_data = data_root.resolve()
    resolved_data.mkdir(parents=True, exist_ok=True)
    command, arguments = _resolve_command(raw, resolved_package, restrict_to_root=True)
    prepared = PreparedStdioCommand(
        command=command,
        args=tuple(arguments),
        cwd=resolved_data,
        env={
            **_clean_environment(plugin.plugin_id),
            "CHATWAIFU_PLUGIN_PACKAGE_DIR": str(resolved_package),
            "CHATWAIFU_PLUGIN_DATA_DIR": str(resolved_data),
        },
        read_only_roots=(resolved_package,),
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
