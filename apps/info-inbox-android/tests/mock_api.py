#!/usr/bin/env python3
"""Local synthetic API. Rejects records outside the fixed fixture conversation."""

import hashlib
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

ROOM = hashlib.sha256(b"fixture-account\0fixture-room").hexdigest()


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def reply(self, body):
        data = json.dumps(body, ensure_ascii=False).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        if self.path != "/v1/models" or self.headers.get("Authorization") != "Bearer test-key":
            self.send_error(400)
            return
        self.reply({"data": [{"id": "fixture-model"}]})

    def do_POST(self):
        if (
            self.path != "/v1/chat/completions"
            or self.headers.get("Authorization") != "Bearer test-key"
        ):
            self.send_error(400)
            return
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        assert body["model"] == "fixture-model" and body["stream"] is False
        assert body["response_format"]["type"] == "json_object"
        data = json.loads(body["messages"][1]["content"])
        records = data["records"]
        assert 1 <= len(records) <= 30
        assert all(record.get("room_ref") == ROOM for record in records)
        text = records[0]["text"]
        item = {
            "title": "合成测试记忆",
            "kind": "memory",
            "importance": "high",
            "reason": "用于验证接口与出处",
            "source_refs": [records[0]["ref"]],
            "due_text": None,
            "involvement": "self",
        }
        if text.startswith("[fixture:event]"):
            item.update(
                title="合成会议",
                kind="event",
                operation="create",
                starts_at="2026-10-13T18:00:00+08:00",
                ends_at="2026-10-13T19:00:00+08:00",
            )
        elif text.startswith("[fixture:update]"):
            target = data["known_items"][0]
            assert target["room_ref"] == ROOM
            item.update(
                title="资料改到周五提交",
                kind="todo",
                operation="update",
                target_id=target["id"],
                expected_revision=target["revision"],
                due_at="2026-10-16T18:00:00+08:00",
            )
        elif text.startswith("[fixture:complete]"):
            target = data["known_items"][0]
            item.update(
                title="资料已收到",
                kind="todo",
                operation="complete",
                target_id=target["id"],
                expected_revision=target["revision"],
            )
        self.reply(
            {
                "choices": [
                    {"message": {"content": json.dumps({"items": [item]}, ensure_ascii=False)}}
                ]
            }
        )


print("Synthetic fixture API ready on localhost:18630", flush=True)
ThreadingHTTPServer(("127.0.0.1", 18630), Handler).serve_forever()
