"""Lifecycle-owned bounded index rebuild orchestration service across memory and photo domains."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Sequence
from dataclasses import replace
from datetime import UTC, datetime
from typing import Protocol
from uuid import uuid4

from chatwaifu_protocol.memory import MemoryRecord

from chatwaifu_runtime.index_orchestration.contracts import (
    DomainRebuildState,
    DomainRebuildStatus,
    IndexRebuildStatus,
    OverallRebuildState,
)
from chatwaifu_runtime.photo_memory.ports import (
    DocumentRepresentationKind,
    EmbeddingDescriptor,
    PhotoEmbeddingInput,
    PhotoMemoryRepository,
    PhotoSemanticPersistencePort,
)
from chatwaifu_runtime.photo_memory.semantic import (
    PhotoSemanticService,
    photo_embedding_text,
    validate_embedding_vector,
)


class RebuildModels(Protocol):
    def describe(self) -> EmbeddingDescriptor: ...
    async def embed(self, inputs: Sequence[str | PhotoEmbeddingInput]) -> list[list[float]]: ...


class RebuildMemoryRepository(Protocol):
    async def list_rebuild_page(
        self, *, after_id: str = "", limit: int = 200
    ) -> list[MemoryRecord]: ...


class RebuildMemoryIndex(Protocol):
    async def upsert_active_record(
        self, record: MemoryRecord, vector: list[float], *, expected_fingerprint: str | None = None
    ) -> bool: ...
    async def purge_stale_embeddings(self, active_fingerprint: str) -> int: ...


logger = logging.getLogger(__name__)

# Bounded per-item timeout during batch rebuild
REBUILD_ITEM_TIMEOUT_SECONDS: float = 5.0


class IndexRebuildService:
    """Coordinates manual singleflight rebuild of memory and photo semantic projections."""

    def __init__(
        self,
        models: RebuildModels,
        memory_repository: RebuildMemoryRepository,
        semantic_memory_index: RebuildMemoryIndex,
        photo_repository: PhotoMemoryRepository,
        photo_semantic: PhotoSemanticService,
        photo_semantic_adapter: PhotoSemanticPersistencePort,
    ) -> None:
        self._models: RebuildModels = models
        self._memory_repository: RebuildMemoryRepository = memory_repository
        self._semantic_memory_index: RebuildMemoryIndex = semantic_memory_index
        self._photo_repository: PhotoMemoryRepository = photo_repository
        self._photo_semantic: PhotoSemanticService = photo_semantic
        self._photo_semantic_adapter: PhotoSemanticPersistencePort = photo_semantic_adapter

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

            await self._photo_semantic.sync_epoch()
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
            photo_status = await self._rebuild_photos(job_id, initial_fingerprint, initial_space_id)

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
                            error="索引重建失败，请检查模型连接后重试。",
                        )
                        for d_name, dom in self._active_job.domains.items()
                    },
                    started_at=self._active_job.started_at,
                    completed_at=datetime.now(UTC).isoformat(),
                    error="索引重建失败，请检查模型连接后重试。",
                )

    def _publish_progress(
        self, job_id: str, domain: str, total: int, indexed: int, failed: int
    ) -> None:
        job = self._active_job
        if job is None or job.job_id != job_id or job.state != OverallRebuildState.RUNNING:
            return
        domains = dict(job.domains)
        domains[domain] = DomainRebuildStatus(
            domain=domain,
            state=DomainRebuildState.RUNNING,
            total_count=total,
            indexed_count=indexed,
            failed_count=failed,
        )
        self._active_job = replace(job, domains=domains)

    async def _rebuild_memory(self, job_id: str, expected_fingerprint: str) -> DomainRebuildStatus:
        try:
            active_records: list[MemoryRecord] = []
            cursor = ""
            while True:
                page = await self._memory_repository.list_rebuild_page(after_id=cursor, limit=200)
                if not page:
                    break
                active_records.extend(page)
                cursor = str(page[-1].memory_id)
            total = len(active_records)
            indexed = 0
            failed = 0
            last_err: str | None = None

            self._publish_progress(job_id, "memory", total, indexed, failed)
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
                    if len(vectors) != 1 or not validate_embedding_vector(vectors[0]):
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
                    ok = await self._semantic_memory_index.upsert_active_record(
                        rec, vectors[0], expected_fingerprint=expected_fingerprint
                    )
                    if ok:
                        indexed += 1
                    else:
                        # Record was deleted/tombstoned in DB during rebuild
                        logger.debug("record %s no longer active; skipping insert", rec.memory_id)
                except Exception as err:
                    failed += 1
                    last_err = "索引生成失败，请检查模型连接后重试。"
                    logger.warning(
                        "memory indexing failed id=%s error_type=%s",
                        rec.memory_id,
                        type(err).__name__,
                    )
                finally:
                    self._publish_progress(job_id, "memory", total, indexed, failed)

            # Purge stale embeddings for memory if no fatal errors
            if failed == 0 and self._models.describe().opaque_fingerprint == expected_fingerprint:
                await self._semantic_memory_index.purge_stale_embeddings(expected_fingerprint)

            domain_state = DomainRebuildState.FAILED if failed > 0 else DomainRebuildState.COMPLETED
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
                error="索引重建失败，请检查模型连接后重试。",
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

            self._publish_progress(job_id, "photo", total, indexed, failed)
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
                    if len(vectors) != 1 or not validate_embedding_vector(vectors[0]):
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
                        guard=lambda: (
                            self._models.describe().opaque_fingerprint == expected_fingerprint
                        ),
                    )
                    if ok:
                        indexed += 1
                    else:
                        logger.debug(
                            "photo %s no longer matches photo_assets; skipping", photo.photo_id
                        )
                except Exception as err:
                    failed += 1
                    last_err = "索引生成失败，请检查模型连接后重试。"
                    logger.warning(
                        "photo indexing failed id=%s error_type=%s",
                        photo.photo_id,
                        type(err).__name__,
                    )
                finally:
                    self._publish_progress(job_id, "photo", total, indexed, failed)

            # Purge stale vector spaces for photos if rebuild succeeded
            if failed == 0 and self._models.describe().opaque_fingerprint == expected_fingerprint:
                await self._photo_semantic_adapter.purge_stale_spaces(
                    representation, expected_space_id
                )

            domain_state = DomainRebuildState.FAILED if failed > 0 else DomainRebuildState.COMPLETED
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
                error="索引重建失败，请检查模型连接后重试。",
            )
