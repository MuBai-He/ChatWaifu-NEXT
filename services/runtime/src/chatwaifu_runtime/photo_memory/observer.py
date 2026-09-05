"""Bounded, opt-in photo memory observation."""

from __future__ import annotations

import asyncio
import io
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Literal
from uuid import UUID

from PIL import Image, ImageOps

from chatwaifu_runtime.photo_memory.classifier import PhotoClassifier
from chatwaifu_runtime.photo_memory.models import PhotoSaveCandidate
from chatwaifu_runtime.photo_memory.ports import PhotoMemoryRepository
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
    def __init__(self, repository: PhotoMemoryRepository, classifier: PhotoClassifier) -> None:
        self.repository = repository
        self._classifier = classifier
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

    async def observe(
        self,
        source: PhotoObservationSource,
        image: LlmInputImage,
        *,
        wait_for_completion: Callable[[], Awaitable[bool]],
    ) -> None:
        fence = self._stop_fence
        if fence is None or source.generation_id in self._tasks:
            return

        if len(self._tasks) >= MAX_PENDING_IMAGES:
            return

        task = asyncio.create_task(
            self._observe_pipeline(source, image, fence, wait_for_completion),
            name=f"photo-observation-{source.generation_id}",
        )
        self._tasks[source.generation_id] = (source.connection_id, task)
        task.add_done_callback(lambda _: self._tasks.pop(source.generation_id, None))

    async def _observe_pipeline(
        self,
        source: PhotoObservationSource,
        image: LlmInputImage,
        fence: object,
        wait_for_completion: Callable[[], Awaitable[bool]],
    ) -> None:
        try:
            async with asyncio.timeout(MAX_LEARNING_SECONDS):
                settings = await self.repository.get_settings(
                    source.principal_scope, source.character_id
                )
                if not settings.retention_enabled or self._stop_fence is not fence:
                    return
                await self._observe(source, image, settings.revision, wait_for_completion)
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
    ) -> None:
        try:
            async with asyncio.timeout(MAX_LEARNING_SECONDS):
                classification = await self._classifier.classify(
                    image, generation_id=source.generation_id
                )
                if classification is None or not await wait_for_completion():
                    return

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
                    ),
                    expected_revision=revision,
                )
                logger.info(
                    "photo observation completed generation_id=%s saved=%s",
                    source.generation_id,
                    record is not None,
                )
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.warning("photo observation skipped generation_id=%s", source.generation_id)


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
