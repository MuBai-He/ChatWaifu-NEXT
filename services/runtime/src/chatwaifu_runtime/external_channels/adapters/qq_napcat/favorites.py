"""Optional native favorite mirroring after a scoped CW2 sticker save."""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import cast
from uuid import UUID

from chatwaifu_protocol.channels import ChannelImageDeliveryPartPayload
from chatwaifu_protocol.sticker_library import LearnedSticker

from chatwaifu_runtime.external_channels.ports import ExternalChannelRepository
from chatwaifu_runtime.sticker_library.models import StickerLearningSource
from chatwaifu_runtime.sticker_library.service import StickerLibraryService

from .client import NapCatClient, NapCatError
from .expressions import FAVORITE_JOURNAL_PREFIX

logger = logging.getLogger(__name__)


class NapCatStickerFavorites:
    def __init__(
        self,
        repository: ExternalChannelRepository,
        library: StickerLibraryService,
        client: NapCatClient,
        connection_id: UUID,
        *,
        journal_lock: asyncio.Lock,
        enabled: Callable[[], bool] = lambda: True,
        group_authorization: Callable[[StickerLearningSource], Awaitable[bool]] | None = None,
    ) -> None:
        self._repository = repository
        self._library = library
        self._client = client
        self._connection_id = connection_id
        self._journal_lock = journal_lock
        self._group_authorization = group_authorization
        self._enabled = enabled
        self._lock = asyncio.Lock()

    async def _allowed(self, source: StickerLearningSource, sticker: LearnedSticker) -> bool:
        connection = await self._repository.get_connection(self._connection_id)
        if (
            not self._enabled()
            or connection is None
            or connection.deleted_at is not None
            or not connection.configuration.enabled
            or source.connection_id != self._connection_id
            or connection.configuration.provider_id != "qq_napcat"
            or connection.configuration.account_key != self._client.bound_account
            or connection.configuration.character_id != source.character_id
            or sticker.source_connection_id != self._connection_id
            or not connection.configuration.presentation_policy
            or not connection.configuration.presentation_policy.stickers_enabled
        ):
            return False
        if source.group_target is not None:
            if self._group_authorization is None or not await self._group_authorization(source):
                return False
        elif connection.configuration.principal_scope != source.principal_scope:
            return False
        settings = await self._library.repository.get_settings(
            source.principal_scope, source.character_id
        )
        return (
            self._enabled()
            and settings.learning_enabled
            and settings.revision == source.settings_revision
        )

    async def _journal(self) -> dict[str, str]:
        raw = await self._repository.get_adapter_cursor(self._connection_id)
        parsed: object = json.loads(raw) if raw else {}
        if not isinstance(parsed, dict):
            raise NapCatError("QQ favorite checkpoint unavailable")
        entries = cast(dict[object, object], parsed)
        if len(entries) > 356 or any(
            not isinstance(k, str) or not isinstance(v, str) for k, v in entries.items()
        ):
            raise NapCatError("QQ favorite checkpoint unavailable")
        return cast(dict[str, str], parsed)

    async def _checkpoint(self, key: str, state: str) -> None:
        # Share only the read/write critical section with sends; provider I/O stays outside it.
        async with self._journal_lock:
            journal = await self._journal()
            if (
                key not in journal
                and sum(k.startswith(FAVORITE_JOURNAL_PREFIX) for k in journal) >= 100
            ):
                raise NapCatError("QQ native favorite checkpoint capacity reached")
            journal[key] = state
            await self._repository.set_adapter_cursor(
                self._connection_id, cursor=json.dumps(journal), updated_at=datetime.now(UTC)
            )

    async def observe_saved(self, source: StickerLearningSource, sticker: LearnedSticker) -> None:
        try:
            async with self._lock:
                if not await self._allowed(source, sticker):
                    return
                key = FAVORITE_JOURNAL_PREFIX + sticker.sha256
                async with self._journal_lock:
                    journal = await self._journal()
                    # Both confirmed and uncertain results are retained; neither is replayed.
                    if key in journal:
                        return
                    if sum(k.startswith(FAVORITE_JOURNAL_PREFIX) for k in journal) >= 100:
                        return
                image = await self._library.image_for_delivery(
                    source.principal_scope,
                    source.character_id,
                    ChannelImageDeliveryPartPayload(
                        sticker_id=sticker.sticker_id,
                        sha256=sticker.sha256,
                        mime_type=sticker.mime_type,
                    ),
                )
                if image is None:
                    return
                md5 = hashlib.md5(image, usedforsecurity=False).hexdigest()
                if md5 in await self._client.favorite_hashes():
                    await self._checkpoint(key, "confirmed")
                    return
                if not await self._allowed(source, sticker):
                    return
                confirmed = await self._client.add_sticker_favorite(
                    image,
                    before_add=lambda: self._allowed(source, sticker),
                    checkpoint=lambda: self._checkpoint(key, "unknown"),
                )
                if confirmed:
                    await self._checkpoint(key, "confirmed")
                logger.info(
                    "QQ native sticker favorite connection_id=%s confirmed=%s",
                    self._connection_id,
                    confirmed,
                )
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.warning(
                "QQ native sticker favorite skipped connection_id=%s", self._connection_id
            )
