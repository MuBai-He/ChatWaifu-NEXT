"""Real Runtime HTTP boundary with a synthetic model; accepts fixture records only."""

import hashlib
import json
import tempfile
from pathlib import Path

import uvicorn
from chatwaifu_runtime.config.settings import Settings
from chatwaifu_runtime.main import create_app
from chatwaifu_runtime.providers.model_config import ModelRole, ModelRoleConfig

TOKEN = "fixture-runtime-connection-token-0000000000"
ROOM = hashlib.sha256(b"fixture-account\0fixture-room").hexdigest()
directory = Path(tempfile.mkdtemp(prefix="inbox-runtime-fixture-"))
settings = Settings.model_validate(
    {
        "config_dir": directory / "config",
        "data_dir": directory,
        "storage": {"database_path": directory / "runtime.db"},
        "llm": {"provider": "demo", "demo_chunk_delay_ms": 0},
        "tts": {"provider": "fake"},
        "security": {"capability_token": TOKEN},
    }
)
app = create_app(settings)
models = app.state.container.model_configurations
original_get = models.get


def get(role: ModelRole) -> ModelRoleConfig:
    config = original_get(role)
    return (
        config.model_copy(update={"provider": "openai_compatible", "enabled": True})
        if role == "memory_extraction"
        else config
    )


async def complete(role: ModelRole, system: str, user: str) -> str:
    assert role == "memory_extraction"
    body = json.loads(user)
    assert all(item["room_ref"] == ROOM for item in body["records"])
    first = body["records"][0]
    item = {
        "title": "合成信息",
        "kind": "memory",
        "importance": "high",
        "reason": "用于验证后端整理流程",
        "source_refs": [first["ref"]],
        "involvement": "self",
    }
    if first["text"].startswith("[fixture:event]"):
        item.update(
            title="合成会议",
            kind="event",
            starts_at="2026-10-13T18:00:00+08:00",
            ends_at="2026-10-13T19:00:00+08:00",
        )
    elif first["text"].startswith("[fixture:update]"):
        target = body["known_items"][0]
        item.update(
            title="资料改到周五",
            kind="todo",
            operation="update",
            target_id=target["id"],
            expected_revision=target["revision"],
            due_at="2026-10-16T18:00:00+08:00",
        )
    elif first["text"].startswith("[fixture:complete]"):
        target = body["known_items"][0]
        item.update(
            title="资料已收到",
            kind="todo",
            operation="complete",
            target_id=target["id"],
            expected_revision=target["revision"],
        )
    return json.dumps({"items": [item]}, ensure_ascii=False)


models.get = get
models.complete = complete
print("Synthetic Runtime fixture starting on 127.0.0.1:18631", flush=True)
uvicorn.run(app, host="127.0.0.1", port=18631, log_level="error", access_log=False)
