"""Real-socket integration tests for MCP network client transports."""

from __future__ import annotations

import asyncio
import ipaddress
import os
import queue
import socket
import ssl
import subprocess
import sys
import threading
import time
from collections.abc import Generator
from contextlib import contextmanager
from pathlib import Path
from typing import Any, cast
from uuid import uuid4

import httpcore2
import httpx2
import mcp.types as mcp_types
import pytest
from chatwaifu_protocol.skills import McpConnectionConfiguration
from chatwaifu_runtime.runtime_skills.errors import SkillExecutionError
from chatwaifu_runtime.runtime_skills.transports import (
    MAX_MCP_WIRE_RESPONSE_BYTES,
    McpClientTransport,
    PinnedAsyncHTTPTransport,
    PinnedNetworkBackend,
    ValidatedMcpEndpoint,
    validate_mcp_url,
)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("transport", "endpoint"),
    [("streamable_http", "/mcp"), ("sse", "/sse")],
)
async def test_real_loopback_mcp_network_transport_round_trip(
    tmp_path: Path,
    transport: str,
    endpoint: str,
) -> None:
    with _network_mcp_server(transport) as port:
        config = McpConnectionConfiguration(
            connection_id=uuid4(),
            name=f"Loopback {transport}",
            transport=cast(Any, transport),
            url=f"http://127.0.0.1:{port}{endpoint}",
            allow_remote=False,
            timeout_seconds=5,
            trust_level="untrusted",
            sandbox_mode="disabled",
            network_policy="loopback",
        )

        async with McpClientTransport().connection_session(
            config,
            bearer_token=None,
            working_root=tmp_path,
        ) as (session, initialized):
            assert initialized.server_info.name == "chatwaifu-network-fixture"
            assert initialized.server_info.version == "1.0.0"

            tools = await session.list_tools()
            assert [tool.name for tool in tools.tools] == ["echo"]

            resources = await session.list_resources()
            assert [str(resource.uri) for resource in resources.resources] == ["fixture://status"]
            resource = await session.read_resource("fixture://status")
            resource_content = resource.contents[0]
            assert isinstance(resource_content, mcp_types.TextResourceContents)
            assert resource_content.text == "network fixture ready"

            templates = await session.list_resource_templates()
            assert [template.uri_template for template in templates.resource_templates] == [
                "fixture://characters/{name}"
            ]
            templated = await session.read_resource("fixture://characters/nene")
            templated_content = templated.contents[0]
            assert isinstance(templated_content, mcp_types.TextResourceContents)
            assert templated_content.text == "character:nene"

            prompts = await session.list_prompts()
            assert [prompt.name for prompt in prompts.prompts] == ["greet"]
            prompt = await session.get_prompt("greet", arguments={"name": "Nene"})
            prompt_content = prompt.messages[0].content
            assert isinstance(prompt_content, mcp_types.TextContent)
            assert prompt_content.text == "Hello Nene from the network fixture"

            result = await session.call_tool("echo", {"text": "real socket"})
            assert result.is_error is False
            assert result.structured_content == {"echo": "real socket"}


@pytest.mark.asyncio
@pytest.mark.parametrize("rebound_address", ["10.0.0.9", "169.254.169.254"])
async def test_validated_public_address_is_pinned_against_dns_rebinding(
    monkeypatch: pytest.MonkeyPatch,
    rebound_address: str,
) -> None:
    """A second DNS answer must never become the TCP destination."""

    resolver_calls = 0
    loop = asyncio.get_running_loop()

    async def rebinding_getaddrinfo(
        host: str,
        port: int,
        *,
        family: socket.AddressFamily,
        type: socket.SocketKind,
    ) -> list[tuple[socket.AddressFamily, socket.SocketKind, int, str, tuple[str, int]]]:
        nonlocal resolver_calls
        del host, family, type
        resolver_calls += 1
        address = "93.184.216.34" if resolver_calls == 1 else rebound_address
        return [(socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", (address, port))]

    monkeypatch.setattr(loop, "getaddrinfo", rebinding_getaddrinfo)
    endpoint = await validate_mcp_url(
        "https://mcp.example.test/status",
        allow_remote=True,
    )
    delegate = _RecordingNetworkBackend()
    backend = PinnedNetworkBackend(endpoint, delegate)

    stream = await backend.connect_tcp("mcp.example.test", 443, timeout=1)

    assert stream is delegate.streams[0]
    assert resolver_calls == 1
    assert delegate.destinations == [("93.184.216.34", 443)]


@pytest.mark.asyncio
@pytest.mark.parametrize("blocked_address", ["10.0.0.9", "169.254.169.254"])
async def test_private_and_metadata_dns_answers_are_rejected(
    monkeypatch: pytest.MonkeyPatch,
    blocked_address: str,
) -> None:
    loop = asyncio.get_running_loop()

    async def private_getaddrinfo(
        host: str,
        port: int,
        *,
        family: socket.AddressFamily,
        type: socket.SocketKind,
    ) -> list[tuple[socket.AddressFamily, socket.SocketKind, int, str, tuple[str, int]]]:
        del host, family, type
        return [
            (
                socket.AF_INET,
                socket.SOCK_STREAM,
                socket.IPPROTO_TCP,
                "",
                (blocked_address, port),
            )
        ]

    monkeypatch.setattr(loop, "getaddrinfo", private_getaddrinfo)

    with pytest.raises(SkillExecutionError) as captured:
        await validate_mcp_url("https://mcp.example.test/status", allow_remote=True)

    assert captured.value.structured.code == "remote_mcp_forbidden"


@pytest.mark.asyncio
async def test_pinned_transport_preserves_host_sni_and_certificate_verification() -> None:
    endpoint = ValidatedMcpEndpoint(
        hostname="mcp.example.test",
        port=443,
        addresses=(ipaddress.ip_address("93.184.216.34"),),
    )
    delegate = _RecordingNetworkBackend(
        response=b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\nConnection: close\r\n\r\nok"
    )
    transport = PinnedAsyncHTTPTransport(endpoint, network_backend=delegate)

    async with httpx2.AsyncClient(transport=transport, trust_env=False) as client:
        response = await client.get("https://mcp.example.test/status")

    assert response.text == "ok"
    assert delegate.destinations == [("93.184.216.34", 443)]
    stream = delegate.streams[0]
    assert stream.tls_hostname == "mcp.example.test"
    assert stream.ssl_context is not None
    assert stream.ssl_context.check_hostname is True
    assert stream.ssl_context.verify_mode == ssl.CERT_REQUIRED
    assert b"Host: mcp.example.test\r\n" in b"".join(stream.writes)


@pytest.mark.asyncio
async def test_pinned_transport_rejects_an_unexpected_connected_peer() -> None:
    endpoint = ValidatedMcpEndpoint(
        hostname="mcp.example.test",
        port=443,
        addresses=(ipaddress.ip_address("93.184.216.34"),),
    )
    delegate = _RecordingNetworkBackend(peer_address="10.0.0.9")
    backend = PinnedNetworkBackend(endpoint, delegate)

    with pytest.raises(httpcore2.ConnectError, match="peer did not match"):
        await backend.connect_tcp("mcp.example.test", 443, timeout=1)

    assert delegate.streams[0].closed is True


@pytest.mark.asyncio
@pytest.mark.parametrize("declared_length", [True, False])
async def test_pinned_transport_bounds_network_response_before_sdk_deserialization(
    declared_length: bool,
) -> None:
    endpoint = ValidatedMcpEndpoint(
        hostname="mcp.example.test",
        port=80,
        addresses=(ipaddress.ip_address("93.184.216.34"),),
    )
    body = b"x" * (MAX_MCP_WIRE_RESPONSE_BYTES + 1)
    headers = (
        f"Content-Length: {len(body)}\r\n".encode()
        if declared_length
        else b"Transfer-Encoding: chunked\r\n"
    )
    encoded_body = (
        body if declared_length else f"{len(body):x}\r\n".encode() + body + b"\r\n0\r\n\r\n"
    )
    delegate = _RecordingNetworkBackend(
        response=b"HTTP/1.1 200 OK\r\n" + headers + b"Connection: close\r\n\r\n" + encoded_body
    )
    transport = PinnedAsyncHTTPTransport(endpoint, network_backend=delegate)

    async with httpx2.AsyncClient(transport=transport, trust_env=False) as client:
        with pytest.raises(SkillExecutionError) as raised:
            await client.get("http://mcp.example.test/status")

    assert raised.value.structured.code == "mcp_response_limit"


class _RecordingNetworkStream(httpcore2.AsyncNetworkStream):
    def __init__(self, peer: tuple[str, int], response: bytes) -> None:
        self.peer = peer
        self.response = response
        self.writes: list[bytes] = []
        self.closed = False
        self.tls_hostname: str | None = None
        self.ssl_context: ssl.SSLContext | None = None

    async def read(
        self,
        max_bytes: int,
        timeout: float | None = None,  # noqa: ASYNC109 - httpcore test double contract
    ) -> bytes:
        del max_bytes, timeout
        response, self.response = self.response, b""
        return response

    async def write(
        self,
        buffer: bytes,
        timeout: float | None = None,  # noqa: ASYNC109 - httpcore test double contract
    ) -> None:
        del timeout
        self.writes.append(buffer)

    async def aclose(self) -> None:
        self.closed = True

    async def start_tls(
        self,
        ssl_context: ssl.SSLContext,
        server_hostname: str | None = None,
        timeout: float | None = None,  # noqa: ASYNC109 - httpcore test double contract
    ) -> httpcore2.AsyncNetworkStream:
        del timeout
        self.ssl_context = ssl_context
        self.tls_hostname = server_hostname
        return self

    def get_extra_info(self, info: str) -> object:
        if info == "server_addr":
            return self.peer
        if info == "is_readable":
            return False
        return None


class _RecordingNetworkBackend(httpcore2.AsyncNetworkBackend):
    def __init__(
        self,
        *,
        peer_address: str | None = None,
        response: bytes = b"",
    ) -> None:
        self.peer_address = peer_address
        self.response = response
        self.destinations: list[tuple[str, int]] = []
        self.streams: list[_RecordingNetworkStream] = []

    async def connect_tcp(
        self,
        host: str,
        port: int,
        timeout: float | None = None,  # noqa: ASYNC109 - httpcore test double contract
        local_address: str | None = None,
        socket_options: Any = None,
    ) -> httpcore2.AsyncNetworkStream:
        del timeout, local_address, socket_options
        self.destinations.append((host, port))
        stream = _RecordingNetworkStream((self.peer_address or host, port), self.response)
        self.streams.append(stream)
        return stream

    async def connect_unix_socket(
        self,
        path: str,
        timeout: float | None = None,  # noqa: ASYNC109 - httpcore test double contract
        socket_options: Any = None,
    ) -> httpcore2.AsyncNetworkStream:
        del path, timeout, socket_options
        raise AssertionError("Unix sockets are not expected")

    async def sleep(self, seconds: float) -> None:
        del seconds


@contextmanager
def _network_mcp_server(transport: str) -> Generator[int]:
    fixture = Path(__file__).parent / "fixtures" / "mcp_network_server.py"
    output: queue.Queue[str | None] = queue.Queue()
    lines: list[str] = []
    environment = os.environ.copy()
    environment["PYTHONUNBUFFERED"] = "1"
    process = subprocess.Popen(
        [sys.executable, str(fixture), transport],
        cwd=fixture.parents[4],
        env=environment,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
    )
    assert process.stdout is not None

    def collect_output() -> None:
        assert process.stdout is not None
        for line in process.stdout:
            stripped = line.rstrip()
            lines.append(stripped)
            output.put(stripped)
        output.put(None)

    reader = threading.Thread(target=collect_output, name=f"mcp-{transport}-output", daemon=True)
    reader.start()
    port: int | None = None
    deadline = time.monotonic() + 10
    try:
        while port is None:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise AssertionError(
                    f"{transport} MCP fixture did not become ready:\n" + "\n".join(lines)
                )
            try:
                line = output.get(timeout=remaining)
            except queue.Empty as error:
                raise AssertionError(
                    f"{transport} MCP fixture readiness timed out:\n" + "\n".join(lines)
                ) from error
            if line is None:
                raise AssertionError(
                    f"{transport} MCP fixture exited before readiness "
                    f"(code={process.poll()}):\n" + "\n".join(lines)
                )
            if line.startswith("MCP_FIXTURE_READY "):
                port = int(line.rsplit(" ", 1)[1])
        yield port
    finally:
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
        process.stdout.close()
        reader.join(timeout=2)
        assert not reader.is_alive(), f"{transport} fixture output reader leaked"
        if port is not None:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
                probe.settimeout(0.25)
                assert probe.connect_ex(("127.0.0.1", port)) != 0, (
                    f"{transport} fixture still accepts connections after teardown"
                )
