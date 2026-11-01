"""Conversation tools use the same owner harness as the product interfaces."""

from collections.abc import Awaitable, Callable
from typing import cast
from uuid import UUID

from chatwaifu_protocol.base import JsonObject
from chatwaifu_protocol.harness import (
    IntentAction,
    StandingIntentInput,
    StandingIntentUpdate,
    WorkItemAction,
)

from chatwaifu_runtime.agent.harness import AgentHarnessService
from chatwaifu_runtime.agent.review import HarnessReviewService, ReviewDecision
from chatwaifu_runtime.runtime_skills.errors import SkillExecutionError
from chatwaifu_runtime.runtime_skills.execution_context import authorized_task


class HarnessSkills:
    def __init__(
        self, harness: AgentHarnessService, review: HarnessReviewService | None = None
    ) -> None:
        self.harness = harness
        self.review = review
        self.review_query: Callable[[UUID], Awaitable[list[JsonObject]]] | None = None

    async def query(self, session_id: str, arguments: JsonObject) -> JsonObject:
        owner = await self.harness.owner(UUID(session_id))
        resources = (
            await self.harness.resources(UUID(session_id))
            if self.harness.resources is not None
            else {}
        )
        return cast(
            JsonObject,
            {
                **resources,
                "review": await self.review_query(UUID(session_id)) if self.review_query else [],
                "intents": [
                    i.model_dump(mode="json") for i in await self.harness.repository.intents(owner)
                ],
                "work_items": [
                    w.model_dump(mode="json")
                    for w in await self.harness.repository.work_items(owner)
                    if await self.harness.eligible(owner, w.evidence)
                ],
                "activity": [
                    a.model_dump(mode="json")
                    for a in await self.harness.repository.activity(owner)
                    if await self.harness.eligible(owner, a.evidence)
                ],
                "sources": await self.harness.knowledge.repository.source_rooms(owner),
                "skills": [
                    {"skill_id": s.skill_id, "name": s.name}
                    for s in self.harness.tasks.skills.list()
                    if s.enabled and "unattended" in s.execution_modes
                ],
            },
        )

    async def manage(self, session_id: str, arguments: JsonObject) -> JsonObject:
        if authorized_task.get() is not None:
            raise SkillExecutionError("delegation_owner_required", "持续委托需由主人直接交办。")
        try:
            action = str(arguments.get("action"))
            if action == "review":
                if self.review is None:
                    raise ValueError("review_not_available")
                return await self.review.decide(
                    str(arguments["review_kind"]),
                    str(arguments["reference"]),
                    ReviewDecision.model_validate(
                        {
                            "session_id": session_id,
                            "decision": arguments["decision"],
                            "expected_revision": arguments.get("expected_revision", 0),
                        }
                    ),
                )
            if action == "create":
                item = await self.harness.create_intent(
                    StandingIntentInput.model_validate(
                        {
                            k: v
                            for k, v in {**arguments, "session_id": session_id}.items()
                            if k != "action"
                        }
                    )
                )
                return cast(JsonObject, item.model_dump(mode="json"))
            if action == "update_intent":
                item = await self.harness.update_intent(
                    UUID(str(arguments["intent_id"])),
                    StandingIntentUpdate.model_validate(
                        {
                            k: v
                            for k, v in {**arguments, "session_id": session_id}.items()
                            if k not in {"action", "intent_id"}
                        }
                    ),
                )
                return cast(JsonObject, item.model_dump(mode="json"))
            if action in {"pause", "resume", "revoke", "enable"}:
                item = await self.harness.act_intent(
                    UUID(str(arguments["intent_id"])),
                    IntentAction.model_validate(
                        {
                            "session_id": session_id,
                            "expected_revision": arguments["expected_revision"],
                            "action": action,
                        }
                    ),
                )
                return cast(JsonObject, item.model_dump(mode="json"))
            work = await self.harness.act_work(
                str(arguments["work_item_id"]),
                WorkItemAction.model_validate(
                    {
                        "session_id": session_id,
                        "expected_revision": arguments["expected_revision"],
                        "action": action,
                        "changes": arguments.get("changes"),
                        "target_id": arguments.get("target_id"),
                        "expected_target_revision": arguments.get("expected_target_revision"),
                    }
                ),
            )
            return cast(JsonObject, work.model_dump(mode="json"))
        except (PermissionError, ValueError, KeyError):
            raise SkillExecutionError(
                "assist_state_changed", "范围或资料已变化，请重新查询后处理。"
            ) from None
