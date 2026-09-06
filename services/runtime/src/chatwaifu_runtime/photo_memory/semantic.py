"""Bounded semantic photo recall service, embedding routing, ranking and lifecycle."""

from __future__ import annotations

import asyncio
import logging
import math
from collections.abc import Sequence
from dataclasses import dataclass
from typing import cast
from uuid import UUID

from chatwaifu_protocol.photo_memory import SavedPhoto

from chatwaifu_runtime.photo_memory.ports import (
    DocumentRepresentationKind,
    EmbeddingDescriptor,
    PhotoEmbeddingInput,
    PhotoEmbeddingPort,
    PhotoSemanticPersistencePort,
)

logger = logging.getLogger(__name__)

# Heuristic minimum similarity threshold based on empirical endpoint probing (ADR 0038).
# In production probe, grounded paraphrases scored ~0.58-0.69, distractors lower,
# and unrelated negative questions scored <= 0.45.
MIN_SEMANTIC_COSINE_SIMILARITY: float = 0.55

# Ambiguity threshold gap: if the top two candidates both meet the minimum similarity threshold
# and the gap between them is less than 0.08, retain both and prompt for clarification rather
# than attaching an arbitrary photo.
SEMANTIC_AMBIGUITY_GAP: float = 0.08

# Strict bounded query budget for whole semantic recall operation <= 1.5s.
SEMANTIC_QUERY_BUDGET_SECONDS: float = 1.5

# Upper bound on vector dimension for sanity and memory limits.
MAX_VECTOR_DIM: int = 8192

# Bounded incremental indexing tasks and per-task timeout
MAX_INCREMENTAL_TASKS: int = 2
INCREMENTAL_TIMEOUT_SECONDS: float = 5.0


@dataclass(frozen=True, slots=True)
class ScoredPhotoMatch:
    photo_id: UUID
    score: float


def photo_embedding_text(photo: SavedPhoto) -> str:
    """Format photo content for semantic embedding consistently with lexical indexing."""
    parts = [photo.title, photo.description, " ".join(photo.keywords), photo.caption]
    return " ".join(part.strip() for part in parts if part.strip())


def validate_embedding_vector(vector: object, expected_dim: int | None = None) -> bool:
    """Validate vector type, dimensions, non-emptiness, finite numeric values, and safe norm.

    Rejects bools (which are int subclasses in Python), strings, nulls, NaNs, infinities,
    and overflowing norms.
    """
    if not isinstance(vector, (list, tuple)):
        return False
    items = list(cast(Sequence[object], vector))
    if len(items) == 0 or len(items) > MAX_VECTOR_DIM:
        return False
    if expected_dim is not None and len(items) != expected_dim:
        return False

    float_vals: list[float] = []
    for val in items:
        if isinstance(val, bool) or not isinstance(val, (int, float)):
            return False
        fval = float(val)
        if not math.isfinite(fval):
            return False
        float_vals.append(fval)

    try:
        norm = math.hypot(*float_vals)
    except OverflowError:
        return False

    if not math.isfinite(norm) or norm <= 1e-12:
        return False

    return True


def cosine_similarity(a: Sequence[float], b: Sequence[float]) -> float:
    """Compute cosine similarity safely without float overflow using unit components."""
    if len(a) != len(b) or len(a) == 0:
        raise ValueError(f"vector dimension mismatch: {len(a)} vs {len(b)}")

    try:
        norm_a = math.hypot(*a)
        norm_b = math.hypot(*b)
    except OverflowError:
        return 0.0

    if norm_a <= 1e-12 or norm_b <= 1e-12 or not math.isfinite(norm_a) or not math.isfinite(norm_b):
        return 0.0

    dot = sum((x / norm_a) * (y / norm_b) for x, y in zip(a, b, strict=True))
    if not math.isfinite(dot):
        return 0.0
    return max(-1.0, min(1.0, dot))


class PhotoSemanticService:
    """Domain service owning model network calls, ranking, lifecycle and finite-pass backfill."""

    def __init__(
        self,
        persistence: PhotoSemanticPersistencePort,
        embedding: PhotoEmbeddingPort,
        *,
        min_cosine: float = MIN_SEMANTIC_COSINE_SIMILARITY,
        ambiguity_gap: float = SEMANTIC_AMBIGUITY_GAP,
        query_budget: float = SEMANTIC_QUERY_BUDGET_SECONDS,
    ) -> None:
        self._persistence: PhotoSemanticPersistencePort = persistence
        self._embedding: PhotoEmbeddingPort = embedding
        self._min_cosine = min_cosine
        self._ambiguity_gap = ambiguity_gap
        self._query_budget = query_budget
        self._incremental_tasks: set[asyncio.Task[bool]] = set()
        self._running: bool = False
        self._route_generation: int = 0

    @property
    def route_generation(self) -> int:
        return self._route_generation

    @property
    def active_task_count(self) -> int:
        self._incremental_tasks = {t for t in self._incremental_tasks if not t.done()}
        return len(self._incremental_tasks)

    async def sync_epoch(self) -> int:
        """Durable monotonic epoch synchronization across restarts."""
        max_gen = await self._persistence.get_max_route_generation()
        if max_gen >= self._route_generation:
            self._route_generation = max_gen + 1
        return self._route_generation

    def start(self) -> None:
        """Idempotent start of photo semantic service without automatic backfill."""
        if self._running:
            return
        self._running = True
        self._route_generation += 1

    async def stop(self) -> None:
        """Tracked graceful shutdown cancelling incremental background tasks without leak."""
        self._running = False
        tasks = list(self._incremental_tasks)
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self._incremental_tasks.clear()

    def index_new_photo(self, scope: str, character_id: str, photo: SavedPhoto) -> bool:
        """Incrementally index a newly saved photo, bounded to max 2 concurrent tasks."""
        if not self._running:
            return False
        desc = self._embedding.describe()
        if not desc.enabled or not desc.semantic_capability:
            return False
        self._incremental_tasks = {t for t in self._incremental_tasks if not t.done()}
        if len(self._incremental_tasks) >= MAX_INCREMENTAL_TASKS:
            logger.warning(
                "dropping incremental photo indexing for %s: bounded task capacity (%d) reached",
                photo.photo_id,
                MAX_INCREMENTAL_TASKS,
            )
            return False
        gen = self._route_generation
        task = asyncio.create_task(
            self._bounded_index_single_photo(scope, character_id, photo, desc, gen),
            name=f"photo-semantic-index-{photo.photo_id}",
        )
        self._incremental_tasks.add(task)
        task.add_done_callback(lambda t: self._incremental_tasks.discard(t))
        return True

    def notify_route_change(self) -> None:
        """Increment generation token and cancel running incremental tasks without autobackfill."""
        self._route_generation += 1
        for task in list(self._incremental_tasks):
            task.cancel()

    async def _bounded_index_single_photo(
        self,
        scope: str,
        character_id: str,
        photo: SavedPhoto,
        desc: EmbeddingDescriptor,
        generation: int,
    ) -> bool:
        try:
            async with asyncio.timeout(INCREMENTAL_TIMEOUT_SECONDS):
                return await self._index_single_photo(scope, character_id, photo, desc, generation)
        except TimeoutError:
            logger.warning(
                "incremental indexing for photo %s timed out after %.1fs",
                photo.photo_id,
                INCREMENTAL_TIMEOUT_SECONDS,
            )
            return False
        except asyncio.CancelledError:
            raise
        except Exception as err:
            logger.warning("incremental indexing for photo %s failed: %s", photo.photo_id, err)
            return False

    async def _index_single_photo(
        self,
        scope: str,
        character_id: str,
        photo: SavedPhoto,
        desc: EmbeddingDescriptor,
        generation: int,
    ) -> bool:
        """Embed and commit a saved photo outside DB transaction with strict fence checks."""
        text = photo_embedding_text(photo)
        if not text.strip():
            return False

        if self._route_generation != generation or not self._running:
            return False

        input_item = PhotoEmbeddingInput.from_text(text)
        try:
            vectors = await self._embedding.embed([input_item])
        except asyncio.CancelledError:
            raise
        except Exception as err:
            logger.warning("failed to embed photo %s: %s", photo.photo_id, err)
            return False

        # Reject count != 1 for single embeds
        if len(vectors) != 1:
            logger.warning(
                "single photo embed returned %d vectors instead of 1 for photo %s",
                len(vectors),
                photo.photo_id,
            )
            return False

        # Post-await check 1: route generation or vector space changed during network call
        post_desc = self._embedding.describe()
        if (
            self._route_generation != generation
            or post_desc.vector_space_id != desc.vector_space_id
            or post_desc.opaque_fingerprint != desc.opaque_fingerprint
            or not self._running
        ):
            logger.warning(
                "model route changed during embedding photo %s; discarding vector", photo.photo_id
            )
            return False

        if not validate_embedding_vector(vectors[0]):
            logger.warning("invalid or corrupt vector returned for photo %s", photo.photo_id)
            return False

        # Transaction guard immediately checked INSIDE awaited SQL transaction before commit
        def guard() -> bool:
            return (
                self._running
                and self._route_generation == generation
                and self._embedding.describe().vector_space_id == desc.vector_space_id
                and self._embedding.describe().opaque_fingerprint == desc.opaque_fingerprint
            )

        return await self._persistence.upsert_embedding(
            scope,
            character_id,
            photo.photo_id,
            photo.sha256,
            DocumentRepresentationKind.PHOTO_DESCRIPTION_V1.value,
            desc.vector_space_id,
            desc.opaque_fingerprint,
            generation,
            vectors[0],
            guard=guard,
        )

    async def search(
        self,
        scope: str,
        character_id: str,
        query: str,
        *,
        limit: int = 2,
    ) -> tuple[list[ScoredPhotoMatch], bool]:
        """Search photos semantically. Bounded <= 1.5s total budget.
        No foreground backfill network.
        """
        if not query.strip():
            return [], False

        try:
            async with asyncio.timeout(self._query_budget):
                return await self._bounded_search(scope, character_id, query, limit=limit)
        except TimeoutError:
            logger.warning(
                "photo semantic search timed out within %.2fs budget", self._query_budget
            )
            return [], False
        except Exception as err:
            logger.warning("photo semantic search encountered error: %s", err)
            return [], False

    async def _bounded_search(
        self,
        scope: str,
        character_id: str,
        query: str,
        *,
        limit: int = 2,
    ) -> tuple[list[ScoredPhotoMatch], bool]:
        desc_before = self._embedding.describe()
        gen_before = self._route_generation

        if not desc_before.enabled or not desc_before.semantic_capability:
            return [], False

        representation = DocumentRepresentationKind.PHOTO_DESCRIPTION_V1.value

        # Fast pre-check: No extra embedding if no indexed photos exist
        indexed_count = await self._persistence.count_embeddings(
            scope, character_id, representation
        )
        if indexed_count == 0:
            return [], False

        query_input = PhotoEmbeddingInput.from_text(query)
        vectors = await self._embedding.embed([query_input])

        # Post-await check 1: Model route or generation swap during query embedding
        desc_after = self._embedding.describe()
        gen_after = self._route_generation
        if gen_before != gen_after or desc_before.vector_space_id != desc_after.vector_space_id:
            logger.warning("model route changed during search query embedding; discarding result")
            return [], False

        if not vectors or not validate_embedding_vector(vectors[0]):
            return [], False

        query_vec = vectors[0]
        q_dim = len(query_vec)

        stored = await self._persistence.list_embeddings(scope, character_id, representation)

        # Post-await check 2: Model swap after fetching stored rows before ranking
        if (
            self._route_generation != gen_after
            or self._embedding.describe().vector_space_id != desc_after.vector_space_id
        ):
            logger.warning(
                "model route changed after fetching stored embeddings; discarding result"
            )
            return [], False

        if not stored:
            return [], False

        candidates: list[ScoredPhotoMatch] = []
        for photo_id, stored_vec in stored:
            if len(stored_vec) != q_dim:
                logger.debug(
                    "photo %s dimension mismatch: stored=%d query=%d; skipping",
                    photo_id,
                    len(stored_vec),
                    q_dim,
                )
                continue
            if not validate_embedding_vector(stored_vec, expected_dim=q_dim):
                continue
            sim = cosine_similarity(query_vec, stored_vec)
            if sim >= self._min_cosine:
                candidates.append(ScoredPhotoMatch(photo_id=photo_id, score=sim))

        if not candidates:
            return [], False

        candidates.sort(key=lambda c: c.score, reverse=True)

        if len(candidates) == 1:
            return [candidates[0]], False

        s1 = candidates[0].score
        s2 = candidates[1].score
        gap = s1 - s2
        if gap < self._ambiguity_gap:
            return candidates[:2], True
        else:
            return [candidates[0]], False
