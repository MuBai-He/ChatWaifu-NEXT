"""Task receipt reads preserve authenticated owner and scene visibility."""

from datetime import UTC, datetime, timedelta
from uuid import uuid4

from chatwaifu_protocol.agent import AgentTaskCreate, TaskAuthorization
from chatwaifu_runtime.config.settings import Settings
from chatwaifu_runtime.main import create_app
from httpx2 import ASGITransport, AsyncClient


async def test_task_delivery_receipt_requires_authentication_and_visible_task(
    runtime_settings: Settings,
) -> None:
    app = create_app(runtime_settings)
    container = app.state.container
    await container.start()
    try:
        await container.agent_tasks.stop()
        owner = await container.sessions.create_session("default")
        person = await container.sessions.create_participant("Other")
        other = await container.sessions.create_session(
            "default", participant_id=person.participant_id
        )
        task = await container.agent_tasks.create(
            AgentTaskCreate(
                session_id=owner.session_id,
                goal="核对任务收件状态",
                authorization=TaskAuthorization(
                    allowed_skill_ids=["workspace.files"],
                    resource_roots=["."],
                    source_ref="owner:api",
                    expires_at=datetime.now(UTC) + timedelta(hours=1),
                ),
            )
        )
        async with AsyncClient(transport=ASGITransport(app), base_url="http://testserver") as http:
            path = f"/v1/agent/tasks/{task.task_id}/delivery"
            params = {"session_id": str(owner.session_id)}
            assert (await http.get(path, params=params)).status_code == 401
            http.headers["Authorization"] = f"Bearer {container.capability_token}"
            response = await http.get(path, params=params)
            assert response.status_code == 200 and response.json() is None
            hidden = await http.get(path, params={"session_id": str(other.session_id)})
            assert hidden.status_code == 404
            # A foreign/missing ledger reference cannot be exposed as this task's receipt.
            await container.agent_tasks.update_checkpoint(task.task_id, delivery_id=uuid4())
            assert (await http.get(path, params=params)).status_code == 409
    finally:
        await container.stop()
