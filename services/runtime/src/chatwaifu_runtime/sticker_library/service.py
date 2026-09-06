"""Bounded, opt-in image learning and scoped sticker reuse."""

from __future__ import annotations

import asyncio
import hashlib
import logging
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from uuid import UUID

from chatwaifu_protocol.channels import ChannelImageDeliveryPartPayload
from chatwaifu_protocol.character import ResponsePlan

from chatwaifu_runtime.media import InboundMediaItem
from chatwaifu_runtime.media.image import (
    async_normalize_animated_sticker,
    normalize_animated_sticker,
)
from chatwaifu_runtime.providers.contracts import LlmInputImage
from chatwaifu_runtime.sticker_library.classifier import StickerClassifier
from chatwaifu_runtime.sticker_library.models import StickerSaveCandidate
from chatwaifu_runtime.sticker_library.ports import StickerLibraryRepository
from chatwaifu_runtime.sticker_library.selection import StickerSelectionHints, matches_interaction

logger = logging.getLogger(__name__)
MAX_PENDING_IMAGES = 2
MAX_LEARNING_SECONDS = 45

__all__ = [
    "MAX_LEARNING_SECONDS",
    "MAX_PENDING_IMAGES",
    "StickerLearningSource",
    "StickerLibraryService",
    "_normalize_sticker",
]


@dataclass(frozen=True, slots=True)
class StickerLearningSource:
    principal_scope: str
    character_id: str
    connection_id: UUID
    generation_id: UUID


class StickerLibraryService:
    def __init__(self, repository: StickerLibraryRepository, classifier: StickerClassifier) -> None:
        self.repository = repository
        self._classifier = classifier
        self._tasks: dict[UUID, tuple[UUID, asyncio.Task[None]]] = {}
        self._stopping = False

    def start(self) -> None:
        self._stopping = False

    async def stop(self) -> None:
        self._stopping = True
        tasks = [task for _, task in self._tasks.values()]
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self._tasks.clear()

    async def cancel_generation(self, generation_id: UUID) -> None:
        entry = self._tasks.get(generation_id)
        if entry is not None:
            entry[1].cancel()
            await asyncio.gather(entry[1], return_exceptions=True)

    async def cancel_connection(self, connection_id: UUID) -> None:
        tasks = [task for conn, task in self._tasks.values() if conn == connection_id]
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    async def observe_batch(
        self,
        source: StickerLearningSource,
        images: Sequence[LlmInputImage | InboundMediaItem],
        *,
        wait_for_completion: Callable[[], Awaitable[bool]],
    ) -> None:
        if self._stopping or source.generation_id in self._tasks or not images:
            return
        settings = await self.repository.get_settings(source.principal_scope, source.character_id)
        # No image bytes are saved or classified when learning is disabled.
        if (
            not settings.learning_enabled
            or self._stopping
            or source.generation_id in self._tasks
            or len(self._tasks) >= MAX_PENDING_IMAGES
        ):
            return
        if len(images) > 4:
            return
        batch = tuple(images)
        task = asyncio.create_task(
            self._learn_batch(source, batch, settings.revision, wait_for_completion),
            name=f"sticker-learning-{source.generation_id}",
        )
        self._tasks[source.generation_id] = (source.connection_id, task)
        task.add_done_callback(lambda _: self._tasks.pop(source.generation_id, None))

    async def observe(
        self,
        source: StickerLearningSource,
        image: LlmInputImage | InboundMediaItem,
        *,
        wait_for_completion: Callable[[], Awaitable[bool]],
    ) -> None:
        await self.observe_batch(source, (image,), wait_for_completion=wait_for_completion)

    async def _learn_batch(
        self,
        source: StickerLearningSource,
        images: tuple[LlmInputImage | InboundMediaItem, ...],
        revision: int,
        wait_for_completion: Callable[[], Awaitable[bool]],
    ) -> None:
        try:
            total_budget = len(images) * MAX_LEARNING_SECONDS
            async with asyncio.timeout(total_budget):
                for image in images:
                    if self._stopping:
                        return
                    try:
                        async with asyncio.timeout(MAX_LEARNING_SECONDS):
                            await self._learn(source, image, revision, wait_for_completion)
                    except asyncio.CancelledError:
                        raise
                    except Exception:
                        logger.warning(
                            "sticker learning failed for image in batch generation_id=%s",
                            source.generation_id,
                        )
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.warning("sticker learning batch skipped generation_id=%s", source.generation_id)

    async def _learn(
        self,
        source: StickerLearningSource,
        image: LlmInputImage | InboundMediaItem,
        revision: int,
        wait_for_completion: Callable[[], Awaitable[bool]],
    ) -> None:
        try:
            async with asyncio.timeout(MAX_LEARNING_SECONDS):
                classifier_image = (
                    image.raster_image if isinstance(image, InboundMediaItem) else image
                )
                classification = await self._classifier.classify(
                    classifier_image, generation_id=source.generation_id
                )
                if classification is None or not await wait_for_completion():
                    return
                # Re-encode only accepted stickers; strip source metadata and bound library size.
                if isinstance(image, InboundMediaItem):
                    data, mime_type, is_animated = await async_normalize_animated_sticker(
                        image.raw_data, image.original_mime_type
                    )
                else:
                    data, mime_type, is_animated = await async_normalize_animated_sticker(
                        image.data, image.mime_type
                    )
                record = await self.repository.save(
                    source.principal_scope,
                    source.character_id,
                    StickerSaveCandidate(
                        data=data,
                        label=classification.label.strip(),
                        description=classification.description.strip(),
                        expression=classification.expression,
                        source_connection_id=source.connection_id,
                        generation_id=source.generation_id,
                        mime_type=mime_type,
                        is_animated=is_animated,
                    ),
                    expected_revision=revision,
                )
                logger.info(
                    "sticker learning completed generation_id=%s saved=%s",
                    source.generation_id,
                    record is not None,
                )
        except asyncio.CancelledError:
            raise
        except Exception:
            # Learning is optional and must never turn a successful conversation into a failure.
            logger.warning("sticker learning skipped generation_id=%s", source.generation_id)

    async def match(
        self,
        principal_scope: str,
        character_id: str,
        plan: ResponsePlan | None,
        *,
        hints: StickerSelectionHints | None = None,
    ) -> ChannelImageDeliveryPartPayload | None:
        hints = hints or StickerSelectionHints()
        if hints.blocked:
            return None
        if hints.interaction is None and (
            plan is None or plan.intent == "answer" or plan.expression == "neutral"
        ):
            return None
        snapshot = await self.repository.snapshot(principal_scope, character_id)
        for item in snapshot.items:
            related = (
                matches_interaction(item.label, item.description, hints)
                if hints.interaction is not None
                else plan is not None and item.expression == plan.expression
            )
            if related:
                return ChannelImageDeliveryPartPayload(
                    sticker_id=item.sticker_id, sha256=item.sha256, mime_type=item.mime_type
                )
        return None

    async def image_for_delivery(
        self, principal_scope: str, character_id: str, payload: ChannelImageDeliveryPartPayload
    ) -> bytes | None:
        data = await self.repository.get_image(
            principal_scope, character_id, payload.sticker_id, expected_sha256=payload.sha256
        )
        if data is None or hashlib.sha256(data).hexdigest() != payload.sha256:
            return None
        return data


def _normalize_sticker(image: LlmInputImage) -> bytes:
    data, _, _ = normalize_animated_sticker(image.data, image.mime_type)
    return data
