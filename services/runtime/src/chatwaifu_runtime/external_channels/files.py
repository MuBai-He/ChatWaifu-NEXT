"""Current-owner file reply through the durable channel delivery scheduler."""

import asyncio
import hashlib
from collections.abc import Callable
from datetime import UTC, datetime
from uuid import UUID, uuid4, uuid5

from chatwaifu_protocol.agent import AgentTask, TaskDeliveryTarget
from chatwaifu_protocol.base import JsonObject
from chatwaifu_protocol.channels import (
    ChannelChatType,
    ChannelDeliveryPartDraft,
    ChannelDeliveryPartKind,
    ChannelDeliveryPartsCancelRequest,
    ChannelDeliveryStatus,
    ChannelFileDeliveryPartPayload,
    ChannelTextDeliveryPartPayload,
)

from chatwaifu_runtime.agent.artifacts import ArtifactService
from chatwaifu_runtime.eventing.publisher import EventPublisher
from chatwaifu_runtime.external_channels.models import (
    ChannelDeliveryPlanRecord,
    DeliveryTransitionResult,
)
from chatwaifu_runtime.external_channels.ports import ExternalChannelRepository
from chatwaifu_runtime.external_channels.qq_capabilities import QQSceneCapabilities
from chatwaifu_runtime.runtime_skills.adapters import GenerationSkillContext


class ChannelFileSkill:
    def __init__(
        self,
        repository: ExternalChannelRepository,
        artifacts: ArtifactService,
        scene: QQSceneCapabilities,
        publisher: EventPublisher,
        enabled: Callable[[], bool],
    ) -> None:
        self.repository = repository
        self.artifacts = artifacts
        self.scene = scene
        self.publisher = publisher
        self.enabled = enabled
        self.changed = asyncio.Condition()

    async def authorize(self, context: GenerationSkillContext) -> bool:
        if context.task_id is not None:
            return await self.scene.binding(context) is not None
        turn = await self.scene.current(context)
        return self.enabled() and turn is not None and turn.chat_type is ChannelChatType.DIRECT

    async def __call__(self, context: GenerationSkillContext, arguments: JsonObject) -> JsonObject:
        turn = await self.scene.current(context)
        binding = await self.scene.binding(context)
        if binding is None or not await self.authorize(context):
            raise PermissionError("file reply has no current owner request")
        artifact, _path = await self.artifacts.resolve(
            context.session_id, UUID(str(arguments["artifact_id"]))
        )
        if turn is not None and context.task_id is None and turn.delivery_id is not None:
            raise ValueError("this reply already has a delivery plan")
        delivery_id = uuid4()
        payload = ChannelFileDeliveryPartPayload(
            artifact_id=artifact.artifact_id,
            sha256=artifact.sha256,
            name=artifact.name,
            mime_type=artifact.media_type,
            byte_length=artifact.byte_length,
        )
        parts = [
            ChannelDeliveryPartDraft(ordinal=0, kind=ChannelDeliveryPartKind.FILE, payload=payload)
        ]
        if context.task_id is not None:
            identity = uuid5(context.task_id, "file:" + str(artifact.artifact_id))
            delivery_id = uuid5(identity, "delivery")
            target = TaskDeliveryTarget(
                request_id=identity,
                task_id=context.task_id,
                session_id=context.session_id,
                turn_id=context.turn_id or uuid5(identity, "turn"),
                generation_id=context.generation_id or uuid5(identity, "generation"),
                binding=binding,
            )
            transition = await self.repository.create_task_delivery_plan(
                target, delivery_id=delivery_id, parts=parts, created_at=datetime.now(UTC)
            )
        else:
            assert turn is not None
            transition = await self.repository.create_delivery_plan(
                turn.channel_turn_id,
                delivery_id=delivery_id,
                parts=parts,
                created_at=datetime.now(UTC),
            )
        if isinstance(transition, DeliveryTransitionResult):
            for event in transition.persisted_events:
                await self.publisher.publish_persisted(event)
        try:
            async with asyncio.timeout(110):
                async with self.changed:
                    while True:
                        plan = await self.repository.get_delivery_plan(delivery_id)
                        if plan is None:
                            raise KeyError("file delivery plan missing")
                        if plan.status in {
                            ChannelDeliveryStatus.DELIVERED,
                            ChannelDeliveryStatus.FAILED,
                            ChannelDeliveryStatus.CANCELLED,
                        }:
                            return {
                                "artifact_id": str(artifact.artifact_id),
                                "delivery_id": str(delivery_id),
                                "status": plan.status.value,
                                "platform_accepted": plan.status is ChannelDeliveryStatus.DELIVERED,
                                "user_received": None,
                                "receipt_verified": any(p.provider_message_id for p in plan.parts),
                                "outcome": "unknown"
                                if any(
                                    p.last_error and "unknown" in p.last_error.code
                                    for p in plan.parts
                                )
                                else (
                                    "accepted"
                                    if plan.status is ChannelDeliveryStatus.DELIVERED
                                    else "rejected"
                                ),
                            }
                        if not await self.authorize(context):
                            raise asyncio.CancelledError("file reply authorization revoked")
                        await self.changed.wait()
        except (TimeoutError, asyncio.CancelledError):
            cancelled = await self.repository.cancel_remaining_delivery_parts(
                delivery_id,
                ChannelDeliveryPartsCancelRequest(
                    reason="file_reply_interrupted", requested_at=datetime.now(UTC)
                ),
            )
            for event in cancelled.persisted_events:
                await self.publisher.publish_persisted(event)
            raise

    async def on_terminal(self, plan: ChannelDeliveryPlanRecord) -> None:
        del plan
        async with self.changed:
            self.changed.notify_all()

    async def publish_task_result(self, task: AgentTask) -> UUID | None:
        if task.channel_binding is None:
            return None
        text = (
            task.result_text or "任务执行结果: " + str(task.blocked_reason or task.state.value)
        )[:2000]
        identity = uuid5(task.task_id, "result:" + hashlib.sha256(text.encode()).hexdigest())
        target = TaskDeliveryTarget(
            request_id=identity,
            task_id=task.task_id,
            session_id=task.session_id,
            turn_id=uuid5(identity, "turn"),
            generation_id=uuid5(identity, "generation"),
            binding=task.channel_binding,
            purpose="result",
        )
        delivery_id = uuid5(identity, "delivery")
        transition = await self.repository.create_task_delivery_plan(
            target,
            delivery_id=delivery_id,
            created_at=datetime.now(UTC),
            parts=[
                ChannelDeliveryPartDraft(
                    ordinal=0,
                    kind=ChannelDeliveryPartKind.TEXT,
                    payload=ChannelTextDeliveryPartPayload(text=text),
                )
            ],
        )
        for event in transition.persisted_events:
            await self.publisher.publish_persisted(event)
        return delivery_id
