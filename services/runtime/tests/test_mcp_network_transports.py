"""Real-socket integration tests for MCP network client transports."""

from __future__ import annotations

import os
import queue
import socket
import subprocess
import sys
import threading
import time
from collections.abc import Generator
from contextlib import contextmanager
from pathlib import Path
from typing import Any, cast
from uuid import uuid4

import mcp.types as mcp_types
import pytest
from chatwaifu_protocol.skills import McpConnectionConfiguration
from chatwaifu_runtime.runtime_skills.transports import McpClientTransport


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
