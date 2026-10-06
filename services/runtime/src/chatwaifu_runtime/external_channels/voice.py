"""Current-turn voice Runtime Skill, using the ordinary durable channel delivery plan."""

from __future__ import annotations

import asyncio
import hashlib
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol
from uuid import UUID, uuid4

from chatwaifu_protocol.base import JsonObject
from chatwaifu_protocol.channels import (
    ChannelAudioDeliveryPartPayload,
    ChannelChatType,
    ChannelConnectionConfiguration,
    ChannelDeliveryPartDraft,
    ChannelDeliveryPartKind,
    ChannelDeliveryPartsCancelRequest,
    ChannelDeliveryPartStatus,
    ChannelDeliveryStatus,
    ChannelTurnStatus,
)

from chatwaifu_runtime.characters.service import CharacterService
from chatwaifu_runtime.conversation.repository import ConversationRepository
from chatwaifu_runtime.conversation.speech import synthesis_language_for_text
from chatwaifu_runtime.eventing.publisher import EventPublisher
from chatwaifu_runtime.external_channels.models import (
    ChannelDeliveryPlanRecord,
    ChannelTurnRecord,
    DeliveryTransitionResult,
)
from chatwaifu_runtime.external_channels.ports import ExternalChannelRepository
from chatwaifu_runtime.providers.contracts import SynthesisRequest
from chatwaifu_runtime.providers.tts_router import TtsRouter
from chatwaifu_runtime.runtime_skills.adapters import GenerationSkillContext
from chatwaifu_runtime.runtime_skills.errors import SkillExecutionError


class GroupReplyVoicePort(Protocol):
    async def authorize_reply_voice(self, turn: ChannelTurnRecord) -> bool: ...

    async def publish_reply_voice(
        self,
        turn: ChannelTurnRecord,
        payload: ChannelAudioDeliveryPartPayload,
        delivery_id: UUID,
        created_at: datetime,
    ) -> None: ...


class ChannelVoiceSkill:
    def __init__(
        self,
        repository: ExternalChannelRepository,
        conversations: ConversationRepository,
        characters: CharacterService,
        tts: TtsRouter,
        publisher: EventPublisher,
        audio_root: Path,
        active_generation: Callable[[UUID], UUID | None],
        supports_audio: Callable[[str], bool],
        *,
        private_voice_enabled: Callable[[], bool] = lambda: True,
    ) -> None:
        self._repository = repository
        self._conversations = conversations
        self._characters = characters
        self._tts = tts
        self._publisher = publisher
        self.audio_root = audio_root
        self._active_generation = active_generation
        self._supports_audio = supports_audio
        self._private_voice_enabled = private_voice_enabled
        self._changed = asyncio.Condition()
        self._executing: set[UUID] = set()
        self._groups: GroupReplyVoicePort | None = None

    def set_group_service(self, groups: GroupReplyVoicePort) -> None:
        self._groups = groups

    async def _turn(self, context: GenerationSkillContext) -> ChannelTurnRecord | None:
        if context.origin != "agent" or context.generation_id is None or context.turn_id is None:
            return None
        if self._active_generation(context.session_id) != context.generation_id:
            return None
        for turn in await self._repository.list_inflight_turns():
            if (
                turn.session_id == context.session_id
                and turn.turn_id == context.turn_id
                and turn.generation_id == context.generation_id
                and turn.status in {ChannelTurnStatus.ACCEPTED, ChannelTurnStatus.PROCESSING}
            ):
                connection = await self._repository.get_connection(turn.connection_id)
                if (
                    connection is not None
                    and connection.configuration.enabled
                    and connection.configuration.provider_id == "qq_napcat"
                    and self._supports_audio(connection.configuration.provider_id)
                ):
                    if turn.chat_type is ChannelChatType.DIRECT:
                        if (
                            self._private_voice_enabled()
                            and turn.sender_key in connection.configuration.allowed_sender_keys
                        ):
                            return turn
                    elif self._groups is not None and await self._groups.authorize_reply_voice(
                        turn
                    ):
                        return turn
        return None

    async def authorize(self, context: GenerationSkillContext) -> bool:
        turn = await self._turn(context)
        if turn is None or context.generation_id is None:
            return False
        source = await self._conversations.generation_user_input_context(context.generation_id)
        return (
            source is not None
            and bool(source.user_text.strip())
            and self._active_generation(context.session_id) == context.generation_id
            and (turn.chat_type is not ChannelChatType.DIRECT or self._private_voice_enabled())
        )

    async def __call__(self, context: GenerationSkillContext, arguments: JsonObject) -> JsonObject:
        turn = await self._turn(context)
        text = arguments.get("text")
        if turn is None or not await self.authorize(context):
            raise SkillExecutionError("voice_not_authorized", "当前轮次没有有效的语音回复权限。")
        if not isinstance(text, str) or not text.strip() or len(text) > 2000:
            raise SkillExecutionError("voice_text_invalid", "语音内容须为 1-2000 字。")
        if turn.delivery_id is not None or turn.generation_id in self._executing:
            raise SkillExecutionError("voice_already_requested", "本轮已请求语音，请勿重复发送。")
        self._executing.add(turn.generation_id)
        asset_id = uuid4()
        path = self.audio_root / f"{asset_id}.wav"
        delivery_id: UUID | None = None
        try:
            character = self._characters.get(
                (await self._required_configuration(turn)).character_id
            )
            if character is None:
                raise SkillExecutionError("voice_character_missing", "角色配置不可用。")
            voice = character.voice_profile
            await asyncio.to_thread(self.audio_root.mkdir, parents=True, exist_ok=True, mode=0o700)
            result = await self._tts.synthesize(
                SynthesisRequest(
                    session_id=turn.session_id,
                    turn_id=turn.turn_id,
                    generation_id=turn.generation_id,
                    segment_id=asset_id,
                    text=text.strip(),
                    destination=path,
                    language=synthesis_language_for_text(text, voice.language),
                    voice_id=voice.voice_id,
                    speaker_id=voice.speaker_id,
                    speed=voice.speed,
                )
            )
            if (
                result.path != path
                or path.is_symlink()
                or not path.is_file()
                or path.stat().st_size > 8 * 1024 * 1024
            ):
                raise SkillExecutionError("voice_asset_invalid", "语音文件不可用或过大。")
            if (
                result.media_type not in {"audio/wav", "audio/x-wav"}
                or not 1 <= result.duration_ms <= 120_000
            ):
                raise SkillExecutionError("voice_format_invalid", "语音格式或时长不受支持。")
            data = await asyncio.to_thread(path.read_bytes)
            if not await self.authorize(context):
                raise asyncio.CancelledError
            payload = ChannelAudioDeliveryPartPayload(
                asset_id=asset_id,
                sha256=hashlib.sha256(data).hexdigest(),
                duration_ms=result.duration_ms,
                text=text.strip(),
            )
            delivery_id = uuid4()
            if turn.chat_type is ChannelChatType.GROUP:
                if self._groups is None:
                    raise SkillExecutionError("voice_not_authorized", "群语音回复不可用。")
                await self._groups.publish_reply_voice(
                    turn, payload, delivery_id, datetime.now(UTC)
                )
            else:
                transition = await self._repository.create_delivery_plan(
                    turn.channel_turn_id,
                    delivery_id=delivery_id,
                    parts=[
                        ChannelDeliveryPartDraft(
                            ordinal=0, kind=ChannelDeliveryPartKind.AUDIO, payload=payload
                        )
                    ],
                    created_at=datetime.now(UTC),
                )
                if isinstance(transition, DeliveryTransitionResult):
                    for event in transition.persisted_events:
                        await self._publisher.publish_persisted(event)
            timed_out = False
            try:
                async with asyncio.timeout(45):
                    async with self._changed:
                        while True:
                            plan = await self._repository.get_delivery_plan(delivery_id)
                            if plan is None:
                                raise SkillExecutionError(
                                    "voice_delivery_missing", "语音投递记录不可用。"
                                )
                            if plan.status in {
                                ChannelDeliveryStatus.DELIVERED,
                                ChannelDeliveryStatus.FAILED,
                                ChannelDeliveryStatus.CANCELLED,
                            }:
                                break
                            if not await self.authorize(context):
                                raise asyncio.CancelledError
                            await self._changed.wait()
            except TimeoutError:
                if not await self.authorize(context):
                    raise asyncio.CancelledError from None
                timed_out = True
                current = await self._repository.get_delivery_plan(delivery_id)
                sending_lease = (
                    next(
                        (
                            part.lease_id
                            for part in current.parts
                            if part.status is ChannelDeliveryPartStatus.SENDING
                        ),
                        None,
                    )
                    if current
                    else None
                )
                transition = await self._repository.cancel_remaining_delivery_parts(
                    delivery_id,
                    ChannelDeliveryPartsCancelRequest(
                        reason="voice_delivery_timeout", requested_at=datetime.now(UTC)
                    ),
                    cancel_sending_lease_id=sending_lease,
                )
                for event in transition.persisted_events:
                    await self._publisher.publish_persisted(event)
                plan = await self._repository.get_delivery_plan(delivery_id)
                if plan is None:
                    raise SkillExecutionError(
                        "voice_delivery_missing", "语音投递记录不可用。"
                    ) from None
                await self.on_plan_terminal(plan)
            if plan.parts[0].status != ChannelDeliveryPartStatus.DELIVERED:
                error = plan.parts[0].last_error
                prefix = (
                    "这条语音的发送结果未确认，先把内容发成文字: "
                    if timed_out or (error and error.code == "qq_delivery_unknown")
                    else "语音没能发出去，先把内容发成文字: "
                )
                return {
                    "delivery_status": "text_fallback",
                    "spoken_text": prefix + text.strip(),
                    "delivery_id": str(delivery_id),
                }
            return {
                "delivery_status": "delivered",
                "spoken_text": text.strip(),
                "delivery_id": str(delivery_id),
            }
        except BaseException:
            if delivery_id is not None:
                cancelled = await self._repository.cancel_remaining_delivery_parts(
                    delivery_id,
                    ChannelDeliveryPartsCancelRequest(
                        reason="voice_tool_stopped", requested_at=datetime.now(UTC)
                    ),
                )
                for event in cancelled.persisted_events:
                    await self._publisher.publish_persisted(event)
                if cancelled.plan.status in {
                    ChannelDeliveryStatus.DELIVERED,
                    ChannelDeliveryStatus.FAILED,
                    ChannelDeliveryStatus.CANCELLED,
                }:
                    await self.on_plan_terminal(cancelled.plan)
            raise
        finally:
            self._executing.discard(turn.generation_id)
            if delivery_id is None:
                await asyncio.to_thread(path.unlink, missing_ok=True)

    async def _required_configuration(
        self, turn: ChannelTurnRecord
    ) -> ChannelConnectionConfiguration:
        connection = await self._repository.get_connection(turn.connection_id)
        if connection is None:
            raise SkillExecutionError("voice_connection_missing", "QQ 连接已移除。")
        return connection.configuration

    async def on_plan_terminal(self, plan: ChannelDeliveryPlanRecord) -> None:
        async with self._changed:
            self._changed.notify_all()
        for part in plan.parts:
            if isinstance(part.payload, ChannelAudioDeliveryPartPayload):
                await asyncio.to_thread(
                    (self.audio_root / f"{part.payload.asset_id}.wav").unlink, missing_ok=True
                )

    async def cleanup(self) -> None:
        active = await self._repository.list_nonterminal_delivery_plans(limit=10_000)
        keep = {
            f"{part.payload.asset_id}.wav"
            for plan in active
            for part in plan.parts
            if isinstance(part.payload, ChannelAudioDeliveryPartPayload)
        }

        def remove_orphans() -> None:
            if self.audio_root.exists():
                for path in self.audio_root.glob("*.wav"):
                    if path.name not in keep:
                        path.unlink(missing_ok=True)

        await asyncio.to_thread(remove_orphans)
