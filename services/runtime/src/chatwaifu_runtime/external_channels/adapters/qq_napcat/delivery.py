"""OneBot delivery with a durable send fence for transports without idempotency keys."""

from __future__ import annotations

import asyncio
import base64
import hashlib
import io
import json
import logging
import re
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import cast
from uuid import UUID

from chatwaifu_protocol.base import JsonObject
from chatwaifu_protocol.channels import (
    ChannelAudioDeliveryPartPayload,
    ChannelChatType,
    ChannelDeliveryPartKind,
    ChannelDeliveryPartStatus,
    ChannelDeliveryStatus,
    ChannelImageDeliveryPartPayload,
    ChannelMessageKind,
    ChannelTextDeliveryPartPayload,
    ChannelTurnStatus,
)
from chatwaifu_protocol.errors import StructuredError
from PIL import Image

from chatwaifu_runtime.eventing.publisher import EventPublisher
from chatwaifu_runtime.external_channels.models import (
    ChannelConnectionRecord,
    ChannelDeliveryPartRecord,
    ChannelDeliveryPlanRecord,
    ChannelTurnRecord,
)
from chatwaifu_runtime.external_channels.ports import ExternalChannelRepository
from chatwaifu_runtime.external_channels.presentation import (
    group_text_parts_match_reply,
    render_bubble_text,
)
from chatwaifu_runtime.external_channels.scheduler import (
    DeliveryPartExecutionResult,
    DeliveryPartOutcome,
)
from chatwaifu_runtime.external_channels.stickers import MAX_STICKER_BYTES, PresetStickerCatalog
from chatwaifu_runtime.sticker_library.service import StickerLibraryService

from .client import NapCatClient, NapCatRejected
from .expressions import FAVORITE_JOURNAL_PREFIX, native_text_segments, send_journal_size

_IMAGE_FORMATS = {"image/png": "PNG", "image/jpeg": "JPEG"}
_MAX_IMAGE_DIMENSION = 8192
_MAX_IMAGE_PIXELS = 16_777_216
logger = logging.getLogger(__name__)


async def reconcile_known_sends(
    repository: ExternalChannelRepository,
    connection_id: UUID,
    publisher: EventPublisher,
    on_terminal: Callable[[ChannelDeliveryPlanRecord], Awaitable[None]],
    *,
    journal_lock: asyncio.Lock,
) -> None:
    """Import trusted send success without policy, lease, login or another send."""
    async with journal_lock:
        cursor = await repository.get_adapter_cursor(connection_id)
        if not cursor:
            return
        try:
            parsed: object = json.loads(cursor)
        except ValueError:
            logger.warning("QQ send journal cannot be decoded; checkpoint retained")
            return
        if not isinstance(parsed, dict):
            return
        raw = cast(dict[object, object], parsed)
        if len(raw) > 356 or any(
            not isinstance(k, str) or not isinstance(v, str) for k, v in raw.items()
        ):
            logger.warning("QQ send journal shape rejected; checkpoint retained")
            return
        journal = cast(dict[str, str], raw)
        if send_journal_size(journal) > 256 or len(journal) - send_journal_size(journal) > 100:
            logger.warning("QQ send journal capacity rejected; checkpoint retained")
            return
        conflicts: set[str] = set()
        for key, receipt in journal.items():
            if key.startswith(FAVORITE_JOURNAL_PREFIX):
                continue
            if receipt == "unknown":
                continue
            if not re.fullmatch(r"-?[0-9]{1,20}", receipt):
                conflicts.add(key)
                continue
            try:
                transition = await repository.reconcile_known_delivery_part_receipt(
                    connection_id, key, receipt, observed_at=datetime.now(UTC)
                )
            except (KeyError, ValueError):
                conflicts.add(key)
                continue
            try:
                for event in transition.persisted_events:
                    await publisher.publish_persisted(event)
            finally:
                if transition.plan.status in {
                    ChannelDeliveryStatus.DELIVERED,
                    ChannelDeliveryStatus.FAILED,
                    ChannelDeliveryStatus.CANCELLED,
                }:
                    await on_terminal(transition.plan)
        send_keys = tuple(key for key in journal if not key.startswith(FAVORITE_JOURNAL_PREFIX))
        retained = await repository.retained_send_journal_keys(connection_id, send_keys)
        kept = {
            key: receipt
            for key, receipt in journal.items()
            if key.startswith(FAVORITE_JOURNAL_PREFIX)
            or key in retained
            or key in conflicts
            or receipt == "unknown"
        }
        if kept != journal:
            await repository.set_adapter_cursor(
                connection_id, cursor=json.dumps(kept), updated_at=datetime.now(UTC)
            )


class NapCatDelivery:
    def __init__(
        self,
        repository: ExternalChannelRepository,
        client: NapCatClient,
        connection_id: UUID,
        owner: str,
        audio_root: Path,
        *,
        sticker_catalog: PresetStickerCatalog | None = None,
        sticker_library: StickerLibraryService | None = None,
        proactive_authorization: Callable[[ChannelDeliveryPlanRecord], Awaitable[bool]]
        | None = None,
        group_authorization: Callable[[ChannelDeliveryPlanRecord], Awaitable[bool]] | None = None,
        journal_lock: asyncio.Lock | None = None,
    ) -> None:
        self._repository = repository
        self._client = client
        self._connection_id = connection_id
        self._owner = owner
        self._audio_root = audio_root
        self._sticker_catalog = sticker_catalog
        self._sticker_library = sticker_library
        self._proactive_authorization = proactive_authorization
        self._group_authorization = group_authorization
        self._journal_lock = journal_lock or asyncio.Lock()

    async def execute_part(
        self, plan: ChannelDeliveryPlanRecord, part: ChannelDeliveryPartRecord
    ) -> DeliveryPartExecutionResult:
        async with self._journal_lock:
            return await self._execute_part(plan, part)

    async def _execute_part(
        self, plan: ChannelDeliveryPlanRecord, part: ChannelDeliveryPartRecord
    ) -> DeliveryPartExecutionResult:
        # The scheduler serializes one executor per connection. Cursor contains no secrets.
        cursor = await self._repository.get_adapter_cursor(self._connection_id)
        journal: dict[str, str] = json.loads(cursor) if cursor else {}
        key = part.provider_client_id
        known = journal.get(key)
        if known and known != "unknown":
            return DeliveryPartExecutionResult(
                DeliveryPartOutcome.DELIVERED, provider_message_id=known
            )
        if known == "unknown":
            return _failed("qq_delivery_unknown", "发送结果待确认，为避免重复消息，未自动重发。")
        if plan.group_target is not None:
            return await self._execute_group(plan, part, journal)
        if plan.channel_turn_id is not None:
            source = await self._repository.get_turn(plan.channel_turn_id)
            if source is not None and source.chat_type is ChannelChatType.GROUP:
                # Historical group rows have no admitted fixed target. Never
                # reinterpret them as owner-private delivery.
                return _failed("qq_group_delivery_cancelled", "群文字投递已停止。")
        if not await self._authorized(plan):
            return _failed("qq_proactive_cancelled", "主动文字投递已停止。")
        connection = await self._repository.get_connection(self._connection_id)
        current = await self._repository.get_delivery_plan(plan.delivery_id)
        if (
            connection is None
            or not connection.configuration.enabled
            or connection.configuration.allowed_sender_keys != [self._owner]
            or current is None
            or current.cancel_requested_at is not None
        ):
            return _failed("qq_delivery_cancelled", "QQ 投递已停止。")
        segments: list[JsonObject]
        try:
            if isinstance(part.payload, ChannelTextDeliveryPartPayload):
                segments = native_text_segments(self._render_text(plan, part))
            elif isinstance(part.payload, ChannelAudioDeliveryPartPayload):
                path = self._audio_root / f"{part.payload.asset_id}.wav"
                if path.is_symlink() or not path.is_file() or path.stat().st_size > 8 * 1024 * 1024:
                    return _failed("qq_audio_unavailable", "语音文件不可用，请重新请求。")
                audio = await asyncio.to_thread(path.read_bytes)
                if hashlib.sha256(audio).hexdigest() != part.payload.sha256:
                    return _failed("qq_audio_changed", "语音文件校验失败。")
                segments = [
                    {
                        "type": "record",
                        "data": {"file": "base64://" + base64.b64encode(audio).decode("ascii")},
                    }
                ]
            else:
                configuration = connection.configuration
                if configuration.character_id != "default":
                    return _failed("qq_image_character_unsupported", "当前角色不支持表情图片。")
                if part.payload.mime_type not in _IMAGE_FORMATS:
                    return _failed("qq_image_invalid", "表情图片格式不受支持。")
                try:
                    if part.payload.sticker_id.startswith("learned_"):
                        image = (
                            await self._sticker_library.image_for_delivery(
                                configuration.principal_scope,
                                configuration.character_id,
                                part.payload,
                            )
                            if self._sticker_library is not None
                            else None
                        )
                    else:
                        image = await asyncio.to_thread(self._preset_image, part.payload)
                except Exception:
                    return _failed("qq_image_unavailable", "表情图片不可用。")
                if image is None:
                    return _failed("qq_image_unavailable", "表情图片不可用或已变更。")
                if hashlib.sha256(image).hexdigest() != part.payload.sha256:
                    return _failed("qq_image_changed", "表情图片校验失败。")
                if not await asyncio.to_thread(_valid_image, image, part.payload.mime_type):
                    return _failed("qq_image_invalid", "表情图片内容或大小不受支持。")
                segments = [
                    {
                        "type": "image",
                        "data": {
                            "file": "base64://" + base64.b64encode(image).decode("ascii"),
                            "sub_type": 1,
                        },
                    }
                ]
            if part.ordinal == 0 and plan.delivery.channel_turn_id is not None:
                reply_target = await self._repository.quoted_reply_target(
                    plan.delivery.channel_turn_id
                )
                if reply_target is not None:
                    if not re.fullmatch(r"-?[0-9]{1,20}", reply_target):
                        return _failed("qq_reply_invalid", "QQ 引用标识不可用。")
                    segments = [{"type": "reply", "data": {"id": reply_target}}, *segments]
            # echo only correlates RPCs. A persisted fence prevents restart replay.
            # Receipt reconciliation owns garbage collection: a delivered
            # part alone cannot prove a cursor's different receipt is resolved.
            if send_journal_size(journal) >= 256:
                return _failed("qq_send_journal_full", "请处理未完成的 QQ 投递后重试。")
            # File reads can overlap cancellation or disabling the connection.
            connection = await self._repository.get_connection(self._connection_id)
            current = await self._repository.get_delivery_plan(plan.delivery_id)
            if (
                connection is None
                or not connection.configuration.enabled
                or connection.configuration.allowed_sender_keys != [self._owner]
                or current is None
                or current.cancel_requested_at is not None
            ):
                return _failed("qq_delivery_cancelled", "QQ 投递已停止。")
            if not await self._authorized(plan):
                return _failed("qq_proactive_cancelled", "主动文字投递已停止。")
            journal[key] = "unknown"
            await self._save(journal)
            try:

                async def before_send() -> bool:
                    connection = await self._repository.get_connection(self._connection_id)
                    current = await self._repository.get_delivery_plan(plan.delivery_id)
                    return (
                        connection is not None
                        and connection.configuration.enabled
                        and connection.configuration.allowed_sender_keys == [self._owner]
                        and current is not None
                        and current.cancel_requested_at is None
                        and await self._authorized(plan)
                    )

                message_id = await self._client.send(self._owner, segments, before_send=before_send)
            except NapCatRejected:
                journal.pop(key, None)
                await self._save(journal)
                return _failed("qq_send_rejected", "QQ 拒绝了这条消息。")
            journal[key] = message_id
            await self._save(journal)
            return DeliveryPartExecutionResult(
                DeliveryPartOutcome.DELIVERED, provider_message_id=message_id
            )
        except Exception:
            return _failed("qq_delivery_unknown", "发送结果待确认，未自动重发。")

    async def _execute_group(
        self,
        plan: ChannelDeliveryPlanRecord,
        part: ChannelDeliveryPartRecord,
        journal: dict[str, str],
    ) -> DeliveryPartExecutionResult:
        """A fixed group path with canonical text or one authorized voice reply."""
        target = plan.group_target
        assert target is not None
        key = part.provider_client_id
        segments: list[JsonObject]
        try:
            if not await self._group_allowed(plan, part):
                return _failed("qq_group_delivery_cancelled", "群文字投递已停止。")
            if send_journal_size(journal) >= 256:
                return _failed("qq_send_journal_full", "请处理未完成的 QQ 投递后重试。")
            if isinstance(part.payload, ChannelTextDeliveryPartPayload):
                segments = native_text_segments(self._render_text(plan, part))
            elif isinstance(part.payload, ChannelAudioDeliveryPartPayload):
                path = self._audio_root / f"{part.payload.asset_id}.wav"
                if path.is_symlink() or not path.is_file() or path.stat().st_size > 8 * 1024 * 1024:
                    return _failed("qq_audio_unavailable", "群语音文件不可用，请重新请求。")
                audio = await asyncio.to_thread(path.read_bytes)
                if hashlib.sha256(audio).hexdigest() != part.payload.sha256:
                    return _failed("qq_audio_changed", "群语音文件校验失败。")
                segments = [
                    {
                        "type": "record",
                        "data": {"file": "base64://" + base64.b64encode(audio).decode("ascii")},
                    }
                ]
            else:
                if self._sticker_library is None:
                    return _failed("qq_image_unavailable", "群表情图片不可用。")
                image = await self._sticker_library.image_for_delivery(
                    f"scene:{target.scene_id}", "default", part.payload
                )
                if image is None or not await asyncio.to_thread(
                    _valid_image, image, part.payload.mime_type
                ):
                    return _failed("qq_image_unavailable", "群表情图片不可用或已变更。")
                segments = [
                    {
                        "type": "image",
                        "data": {
                            "file": "base64://" + base64.b64encode(image).decode("ascii"),
                            "sub_type": 1,
                        },
                    }
                ]
                if not await self._group_allowed(plan, part):
                    return _failed("qq_group_delivery_cancelled", "群表情投递已停止。")
            journal[key] = "unknown"
            await self._save(journal)
            try:
                if isinstance(part.payload, ChannelAudioDeliveryPartPayload):
                    message_id = await self._client.send_group(
                        target.group_id,
                        segments,
                        allow_voice=True,
                        before_send=lambda: self._group_allowed(plan, part),
                    )
                else:
                    message_id = await self._client.send_group(
                        target.group_id,
                        segments,
                        before_send=lambda: self._group_allowed(plan, part),
                    )
            except NapCatRejected:
                journal.pop(key, None)
                await self._save(journal)
                return _failed("qq_send_rejected", "QQ 拒绝了这条消息。")
            journal[key] = message_id
            await self._save(journal)
            return DeliveryPartExecutionResult(
                DeliveryPartOutcome.DELIVERED, provider_message_id=message_id
            )
        except Exception:
            return _failed("qq_delivery_unknown", "发送结果待确认，未自动重发。")

    async def _group_allowed(
        self, plan: ChannelDeliveryPlanRecord, part: ChannelDeliveryPartRecord
    ) -> bool:
        if (
            self._group_authorization is None
            or plan.channel_turn_id is None
            or not self._client.group_dispatch_ready
        ):
            return False
        if not await self._group_context_matches(plan, part):
            return False
        if not await self._group_authorization(plan):
            return False
        # The host guard can await storage. Check immutable target, lease and
        # cancellation again after it returns, including after login preflight.
        if not await self._group_context_matches(plan, part):
            return False
        # Conversely, those storage awaits can overlap route revocation. The
        # final await must consult host authority, not just historical fields.
        return await self._group_authorization(plan)

    async def _group_context_matches(
        self, plan: ChannelDeliveryPlanRecord, part: ChannelDeliveryPartRecord
    ) -> bool:
        connection = await self._repository.get_connection(self._connection_id)
        current = await self._repository.get_delivery_plan(plan.delivery_id)
        source = (
            await self._repository.get_turn(plan.channel_turn_id)
            if plan.channel_turn_id is not None
            else None
        )
        return self._group_records_match(plan, part, connection, current, source)

    def _group_records_match(
        self,
        plan: ChannelDeliveryPlanRecord,
        part: ChannelDeliveryPartRecord,
        connection: ChannelConnectionRecord | None,
        current: ChannelDeliveryPlanRecord | None,
        source: ChannelTurnRecord | None,
    ) -> bool:
        target = plan.group_target
        if (
            target is None
            or connection is None
            or current is None
            or source is None
            or connection.deleted_at is not None
            or not connection.configuration.enabled
            or connection.configuration.provider_id != "qq_napcat"
            or connection.configuration.connection_id != self._connection_id
            or connection.configuration.account_key != target.account_key
            or self._client.bound_account != target.account_key
            or not self._client.group_dispatch_ready
            or plan.connection_id != self._connection_id
            or target.connection_id != self._connection_id
            or target.channel_turn_id != plan.channel_turn_id
            or plan.outbound_intent_id is not None
            or current.delivery_id != plan.delivery_id
            or current.connection_id != self._connection_id
            or current.channel_turn_id != plan.channel_turn_id
            or current.outbound_intent_id is not None
            or current.group_target != target
            or current.plan_version != plan.plan_version
            or current.cancel_requested_at is not None
            or current.status is not ChannelDeliveryStatus.SENDING
            or source.channel_turn_id != target.channel_turn_id
            or source.connection_id != self._connection_id
            or source.chat_type is not ChannelChatType.GROUP
            or (
                source.status is not ChannelTurnStatus.COMPLETED
                and not (
                    source.status is ChannelTurnStatus.PROCESSING
                    and len(plan.parts) == 1
                    and isinstance(plan.parts[0].payload, ChannelAudioDeliveryPartPayload)
                )
            )
            or source.input_kind not in {ChannelMessageKind.TEXT, ChannelMessageKind.IMAGE}
            or source.account_key != target.account_key
            or source.conversation_key != f"group:{target.group_id}"
            or source.principal_scope != f"scene:{target.scene_id}"
            or source.group_route_id != target.route_id
            or source.group_route_revision != target.route_revision
            or source.group_lineage_version != 1
            or plan.delivery.part_count != len(plan.parts)
            or current.delivery.part_count != len(current.parts)
            or len(plan.parts) != len(current.parts)
            or not group_text_parts_match_reply(
                plan.parts, source.reply_text or "", allow_sticker=True, allow_voice=True
            )
            or not group_text_parts_match_reply(
                current.parts, source.reply_text or "", allow_sticker=True, allow_voice=True
            )
            or not 0 <= part.ordinal < len(current.parts)
        ):
            return False
        for original_part, persisted_part in zip(plan.parts, current.parts, strict=True):
            if (
                original_part.delivery_id != plan.delivery_id
                or persisted_part.delivery_id != plan.delivery_id
                or original_part.part_id != persisted_part.part_id
                or original_part.provider_client_id != persisted_part.provider_client_id
                or original_part.payload != persisted_part.payload
                or original_part.delay_after_ms != persisted_part.delay_after_ms
            ):
                return False
        if any(
            previous.required and previous.status is not ChannelDeliveryPartStatus.DELIVERED
            for previous in current.parts[: part.ordinal]
        ):
            return False
        original, claimed = plan.parts[part.ordinal], current.parts[part.ordinal]
        return (
            part.delivery_id == original.delivery_id == claimed.delivery_id == plan.delivery_id
            and part.part_id == original.part_id == claimed.part_id
            and part.provider_client_id == original.provider_client_id == claimed.provider_client_id
            and part.payload == original.payload == claimed.payload
            and part.kind is original.kind is claimed.kind
            and (
                (
                    isinstance(part.payload, ChannelTextDeliveryPartPayload)
                    and bool(part.payload.text.strip())
                    and part.required
                    and original.required
                    and claimed.required
                )
                or (
                    isinstance(part.payload, ChannelAudioDeliveryPartPayload)
                    and part.payload.mime_type == "audio/wav"
                    and bool(part.payload.text.strip())
                    and part.required
                    and original.required
                    and claimed.required
                )
                or (
                    isinstance(part.payload, ChannelImageDeliveryPartPayload)
                    and part.payload.sticker_id.startswith("learned_")
                    and not part.required
                    and not original.required
                    and not claimed.required
                )
            )
            and part.ordinal == original.ordinal == claimed.ordinal
            and part.status is claimed.status is ChannelDeliveryPartStatus.SENDING
            and part.lease_id is not None
            and part.lease_id == claimed.lease_id
            and part.attempt == claimed.attempt
            and claimed.lease_expires_at is not None
            and claimed.lease_expires_at > datetime.now(UTC)
        )

    @staticmethod
    def _render_text(plan: ChannelDeliveryPlanRecord, part: ChannelDeliveryPartRecord) -> str:
        assert isinstance(part.payload, ChannelTextDeliveryPartPayload)
        return render_bubble_text(
            part.payload.text,
            has_following_text_part=any(
                following.kind is ChannelDeliveryPartKind.TEXT
                for following in plan.parts[part.ordinal + 1 :]
            ),
        )

    async def _save(self, journal: dict[str, str]) -> None:
        await self._repository.set_adapter_cursor(
            self._connection_id, cursor=json.dumps(journal), updated_at=datetime.now(UTC)
        )

    async def _authorized(self, plan: ChannelDeliveryPlanRecord) -> bool:
        if plan.outbound_intent_id is None:
            return True
        return self._proactive_authorization is not None and await self._proactive_authorization(
            plan
        )

    def _preset_image(self, payload: ChannelImageDeliveryPartPayload) -> bytes | None:
        if self._sticker_catalog is None:
            return None
        entry = next(
            (
                item
                for item in self._sticker_catalog.load_manifest()
                if item.sticker_id == payload.sticker_id
                and item.sha256 == payload.sha256
                and item.mime_type == payload.mime_type
            ),
            None,
        )
        if entry is None:
            return None
        return self._sticker_catalog.load_sticker_bytes(payload.sticker_id, payload.sha256)


def _valid_image(data: bytes, mime_type: str) -> bool:
    """Decode bounded static images after immutable catalog/library resolution."""
    if not data or len(data) > MAX_STICKER_BYTES:
        return False
    try:
        with Image.open(io.BytesIO(data)) as image:
            if (
                image.format != _IMAGE_FORMATS.get(mime_type)
                or getattr(image, "n_frames", 1) != 1
                or getattr(image, "is_animated", False)
                or not 0 < image.width <= _MAX_IMAGE_DIMENSION
                or not 0 < image.height <= _MAX_IMAGE_DIMENSION
                or image.width * image.height > _MAX_IMAGE_PIXELS
            ):
                return False
            image.verify()
        # JPEG verify() alone does not decode its pixels or reject truncated scans.
        with Image.open(io.BytesIO(data)) as decoded:
            decoded.load()
    except Exception:
        return False
    return True


def _failed(code: str, message: str) -> DeliveryPartExecutionResult:
    return DeliveryPartExecutionResult(
        DeliveryPartOutcome.FATAL_ERROR,
        error=StructuredError(
            code=code,
            message=message,
            component="external_channels.qq",
            retryable=False,
        ),
    )
