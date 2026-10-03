"""OneBot delivery with a durable send fence for transports without idempotency keys."""

from __future__ import annotations

import asyncio
import base64
import hashlib
import io
import json
import re
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

from chatwaifu_protocol.base import JsonObject
from chatwaifu_protocol.channels import (
    ChannelAudioDeliveryPartPayload,
    ChannelImageDeliveryPartPayload,
    ChannelTextDeliveryPartPayload,
)
from chatwaifu_protocol.errors import StructuredError
from PIL import Image

from chatwaifu_runtime.external_channels.models import (
    ChannelDeliveryPartRecord,
    ChannelDeliveryPlanRecord,
)
from chatwaifu_runtime.external_channels.ports import ExternalChannelRepository
from chatwaifu_runtime.external_channels.scheduler import (
    DeliveryPartExecutionResult,
    DeliveryPartOutcome,
)
from chatwaifu_runtime.external_channels.stickers import MAX_STICKER_BYTES, PresetStickerCatalog
from chatwaifu_runtime.sticker_library.service import StickerLibraryService

from .client import NapCatClient, NapCatRejected

_IMAGE_FORMATS = {"image/png": "PNG", "image/jpeg": "JPEG"}
_MAX_IMAGE_DIMENSION = 8192
_MAX_IMAGE_PIXELS = 16_777_216


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
    ) -> None:
        self._repository = repository
        self._client = client
        self._connection_id = connection_id
        self._owner = owner
        self._audio_root = audio_root
        self._sticker_catalog = sticker_catalog
        self._sticker_library = sticker_library

    async def execute_part(
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
        connection = await self._repository.get_connection(self._connection_id)
        current = await self._repository.get_delivery_plan(plan.delivery_id)
        if (
            connection is None
            or not connection.configuration.enabled
            or current is None
            or current.cancel_requested_at is not None
        ):
            return _failed("qq_delivery_cancelled", "QQ 投递已停止。")
        try:
            if isinstance(part.payload, ChannelTextDeliveryPartPayload):
                segments: list[JsonObject] = [{"type": "text", "data": {"text": part.payload.text}}]
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
                        "data": {"file": "base64://" + base64.b64encode(image).decode("ascii")},
                    }
                ]
            if part.ordinal == 0:
                reply_target = await self._repository.quoted_reply_target(
                    plan.delivery.channel_turn_id
                )
                if reply_target is not None:
                    if not re.fullmatch(r"-?[0-9]{1,20}", reply_target):
                        return _failed("qq_reply_invalid", "QQ 引用标识不可用。")
                    segments = [{"type": "reply", "data": {"id": reply_target}}, *segments]
            # echo only correlates RPCs. A persisted fence prevents restart replay.
            if len(journal) >= 128:
                active = await self._repository.list_nonterminal_delivery_plans(
                    connection_id=self._connection_id, limit=10000
                )
                keep = {p.provider_client_id for item in active for p in item.parts}
                journal = {k: v for k, v in journal.items() if k in keep}
            if len(journal) >= 256:
                return _failed("qq_send_journal_full", "请处理未完成的 QQ 投递后重试。")
            # File reads can overlap cancellation or disabling the connection.
            connection = await self._repository.get_connection(self._connection_id)
            current = await self._repository.get_delivery_plan(plan.delivery_id)
            if (
                connection is None
                or not connection.configuration.enabled
                or current is None
                or current.cancel_requested_at is not None
            ):
                return _failed("qq_delivery_cancelled", "QQ 投递已停止。")
            journal[key] = "unknown"
            await self._save(journal)
            try:

                async def before_send() -> bool:
                    connection = await self._repository.get_connection(self._connection_id)
                    current = await self._repository.get_delivery_plan(plan.delivery_id)
                    return (
                        connection is not None
                        and connection.configuration.enabled
                        and current is not None
                        and current.cancel_requested_at is None
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

    async def _save(self, journal: dict[str, str]) -> None:
        await self._repository.set_adapter_cursor(
            self._connection_id, cursor=json.dumps(journal), updated_at=datetime.now(UTC)
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
