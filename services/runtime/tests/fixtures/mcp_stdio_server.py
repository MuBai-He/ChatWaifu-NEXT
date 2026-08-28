"""Official MCP stdio fixture for OS sandbox handshake tests."""

from __future__ import annotations

from mcp.server.mcpserver import MCPServer


def _ping(text: str) -> dict[str, str]:
    return {"reply": text}


server = MCPServer("chatwaifu-seatbelt-fixture", version="1.0.0")
server.tool(name="ping")(_ping)


if __name__ == "__main__":
    server.run("stdio")
