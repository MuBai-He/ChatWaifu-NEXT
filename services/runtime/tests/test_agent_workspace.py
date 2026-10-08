"""Real worker output, persistent artifacts and workspace escape prevention."""

import shutil
from pathlib import Path
from uuid import uuid4

import pytest
from chatwaifu_runtime.bootstrap.container import RuntimeContainer
from chatwaifu_runtime.config.settings import Settings
from chatwaifu_runtime.runtime_skills.errors import SkillExecutionError


async def test_workspace_versions_artifacts_and_escape(
    runtime_settings: Settings,
    tmp_path: Path,
) -> None:
    container = RuntimeContainer(runtime_settings)
    await container.start()
    try:
        session = await container.sessions.create_session("default")
        sid = str(session.session_id)
        files = container.workspace_skills
        created = await files.write(sid, {"path": "notes/test.txt", "text": "宁宁的测试"})
        read = await files.read(sid, {"path": "notes/test.txt"})
        assert read["text"] == "宁宁的测试"
        assert read["sha256"] == created["sha256"]
        with pytest.raises(SkillExecutionError, match="checksum"):
            await files.write(sid, {"path": "notes/test.txt", "text": "overwrite"})
        updated = await files.write(
            sid,
            {
                "path": "notes/test.txt",
                "text": "new",
                "expected_sha256": read["sha256"],
            },
        )
        assert updated["sha256"] != created["sha256"]
        with pytest.raises(ValueError, match="relative"):
            await files.read(sid, {"path": "../runtime.db"})
        (files.root / "outside").symlink_to(tmp_path)
        with pytest.raises(ValueError, match="symlinks"):
            await files.read(sid, {"path": "outside/runtime.db"})
        assert (await files.list(sid, {}))["items"]
        person = await container.sessions.create_participant("Other")
        other = await container.sessions.create_session(
            "default", participant_id=person.participant_id
        )
        artifact = created["artifact"]
        assert isinstance(artifact, dict)
        with pytest.raises(KeyError):
            await container.artifacts.resolve(other.session_id, uuid4())
        with pytest.raises(PermissionError):
            await files.read(str(other.session_id), {"path": "notes/test.txt"})
    finally:
        await container.stop()


@pytest.mark.parametrize("kind", ["word", "powerpoint"])
@pytest.mark.skipif(
    shutil.which("soffice") is None or shutil.which("node") is None,
    reason="requires LibreOffice and Node.js; Linux CI installs the real document dependencies",
)
async def test_real_document_worker_and_artifact_inspection(
    runtime_settings: Settings,
    kind: str,
) -> None:
    container = RuntimeContainer(runtime_settings)
    await container.start()
    try:
        session = await container.sessions.create_session("default")
        sid = str(session.session_id)
        if kind == "word":
            result = await container.workspace_skills.word(
                sid,
                {
                    "title": "自主任务测试",
                    "sections": [
                        {"heading": "完成标准", "paragraphs": ["发现能力", "检查实际结果"]},
                    ],
                },
            )
        else:
            result = await container.workspace_skills.powerpoint(
                sid,
                {
                    "title": "自主任务测试",
                    "slides": [
                        {"title": "完成标准", "bullets": ["发现能力", "检查实际结果"]},
                        {
                            "title": "长材料分页",
                            "bullets": [
                                "第一项原始材料需要完整保留。" * 30
                                + "第二项原始材料仍然需要完整保留。" * 30
                            ],
                        },
                    ],
                },
            )
        artifact = result["artifact"]
        assert isinstance(artifact, dict)
        inspected = await container.workspace_skills.inspect(
            sid,
            {
                "artifact_id": artifact["artifact_id"],
            },
        )
        assert inspected["verified"] is True and inspected["parts"]
        assert artifact["byte_length"] and artifact["sha256"]
    finally:
        await container.stop()
