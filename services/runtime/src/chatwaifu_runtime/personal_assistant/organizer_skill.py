"""Trusted-session organizer tools; mutations keep normal Skill confirmation policy."""

from datetime import UTC, datetime
from typing import Literal, cast
from uuid import UUID, uuid4

from chatwaifu_protocol.base import JsonObject
from pydantic import BaseModel, ConfigDict, ValidationError

from chatwaifu_runtime.personal_assistant.integration import PersonalAssistantIntegration
from chatwaifu_runtime.personal_assistant.tasks import AppleOperation, TaskInput
from chatwaifu_runtime.runtime_skills.errors import SkillExecutionError


class TaskChange(BaseModel):
    model_config = ConfigDict(extra="forbid")
    task_id: UUID
    action: Literal["pause", "resume", "cancel"]


class TaskRevision(BaseModel):
    model_config = ConfigDict(extra="forbid")
    task: TaskInput
    expected_revision: int


class OrganizerSkill:
    def __init__(self, integration: PersonalAssistantIntegration, mode: str):
        self.integration = integration
        self.mode = mode

    async def __call__(self, session_id: str, arguments: JsonObject) -> JsonObject:
        service = self.integration.tasks
        if service is None:
            raise SkillExecutionError("assistant_disabled", "服务器尚未启用个人助理。")
        try:
            await service.owner(session_id)
            repository = service.repository
            now = datetime.now(UTC).timestamp()
            if self.mode == "organizer_read":
                return cast(
                    JsonObject,
                    {
                        "devices": await repository.devices(),
                        "tasks": await repository.tasks(),
                        "operations": await repository.operations(),
                        "now": datetime.now(UTC).isoformat(),
                        "scheduler_error": service.last_error,
                        "history": await repository.history(),
                    },
                )
            if self.mode == "schedule_create":
                task = TaskInput.model_validate({"request_id": str(uuid4()), **arguments})
                return cast(
                    JsonObject,
                    {"task": await repository.create_task(task, now), "state": "scheduled"},
                )
            if self.mode == "schedule_update":
                revision = TaskRevision.model_validate(arguments)
                await repository.revise_task(revision.task, revision.expected_revision, now)
                return {"accepted": True}
            if self.mode == "schedule_change":
                change = TaskChange.model_validate(arguments)
                await repository.change_task(str(change.task_id), change.action, now)
                return {"accepted": True}
            operation = AppleOperation.model_validate({"request_id": str(uuid4()), **arguments})
            if (operation.action == "list") != (self.mode == "apple_read"):
                raise ValueError("action_does_not_match_skill_permission")
            receipt = await repository.enqueue(operation, now)
            # Honest async receipt: the caller can query organizer.read for terminal results.
            return cast(
                JsonObject,
                {
                    **receipt,
                    "message": "等待 Mac 执行，不能视为已写入;用 organizer.read 查询结果。",
                },
            )
        except ValidationError:
            raise SkillExecutionError(
                "invalid_organizer_arguments", "参数无效:请提供明确设备、事项和含时区的时间。"
            ) from None
        except ValueError as error:
            raise SkillExecutionError("organizer_request_rejected", str(error)) from None
