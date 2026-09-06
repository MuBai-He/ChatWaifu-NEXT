"""Lifecycle-owned bounded index rebuild orchestration service across memory and photo domains."""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime
from typing import TYPE_CHECKING
from uuid import uuid4

from chatwaifu_runtime.index_orchestration.contracts import (
    DomainRebuildState,
    DomainRebuildStatus,
    IndexRebuildStatus,
    OverallRebuildState,
)
from chatwaifu_runtime.memory.semantic_index import SQLiteSemanticMemoryIndex
from chatwaifu_runtime.photo_memory.ports import (
    DocumentRepresentationKind,
    PhotoEmbeddingInput,
)
from chatwaifu_runtime.photo_memory.semantic import (
    photo_embedding_text,
    validate_embedding_vector,
)

if TYPE_CHECKING:
    from chatwaifu_runtime.persistence.sqlite_memory_repository import SQLiteMemoryRepository
    from chatwaifu_runtime.persistence.sqlite_photo_semantic import SQLitePhotoSemanticAdapter
    from chatwaifu_runtime.photo_memory.ports import PhotoMemoryRepository
    from chatwaifu_runtime.photo_memory.semantic import PhotoSemanticService
    from chatwaifu_runtime.providers.model_config import ModelConfigurationService

logger = logging.getLogger(__name__)

# Bounded per-item timeout during batch rebuild
REBUILD_ITEM_TIMEOUT_SECONDS: float = 5.0


class IndexRebuildService:
    """Coordinates manual singleflight rebuild of memory and photo semantic projections."""

    def __init__(
        self,
        models: ModelConfigurationService,
        memory_repository: SQLiteMemoryRepository,
        semantic_memory_index: SQLiteSemanticMemoryIndex,
        photo_repository: PhotoMemoryRepository,
        photo_semantic: PhotoSemanticService,
        photo_semantic_adapter: SQLitePhotoSemanticAdapter,
    ) -> None:
        self._models: ModelConfigurationService = models
        self._memory_repository: SQLiteMemoryRepository = memory_repository
        self._semantic_memory_index: SQLiteSemanticMemoryIndex = semantic_memory_index
        self._photo_repository: PhotoMemoryRepository = photo_repository
        self._photo_semantic: PhotoSemanticService = photo_semantic
        self._photo_semantic_adapter: SQLitePhotoSemanticAdapter = photo_semantic_adapter

        self._active_job: IndexRebuildStatus | None = None
        self._active_task: asyncio.Task[None] | None = None
        self._lock = asyncio.Lock()

    def get_status(self) -> IndexRebuildStatus:
        if self._active_job is not None:
            return self._active_job
        return IndexRebuildStatus(
            job_id="",
            state=OverallRebuildState.IDLE,
            domains={
                "memory": DomainRebuildStatus(
                    domain="memory",
                    state=DomainRebuildState.IDLE,
                    total_count=0,
                    indexed_count=0,
                    failed_count=0,
                ),
                "photo": DomainRebuildStatus(
                    domain="photo",
                    state=DomainRebuildState.IDLE,
                    total_count=0,
                    indexed_count=0,
                    failed_count=0,
                ),
            },
        )

    async def start_rebuild(self) -> IndexRebuildStatus:
        """Start a singleflight lifecycle-owned cancellable index rebuild task."""
        async with self._lock:
            if self._active_task is not None and not self._active_task.done():
                logger.info("rebuild already running, returning active status (singleflight)")
                return self.get_status()

            job_id = str(uuid4())
            now_iso = datetime.now(UTC).isoformat()
            initial_status = IndexRebuildStatus(
                job_id=job_id,
                state=OverallRebuildState.RUNNING,
                domains={
                    "memory": DomainRebuildStatus(
                        domain="memory",
                        state=DomainRebuildState.RUNNING,
                        total_count=0,
                        indexed_count=0,
                        failed_count=0,
                    ),
                    "photo": DomainRebuildStatus(
                        domain="photo",
                        state=DomainRebuildState.RUNNING,
                        total_count=0,
                        indexed_count=0,
                        failed_count=0,
                    ),
                },
                started_at=now_iso,
            )
            self._active_job = initial_status
            task = asyncio.create_task(
                self._run_rebuild(job_id),
                name=f"index-rebuild-{job_id}",
            )
            self._active_task = task
            return initial_status

    def on_model_route_change(self) -> None:
        """Safely cancel/stale running job when embedding model changes."""
        if self._active_task is not None and not self._active_task.done():
            logger.info("embedding model route changed; cancelling active rebuild job")
            self._active_task.cancel()
            if self._active_job is not None:
                self._active_job = IndexRebuildStatus(
                    job_id=self._active_job.job_id,
                    state=OverallRebuildState.CANCELLED,
                    domains={
                        d_name: DomainRebuildStatus(
                            domain=d_name,
                            state=DomainRebuildState.CANCELLED,
                            total_count=dom.total_count,
                            indexed_count=dom.indexed_count,
                            failed_count=dom.failed_count,
                            error="Embedding model changed during rebuild",
                        )
                        for d_name, dom in self._active_job.domains.items()
                    },
                    started_at=self._active_job.started_at,
                    completed_at=datetime.now(UTC).isoformat(),
                    error="Embedding model changed during rebuild",
                )

    async def stop(self) -> None:
        """Cancel running rebuild job on shutdown."""
        if self._active_task is not None and not self._active_task.done():
            self._active_task.cancel()
            try:
                await asyncio.gather(self._active_task, return_exceptions=True)
            except Exception:
                pass
            self._active_task = None

    async def _run_rebuild(self, job_id: str) -> None:
        try:
            desc = self._models.describe()
            if not desc.enabled or not desc.semantic_capability:
                logger.warning(
                    "rebuild aborted: embedding model disabled or lacking semantic capability"
                )
                self._active_job = IndexRebuildStatus(
                    job_id=job_id,
                    state=OverallRebuildState.FAILED,
                    domains={
                        "memory": DomainRebuildStatus(
                            domain="memory",
                            state=DomainRebuildState.FAILED,
                            total_count=0,
                            indexed_count=0,
                            failed_count=0,
                            error="Embedding model is disabled or not configured",
                        ),
                        "photo": DomainRebuildStatus(
                            domain="photo",
                            state=DomainRebuildState.FAILED,
                            total_count=0,
                            indexed_count=0,
                            failed_count=0,
                            error="Embedding model is disabled or not configured",
                        ),
                    },
                    started_at=self._active_job.started_at if self._active_job else None,
                    completed_at=datetime.now(UTC).isoformat(),
                    error="Embedding model is disabled or not configured",
                )
                return

            # Capture route snapshot
            initial_fingerprint = desc.opaque_fingerprint
            initial_space_id = desc.vector_space_id

            # 1. Rebuild Memory domain
            mem_status = await self._rebuild_memory(job_id, initial_fingerprint)

            # Check if cancelled or model changed
            if (
                self._models.describe().opaque_fingerprint != initial_fingerprint
                or self._active_job is None
                or self._active_job.state == OverallRebuildState.CANCELLED
            ):
                return

            # Update intermediate progress
            self._active_job = IndexRebuildStatus(
                job_id=job_id,
                state=OverallRebuildState.RUNNING,
                domains={
                    "memory": mem_status,
                    "photo": DomainRebuildStatus(
                        domain="photo",
                        state=DomainRebuildState.RUNNING,
                        total_count=0,
                        indexed_count=0,
                        failed_count=0,
                    ),
                },
                started_at=self._active_job.started_at,
            )

            # 2. Rebuild Photo domain
            photo_status = await self._rebuild_photos(
                job_id, initial_fingerprint, initial_space_id
            )

            # Check if cancelled or model changed during photo rebuild
            if (
                self._models.describe().opaque_fingerprint != initial_fingerprint
                or self._active_job.state == OverallRebuildState.CANCELLED
            ):
                return

            overall_failed = (
                mem_status.state == DomainRebuildState.FAILED
                or photo_status.state == DomainRebuildState.FAILED
            )
            overall_state = (
                OverallRebuildState.FAILED if overall_failed else OverallRebuildState.COMPLETED
            )
            overall_error = None
            if overall_failed:
                errs = [e for e in (mem_status.error, photo_status.error) if e]
                overall_error = "; ".join(errs) if errs else "Rebuild failed on one or more items"

            self._active_job = IndexRebuildStatus(
                job_id=job_id,
                state=overall_state,
                domains={
                    "memory": mem_status,
                    "photo": photo_status,
                },
                started_at=self._active_job.started_at,
                completed_at=datetime.now(UTC).isoformat(),
                error=overall_error,
            )
        except asyncio.CancelledError:
            logger.info("rebuild job %s was cancelled", job_id)
            if self._active_job is not None:
                self._active_job = IndexRebuildStatus(
                    job_id=job_id,
                    state=OverallRebuildState.CANCELLED,
                    domains={
                        d_name: DomainRebuildStatus(
                            domain=d_name,
                            state=DomainRebuildState.CANCELLED,
                            total_count=dom.total_count,
                            indexed_count=dom.indexed_count,
                            failed_count=dom.failed_count,
                            error="Rebuild cancelled",
                        )
                        for d_name, dom in self._active_job.domains.items()
                    },
                    started_at=self._active_job.started_at,
                    completed_at=datetime.now(UTC).isoformat(),
                    error="Rebuild cancelled",
                )
            raise
        except Exception as err:
            logger.exception("unexpected error during index rebuild job %s: %s", job_id, err)
            if self._active_job is not None:
                self._active_job = IndexRebuildStatus(
                    job_id=job_id,
                    state=OverallRebuildState.FAILED,
                    domains={
                        d_name: DomainRebuildStatus(
                            domain=d_name,
                            state=DomainRebuildState.FAILED,
                            total_count=dom.total_count,
                            indexed_count=dom.indexed_count,
                            failed_count=dom.failed_count,
                            error=str(err),
                        )
                        for d_name, dom in self._active_job.domains.items()
                    },
                    started_at=self._active_job.started_at,
                    completed_at=datetime.now(UTC).isoformat(),
                    error=str(err),
                )

    async def _rebuild_memory(
        self, job_id: str, expected_fingerprint: str
    ) -> DomainRebuildStatus:
        try:
            records = await self._memory_repository.list_records(limit=1000)
            active_records = [r for r in records if r.state == "active"]
            total = len(active_records)
            indexed = 0
            failed = 0
            last_err: str | None = None

            for rec in active_records:
                if self._models.describe().opaque_fingerprint != expected_fingerprint:
                    return DomainRebuildStatus(
                        domain="memory",
                        state=DomainRebuildState.CANCELLED,
                        total_count=total,
                        indexed_count=indexed,
                        failed_count=failed,
                        error="Embedding model changed during memory rebuild",
                    )
                try:
                    async with asyncio.timeout(REBUILD_ITEM_TIMEOUT_SECONDS):
                        vectors = await self._models.embed([rec.text])
                    if not vectors or not validate_embedding_vector(vectors[0]):
                        failed += 1
                        last_err = "Invalid vector generated"
                        continue

                    # Post-await check
                    if self._models.describe().opaque_fingerprint != expected_fingerprint:
                        return DomainRebuildStatus(
                            domain="memory",
                            state=DomainRebuildState.CANCELLED,
                            total_count=total,
                            indexed_count=indexed,
                            failed_count=failed,
                            error="Embedding model changed during memory rebuild",
                        )

                    # Atomically insert only if active record still exists (no resurrection!)
                    ok = await self._semantic_memory_index.upsert_active_record(rec, vectors[0])
                    if ok:
                        indexed += 1
                    else:
                        # Record was deleted/tombstoned in DB during rebuild
                        logger.debug("record %s no longer active; skipping insert", rec.memory_id)
                except Exception as err:
                    failed += 1
                    last_err = str(err)
                    logger.warning("failed to embed memory record %s: %s", rec.memory_id, err)

            # Purge stale embeddings for memory if no fatal errors
            if failed == 0 and self._models.describe().opaque_fingerprint == expected_fingerprint:
                await self._semantic_memory_index.purge_stale_embeddings(expected_fingerprint)

            domain_state = (
                DomainRebuildState.FAILED if failed > 0 else DomainRebuildState.COMPLETED
            )
            return DomainRebuildStatus(
                domain="memory",
                state=domain_state,
                total_count=total,
                indexed_count=indexed,
                failed_count=failed,
                error=last_err if failed > 0 else None,
            )
        except Exception as err:
            logger.exception("memory rebuild domain failed: %s", err)
            return DomainRebuildStatus(
                domain="memory",
                state=DomainRebuildState.FAILED,
                total_count=0,
                indexed_count=0,
                failed_count=1,
                error=str(err),
            )

    async def _rebuild_photos(
        self, job_id: str, expected_fingerprint: str, expected_space_id: str
    ) -> DomainRebuildStatus:
        try:
            photos_data = await self._photo_semantic_adapter.list_all_photos()
            total = len(photos_data)
            indexed = 0
            failed = 0
            last_err: str | None = None
            representation = DocumentRepresentationKind.PHOTO_DESCRIPTION_V1.value

            for scope, char_id, photo in photos_data:
                if self._models.describe().opaque_fingerprint != expected_fingerprint:
                    return DomainRebuildStatus(
                        domain="photo",
                        state=DomainRebuildState.CANCELLED,
                        total_count=total,
                        indexed_count=indexed,
                        failed_count=failed,
                        error="Embedding model changed during photo rebuild",
                    )
                text = photo_embedding_text(photo)
                if not text.strip():
                    continue

                try:
                    async with asyncio.timeout(REBUILD_ITEM_TIMEOUT_SECONDS):
                        vectors = await self._models.embed([PhotoEmbeddingInput.from_text(text)])
                    if not vectors or not validate_embedding_vector(vectors[0]):
                        failed += 1
                        last_err = "Invalid vector generated"
                        continue

                    # Post-await check
                    if self._models.describe().opaque_fingerprint != expected_fingerprint:
                        return DomainRebuildStatus(
                            domain="photo",
                            state=DomainRebuildState.CANCELLED,
                            total_count=total,
                            indexed_count=indexed,
                            failed_count=failed,
                            error="Embedding model changed during photo rebuild",
                        )

                    # Atomically insert only if photo still exists in photo_assets with matching sha
                    ok = await self._photo_semantic_adapter.upsert_embedding(
                        scope,
                        char_id,
                        photo.photo_id,
                        photo.sha256,
                        representation,
                        expected_space_id,
                        expected_fingerprint,
                        self._photo_semantic.route_generation,
                        vectors[0],
                    )
                    if ok:
                        indexed += 1
                    else:
                        logger.debug(
                            "photo %s no longer matches photo_assets; skipping", photo.photo_id
                        )
                except Exception as err:
                    failed += 1
                    last_err = str(err)
                    logger.warning("failed to embed photo %s: %s", photo.photo_id, err)

            # Purge stale vector spaces for photos if rebuild succeeded
            if failed == 0 and self._models.describe().opaque_fingerprint == expected_fingerprint:
                await self._photo_semantic_adapter.purge_stale_spaces(
                    representation, expected_space_id
                )

            domain_state = (
                DomainRebuildState.FAILED if failed > 0 else DomainRebuildState.COMPLETED
            )
            return DomainRebuildStatus(
                domain="photo",
                state=domain_state,
                total_count=total,
                indexed_count=indexed,
                failed_count=failed,
                error=last_err if failed > 0 else None,
            )
        except Exception as err:
            logger.exception("photo rebuild domain failed: %s", err)
            return DomainRebuildStatus(
                domain="photo",
                state=DomainRebuildState.FAILED,
                total_count=0,
                indexed_count=0,
                failed_count=1,
                error=str(err),
            )
