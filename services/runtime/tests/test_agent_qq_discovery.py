"""Version pins, scene references and no-HTTP-after-revocation discovery fences."""

# pyright: reportPrivateUsage=false
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, cast
from uuid import uuid4

import pytest
from chatwaifu_protocol.agent import TaskChannelBinding
from chatwaifu_protocol.base import JsonObject
from chatwaifu_runtime.agent.materials import extract_material
from chatwaifu_runtime.bootstrap.container import RuntimeContainer
from chatwaifu_runtime.config.settings import Settings
from chatwaifu_runtime.external_channels.adapters.qq_napcat.catalog import catalog_versions
from chatwaifu_runtime.external_channels.adapters.qq_napcat.client import NapCatError
from chatwaifu_runtime.runtime_skills.adapters import GenerationSkillContext


def test_compatibility_evidence_does_not_cover_additional_actions(tmp_path: Path) -> None:
    import json

    root = tmp_path / "builtin" / "qq-scene"
    root.mkdir(parents=True)
    path = root / "api-catalog.json"
    data: dict[str, Any] = {
        "version": "4.18.33",
        "actions": [{"action": "get_msg", "reviewed": True}],
        "compatible_versions": [
            {
                "version": "4.18.28",
                "sha256": "0" * 64,
                "reviewed_actions": ["get_msg"],
            }
        ],
    }
    path.write_text(json.dumps(data), encoding="utf-8")
    assert catalog_versions(tmp_path) == {"4.18.33", "4.18.28"}
    data["actions"].append({"action": "get_group_info", "reviewed": True})
    path.write_text(json.dumps(data), encoding="utf-8")
    assert catalog_versions(tmp_path) == {"4.18.33"}


async def test_qq_catalog_version_and_late_authorization_fences(
    runtime_settings: Settings,
) -> None:
    container = RuntimeContainer(runtime_settings)
    management = container.qq_channels
    connection_id = uuid4()

    class Client:
        version = "4.18.33"
        replace_during_preflight = False
        calls: list[str]

        def __init__(self) -> None:
            self.calls = []

        async def call(self, action: str, params: JsonObject) -> JsonObject:
            self.calls.append(action)
            if action == "get_login_info":
                return {"user_id": "10001"}
            if action == "get_version_info":
                if self.replace_during_preflight:
                    management._clients.pop(connection_id)
                return {"app_version": self.version}
            return {"message": "scoped result"}

    client = Client()
    management._clients[connection_id] = cast(Any, client)
    admitted = True

    async def guard() -> bool:
        return admitted

    try:
        client.version = "4.18.32"
        with pytest.raises(NapCatError, match="version differs"):
            await management.scoped_agent_call(connection_id, "10001", "get_msg", {}, guard)
        assert "get_msg" not in client.calls
        client.version = "4.18.33"
        result = await management.scoped_agent_call(connection_id, "10001", "get_msg", {}, guard)
        assert result["message"] == "scoped result"
        client.version = "4.18.28"
        result = await management.scoped_agent_call(connection_id, "10001", "get_msg", {}, guard)
        assert result["message"] == "scoped result"
        admitted = False
        before = len(client.calls)
        with pytest.raises(NapCatError, match="unavailable"):
            await management.scoped_agent_call(connection_id, "10001", "get_msg", {}, guard)
        assert len(client.calls) == before
        admitted = True
        client.replace_during_preflight = True
        calls_before = client.calls.count("get_msg")
        with pytest.raises(NapCatError, match="version preflight"):
            await management.scoped_agent_call(connection_id, "10001", "get_msg", {}, guard)
        assert client.calls.count("get_msg") == calls_before
    finally:
        management._clients.clear()


async def test_qq_opaque_references_expire_and_revocation_prevents_download(
    runtime_settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    container = RuntimeContainer(runtime_settings)
    scene = container.qq_scene_capabilities
    binding = TaskChannelBinding(
        connection_id=uuid4(),
        account_key="10001",
        conversation_key="group:1",
        sender_key="member",
        source_ref="original-message",
    )
    token = scene.resource(binding, {"file_id": "original-file", "name": "资料.txt"})
    assert scene.resolve_resource(binding, token)["file_id"] == "original-file"
    with pytest.raises(PermissionError, match="another scene"):
        scene.resolve_resource(binding.model_copy(update={"source_ref": "other-message"}), token)
    saved = scene._resources[token]
    scene._resources[token] = (saved[0], saved[1], datetime.now(UTC) - timedelta(seconds=1))
    with pytest.raises(PermissionError, match="expired"):
        scene.resolve_resource(binding, token)

    async def revoked(context: GenerationSkillContext) -> None:
        return None

    resolved = False

    async def validate(url: str) -> Any:
        nonlocal resolved
        resolved = True
        return object()

    monkeypatch.setattr(scene, "binding", revoked)
    monkeypatch.setattr(
        "chatwaifu_runtime.external_channels.qq_capabilities.validate_public_url", validate
    )
    context = GenerationSkillContext(uuid4(), uuid4(), uuid4(), "agent")
    with pytest.raises(PermissionError, match="before download"):
        await scene.read_material(
            context, binding, {"url": "https://files.qq.com/a", "name": "资料.txt"}
        )
    assert resolved


async def test_real_material_worker_extracts_docx_and_rejects_unsupported_type(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import io

    from docx import Document

    monkeypatch.setenv("PYTHONIOENCODING", "cp1252")
    document = Document()
    document.add_paragraph("材料来源完整，数字 42 和中文内容均保留。")
    buffer = io.BytesIO()
    document.save(buffer)
    assert "数字 42" in await extract_material("资料.docx", buffer.getvalue())
    assert await extract_material("资料.txt", "原始群资料".encode()) == "原始群资料"
    with pytest.raises(ValueError, match="additional extraction adapter"):
        await extract_material("unsupported.exe", b"untrusted")
