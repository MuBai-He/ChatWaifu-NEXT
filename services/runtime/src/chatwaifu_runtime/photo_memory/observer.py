"""Bounded, opt-in photo memory observation."""

from __future__ import annotations

import asyncio
import io
import logging
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from typing import Literal
from uuid import UUID

from chatwaifu_protocol.photo_memory import SavedPhoto
from PIL import Image, ImageOps

from chatwaifu_runtime.media import InboundMediaItem
from chatwaifu_runtime.photo_memory.annotations import PhotoAnnotationService
from chatwaifu_runtime.photo_memory.classifier import PhotoClassifier
from chatwaifu_runtime.photo_memory.metadata import extract_photo_metadata
from chatwaifu_runtime.photo_memory.models import PhotoItemOrigin, PhotoSaveCandidate
from chatwaifu_runtime.photo_memory.ports import PhotoMemoryRepository
from chatwaifu_runtime.photo_memory.semantic import PhotoSemanticService
from chatwaifu_runtime.providers.contracts import LlmInputImage

logger = logging.getLogger(__name__)
MAX_PENDING_IMAGES = 2
MAX_LEARNING_SECONDS = 45


@dataclass(frozen=True, slots=True)
class PhotoObservationSource:
    principal_scope: str
    character_id: str
    connection_id: UUID
    generation_id: UUID


class PhotoMemoryObserver:
    def __init__(
        self,
        repository: PhotoMemoryRepository,
        classifier: PhotoClassifier,
        semantic_service: PhotoSemanticService | None = None,
        annotations: PhotoAnnotationService | None = None,
    ) -> None:
        self._annotations = annotations
        self.repository = repository
        self._classifier = classifier
        self._semantic_service = semantic_service
        self._tasks: dict[UUID, tuple[UUID, asyncio.Task[None]]] = {}
        self._stop_fence: object | None = None

    def start(self) -> None:
        self._stop_fence = object()

    async def stop(self) -> None:
        self._stop_fence = None
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
        source: PhotoObservationSource,
        images: Sequence[LlmInputImage | InboundMediaItem],
        *,
        wait_for_completion: Callable[[], Awaitable[bool]],
        item_origins: Sequence[PhotoItemOrigin] | None = None,
    ) -> None:
        fence = self._stop_fence
        if fence is None or source.generation_id in self._tasks or not images:
            return

        if len(self._tasks) >= MAX_PENDING_IMAGES:
            return

        if len(images) > 4:
            return
        if item_origins is not None and len(item_origins) != len(images):
            return
        batch = tuple(images)
        origins = tuple(item_origins) if item_origins is not None else None
        task = asyncio.create_task(
            self._observe_batch_pipeline(source, batch, fence, wait_for_completion, origins),
            name=f"photo-observation-{source.generation_id}",
        )
        self._tasks[source.generation_id] = (source.connection_id, task)
        task.add_done_callback(lambda _: self._tasks.pop(source.generation_id, None))

    async def observe(
        self,
        source: PhotoObservationSource,
        image: LlmInputImage | InboundMediaItem,
        *,
        wait_for_completion: Callable[[], Awaitable[bool]],
    ) -> None:
        await self.observe_batch(source, (image,), wait_for_completion=wait_for_completion)

    async def _observe_batch_pipeline(
        self,
        source: PhotoObservationSource,
        images: tuple[LlmInputImage | InboundMediaItem, ...],
        fence: object,
        wait_for_completion: Callable[[], Awaitable[bool]],
        item_origins: tuple[PhotoItemOrigin, ...] | None = None,
    ) -> None:
        if item_origins is not None and len(item_origins) != len(images):
            return
        try:
            total_budget = len(images) * MAX_LEARNING_SECONDS
            async with asyncio.timeout(total_budget):
                settings = await self.repository.get_settings(
                    source.principal_scope, source.character_id
                )
                if not settings.retention_enabled or self._stop_fence is not fence:
                    return
                saved_records: list[SavedPhoto] = []
                for idx, image in enumerate(images):
                    if self._stop_fence is not fence:
                        return
                    if isinstance(image, InboundMediaItem) and image.is_animated:
                        logger.info(
                            "skipping animated media item for photo memory generation_id=%s idx=%s",
                            source.generation_id,
                            idx,
                        )
                        continue
                    actual_image = (
                        image.raster_image if isinstance(image, InboundMediaItem) else image
                    )
                    item_origin = item_origins[idx] if item_origins is not None else None
                    try:
                        async with asyncio.timeout(MAX_LEARNING_SECONDS):
                            record = await self._observe(
                                source,
                                actual_image,
                                settings.revision,
                                wait_for_completion,
                                item_origin=item_origin,
                            )
                            if record is not None:
                                saved_records.append(record)
                    except asyncio.CancelledError:
                        raise
                    except Exception:
                        logger.warning(
                            "photo observation failed for image in batch generation_id=%s",
                            source.generation_id,
                        )
                if saved_records and self._annotations is not None and self._stop_fence is fence:
                    self._annotations.observe(source.generation_id)
                if (
                    saved_records
                    and self._semantic_service is not None
                    and self._stop_fence is fence
                ):
                    self._semantic_service.index_new_photos(
                        source.principal_scope, source.character_id, tuple(saved_records)
                    )
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.warning(
                "photo observation pipeline skipped generation_id=%s", source.generation_id
            )

    async def _observe(
        self,
        source: PhotoObservationSource,
        image: LlmInputImage,
        revision: int,
        wait_for_completion: Callable[[], Awaitable[bool]],
        item_origin: PhotoItemOrigin | None = None,
    ) -> SavedPhoto | None:
        try:
            async with asyncio.timeout(MAX_LEARNING_SECONDS):
                classification = await self._classifier.classify(
                    image, generation_id=source.generation_id
                )
                if classification is None or not await wait_for_completion():
                    return None

                meta = extract_photo_metadata(image.data, fallback_mime=image.mime_type)
                data, mime_type, width, height = _normalize_photo(image)

                record = await self.repository.save(
                    source.principal_scope,
                    source.character_id,
                    PhotoSaveCandidate(
                        data=data,
                        mime_type=mime_type,
                        width=width,
                        height=height,
                        title=classification.title.strip(),
                        description=classification.description.strip(),
                        confidence=classification.confidence,
                        keywords=tuple(k.strip() for k in classification.keywords),
                        source_connection_id=source.connection_id,
                        generation_id=source.generation_id,
                        captured_at=meta.captured_at,
                        captured_at_offset=meta.captured_at_offset,
                        original_width=meta.original_width,
                        original_height=meta.original_height,
                        original_mime_type=meta.original_mime_type,
                        item_origin=item_origin,
                    ),
                    expected_revision=revision,
                )
                logger.info(
                    "photo observation completed generation_id=%s saved=%s",
                    source.generation_id,
                    record is not None,
                )
                return record
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.warning("photo observation skipped generation_id=%s", source.generation_id)
            return None


def _normalize_photo(
    image: LlmInputImage,
) -> tuple[bytes, Literal["image/png", "image/jpeg"], int, int]:
    if not image.data:
        raise ValueError("empty image")
    if len(image.data) > 5 * 1024 * 1024:
        raise ValueError("input photo exceeds size limit")
    with Image.open(io.BytesIO(image.data)) as source:
        if source.format not in {"PNG", "JPEG"} or getattr(source, "n_frames", 1) != 1:
            raise ValueError("unsupported photo image")
        if source.width > 8192 or source.height > 8192 or source.width * source.height > 16_777_216:
            raise ValueError("photo image dimensions exceed limits")

        source = ImageOps.exif_transpose(source)

        if source.mode in ("RGBA", "P"):
            converted = source.convert("RGBA")
            fmt = "PNG"
            mime_type: Literal["image/png", "image/jpeg"] = "image/png"
        else:
            converted = source.convert("RGB")
            fmt = "JPEG"
            mime_type = "image/jpeg"

        if converted.width > 2048 or converted.height > 2048:
            converted.thumbnail((2048, 2048), Image.Resampling.LANCZOS)

        clean = Image.new(converted.mode, converted.size)
        clean.paste(converted)

        result = io.BytesIO()
        if fmt == "JPEG":
            clean.save(result, format=fmt, quality=90)
        else:
            clean.save(result, format=fmt)

    data = result.getvalue()
    if len(data) > 5 * 1024 * 1024:
        if fmt == "JPEG":
            # try to reduce quality if it's over 5MiB for JPEG
            result = io.BytesIO()
            clean.save(result, format=fmt, quality=75)
            data = result.getvalue()
            if len(data) > 5 * 1024 * 1024:
                raise ValueError("normalized photo exceeds size limit")
        else:
            raise ValueError("normalized photo exceeds size limit")

    return data, mime_type, clean.width, clean.height
