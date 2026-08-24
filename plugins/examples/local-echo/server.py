"""Minimal MCP 2025-11-25 stdio server used by Runtime integration tests."""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path
from typing import Any

PROTOCOL_VERSION = "2025-11-25"


def main() -> None:
    for raw_line in sys.stdin:
        try:
            request = json.loads(raw_line)
            response = handle(request)
        except Exception as error:
            response = {
                "jsonrpc": "2.0",
                "id": None,
                "error": {"code": -32603, "message": type(error).__name__},
            }
        if response is not None:
            sys.stdout.write(json.dumps(response, ensure_ascii=False, separators=(",", ":")) + "\n")
            sys.stdout.flush()


def handle(request: dict[str, Any]) -> dict[str, Any] | None:
    method = request.get("method")
    request_id = request.get("id")
    if method == "initialize":
        return {
            "jsonrpc": "2.0",
            "id": request_id,
            "result": {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "chatwaifu-local-echo", "version": "1.0.0"},
            },
        }
    if method in {"notifications/initialized", "notifications/cancelled"}:
        return None
    if method != "tools/call":
        return _error(request_id, -32601, "Method not found")
    params = request.get("params", {})
    name = params.get("name")
    arguments = params.get("arguments", {})
    if name == "local_echo":
        text = str(arguments.get("text", ""))
        result = {"echo": text, "spoken_summary": f"插件已回显: {text}"}
    elif name == "append_note":
        text = str(arguments.get("text", ""))
        with (Path.cwd() / "notes.log").open("a", encoding="utf-8") as note_file:
            note_file.write(text.replace("\n", " ") + "\n")
        result = {"written": True, "spoken_summary": "测试笔记已写入插件目录。"}
    elif name == "wait":
        seconds = float(arguments.get("seconds", 0))
        time.sleep(seconds)
        result = {"waited": seconds, "spoken_summary": f"等待了 {seconds:g} 秒。"}
    else:
        return _error(request_id, -32602, "Unknown tool")
    return {
        "jsonrpc": "2.0",
        "id": request_id,
        "result": {
            "content": [{"type": "text", "text": result["spoken_summary"]}],
            "structuredContent": result,
            "isError": False,
        },
    }


def _error(request_id: object, code: int, message: str) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": request_id, "error": {"code": code, "message": message}}


if __name__ == "__main__":
    main()
