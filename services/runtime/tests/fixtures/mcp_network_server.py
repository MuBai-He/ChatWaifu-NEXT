"""Real loopback MCP server used by network transport integration tests."""

from __future__ import annotations

import asyncio
import socket
import sys

import uvicorn
from mcp.server.mcpserver import MCPServer


def _echo(text: str) -> dict[str, str]:
    """Echo a value through the real MCP transport."""

    return {"echo": text}


def _status() -> str:
    return "network fixture ready"


def _character(name: str) -> str:
    return f"character:{name}"


def _greet(name: str) -> str:
    """Build a deterministic greeting prompt."""

    return f"Hello {name} from the network fixture"


def build_server() -> MCPServer:
    server = MCPServer("chatwaifu-network-fixture", version="1.0.0")
    server.tool(name="echo")(_echo)
    server.resource("fixture://status", mime_type="text/plain")(_status)
    server.resource("fixture://characters/{name}", mime_type="text/plain")(_character)
    server.prompt(name="greet")(_greet)
    return server


class ReadinessServer(uvicorn.Server):
    """Announce readiness only after Uvicorn has installed its listener."""

    def __init__(self, config: uvicorn.Config, port: int) -> None:
        super().__init__(config)
        self._fixture_port = port

    async def startup(self, sockets: list[socket.socket] | None = None) -> None:
        await super().startup(sockets=sockets)
        print(f"MCP_FIXTURE_READY {self._fixture_port}", flush=True)


async def serve(transport: str) -> None:
    server = build_server()
    if transport == "streamable_http":
        app = server.streamable_http_app(
            streamable_http_path="/mcp",
            json_response=True,
            stateless_http=False,
            host="127.0.0.1",
        )
    elif transport == "sse":
        app = server.sse_app(
            sse_path="/sse",
            message_path="/messages/",
            host="127.0.0.1",
        )
    else:
        raise ValueError(f"unsupported transport: {transport}")

    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    listener.bind(("127.0.0.1", 0))
    listener.listen(128)
    listener.setblocking(False)
    port = int(listener.getsockname()[1])
    config = uvicorn.Config(
        app,
        host="127.0.0.1",
        port=port,
        log_level="warning",
        lifespan="on",
    )
    try:
        await ReadinessServer(config, port).serve(sockets=[listener])
    finally:
        listener.close()


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit("usage: mcp_network_server.py <streamable_http|sse>")
    asyncio.run(serve(sys.argv[1]))
