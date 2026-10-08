"""Task creation is an ordinary permissioned skill, never a discovery backdoor."""

from datetime import UTC, datetime, timedelta
from typing import cast
from uuid import UUID

from chatwaifu_protocol.agent import AgentTaskCreate, TaskAuthorization
from chatwaifu_protocol.base import JsonObject

from chatwaifu_runtime.agent.tasks import AgentTaskService
from chatwaifu_runtime.external_channels.qq_capabilities import QQSceneCapabilities
from chatwaifu_runtime.runtime_skills.execution_context import (
    authorized_generation,
    authorized_task,
)


class TaskSkills:
    def __init__(self, tasks: AgentTaskService) -> None:
        self.tasks = tasks
        self.scene: QQSceneCapabilities | None = None

    async def create(self, session_id: str, arguments: JsonObject) -> JsonObject:
        if authorized_task.get() is not None:
            raise PermissionError("tasks cannot create recursive tasks")
        request = AgentTaskCreate(
            session_id=UUID(session_id),
            goal=str(arguments["goal"]),
            completion_criteria=[str(v) for v in _list(arguments.get("completion_criteria", []))],
            authorization=TaskAuthorization(
                allowed_skill_ids=[str(v) for v in _list(arguments["allowed_skill_ids"])],
                resource_roots=[str(v) for v in _list(arguments.get("resource_roots", []))],
                calendar_ids=[str(v) for v in _list(arguments.get("calendar_ids", []))],
                allow_writes=arguments.get("allow_writes") is True,
                source_ref="owner-confirmed-skill:" + session_id,
                expires_at=datetime.now(UTC) + timedelta(hours=24),
            ),
        )
        context = authorized_generation.get()
        binding = await self.scene.capture(context) if self.scene and context else None
        task = await self.tasks.create(request, channel_binding=binding)
        return {"task": task.model_dump(mode="json"), "completed": False}

    async def status(self, session_id: str, arguments: JsonObject) -> JsonObject:
        task = await self.tasks.get(UUID(str(arguments["task_id"])), UUID(session_id))
        return {"task": task.model_dump(mode="json")}

    async def defer(self, session_id: str, arguments: JsonObject) -> JsonObject:
        task_id = authorized_task.get()
        if task_id is None:
            raise PermissionError("defer requires the current authorized task")
        await self.tasks.get(task_id, UUID(session_id))
        task = await self.tasks.defer(
            task_id, int(str(arguments["seconds"])), str(arguments["reason"])
        )
        return {"task_id": str(task_id), "wake_at": str(task.wake_at), "deferred": True}


def _list(value: object) -> list[object]:
    if not isinstance(value, list):
        raise ValueError("expected a list")
    return cast(list[object], value)
