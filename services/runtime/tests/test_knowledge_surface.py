"""The public map shell has no data authority; data still needs scoped authentication."""

from pathlib import Path
from typing import cast
from uuid import UUID, uuid4

import pytest
from chatwaifu_runtime.config.settings import Settings
from chatwaifu_runtime.knowledge.service import KnowledgeService
from chatwaifu_runtime.knowledge.skill import KnowledgeSearchSkill
from chatwaifu_runtime.main import create_app
from chatwaifu_runtime.runtime_skills.errors import SkillExecutionError
from fastapi.testclient import TestClient


def test_embedded_shell_and_assets_are_public_but_other_paths_are_not(
    runtime_settings: Settings, tmp_path: Path
) -> None:
    static = tmp_path / "map"
    static.mkdir()
    (static / "index.html").write_text("<html>knowledge shell</html>")
    (static / "bundle.js").write_text("void 0;")
    settings = runtime_settings.model_copy(
        update={
            "knowledge": runtime_settings.knowledge.model_copy(
                update={"enabled": True, "web_dir": static}
            )
        }
    )
    with TestClient(create_app(settings)) as c:
        page = c.get("/knowledge")
        assert page.status_code == 200
        assert "frame-ancestors 'none'" in page.headers["content-security-policy"]
        assert c.get("/knowledge-assets/bundle.js").status_code == 200
        assert c.get("/v1/knowledge/status", params={"session_id": str(uuid4())}).status_code == 401
        assert c.get("/knowledge-assets/../v1/sessions").status_code == 401


@pytest.mark.asyncio
async def test_skill_does_not_accept_caller_session_or_owner() -> None:
    class Service:
        retrieval = None

        async def owner(self, session: UUID) -> str:
            raise ValueError("private_owner_required")

    skill = KnowledgeSearchSkill(cast(KnowledgeService, Service()))
    with pytest.raises(SkillExecutionError):
        await skill(str(uuid4()), {"question": "私有资料", "session_id": str(uuid4())})
    with pytest.raises(SkillExecutionError):
        await skill(str(uuid4()), {"question": "私有资料"})


def test_password_session_and_native_viewer_share_only_the_knowledge_surface(
    runtime_settings: Settings,
) -> None:
    app = create_app(runtime_settings)
    owner = {"Authorization": f"Bearer {app.state.container.capability_token}"}
    with TestClient(app) as client:
        login = client.put(
            "/v1/runtime/password",
            headers=owner,
            json={"new_password": "knowledge-fixture-password-2026", "expected_revision": 0},
        )
        assert login.status_code == 200
        session_auth = {"Authorization": f"Bearer {login.json()['token']}"}
        session = client.post("/v1/sessions", headers=session_auth, json={}).json()["session_id"]
        viewer = client.post(
            "/v1/knowledge/viewer-session",
            headers=session_auth,
            json={"session_id": session, "device_id": str(uuid4())},
        )
        assert viewer.status_code == 200
        scoped = {"Cookie": viewer.json()["cookie"].split(";", 1)[0]}
        assert (
            client.get(
                "/v1/knowledge/status", params={"session_id": session}, headers=scoped
            ).status_code
            == 200
        )
        assert client.get("/v1/runtime/password", headers=scoped).status_code == 401
        assert client.get("/v1/model-configs", headers=scoped).status_code == 401
        assert client.get("/v1/runtime/password", headers=session_auth).status_code == 200
