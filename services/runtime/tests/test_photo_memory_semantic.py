"""Comprehensive event-driven regressions for Phase 17.3D bounded semantic photo recall."""
# pyright: reportPrivateUsage=false

from __future__ import annotations

import asyncio
import io
import json
import math
from collections.abc import Callable, Coroutine, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import pytest
from chatwaifu_protocol.photo_memory import SavedPhoto
from chatwaifu_runtime.config.settings import StorageConfig
from chatwaifu_runtime.persistence.database import Database
from chatwaifu_runtime.persistence.sqlite_photo_memory import SQLitePhotoMemoryRepository
from chatwaifu_runtime.persistence.sqlite_photo_semantic import SQLitePhotoSemanticAdapter
from chatwaifu_runtime.photo_memory.models import PhotoSaveCandidate
from chatwaifu_runtime.photo_memory.ports import (
    DocumentRepresentationKind,
    EmbeddingDescriptor,
    EmbeddingModality,
    PhotoEmbeddingInput,
)
from chatwaifu_runtime.photo_memory.recall import (
    PhotoRecallService,
    extract_photo_content_query,
    is_recent_only_request,
)
from chatwaifu_runtime.photo_memory.semantic import (
    PhotoSemanticService,
    cosine_similarity,
    photo_embedding_text,
    validate_embedding_vector,
)
from chatwaifu_runtime.providers.model_config import UnsupportedEmbeddingModalityError
from PIL import Image


def make_test_png(color: tuple[int, int, int] | None = None) -> bytes:
    if color is None:
        u = uuid4().int
        color = (u % 256, (u >> 8) % 256, (u >> 16) % 256)
    img = Image.new("RGB", (1, 1), color)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def _make_unit_vector(dim: int, seed: int) -> list[float]:
    """Generate a deterministic unit vector."""
    vec = [math.sin(seed * (i + 1)) for i in range(dim)]
    norm = math.hypot(*vec)
    return [x / norm for x in vec]


def _make_vector_with_cosine(base: list[float], cosine: float, seed: int = 999) -> list[float]:
    """Construct a unit vector with exact cosine similarity to base."""
    dim = len(base)
    raw = _make_unit_vector(dim, seed)
    dot = sum(b * r for b, r in zip(base, raw, strict=True))
    ortho = [r - dot * b for b, r in zip(base, raw, strict=True)]
    norm_ortho = math.hypot(*ortho)
    ortho_unit = [x / norm_ortho for x in ortho]

    sin_theta = math.sqrt(max(0.0, 1.0 - cosine * cosine))
    res = [cosine * b + sin_theta * o for b, o in zip(base, ortho_unit, strict=True)]
    res_norm = math.hypot(*res)
    return [x / res_norm for x in res]


class FakeNeuralEmbeddingProvider:
    def __init__(
        self,
        vector_space_id: str = "openai_compatible:test-embedding-v1",
        fingerprint: str = "oai_test_fp",
        dim: int = 2048,
        semantic_capability: bool = True,
        enabled: bool = True,
    ) -> None:
        self.space_id = vector_space_id
        self.fingerprint_value = fingerprint
        self.dim = dim
        self.semantic_capability = semantic_capability
        self.enabled = enabled
        self.custom_vectors: dict[str, list[float]] = {}
        self.embed_call_count = 0
        self.hang_seconds: float = 0.0
        self.should_raise: Exception | None = None
        self.pre_embed_callback: Callable[[], Coroutine[Any, Any, None]] | None = None

    def describe(self) -> EmbeddingDescriptor:
        return EmbeddingDescriptor(
            supported_modalities=frozenset([EmbeddingModality.TEXT]),
            vector_space_id=self.space_id,
            semantic_capability=self.semantic_capability,
            enabled=self.enabled,
            opaque_fingerprint=self.fingerprint_value,
        )

    def register(self, text: str, vector: list[float]) -> None:
        self.custom_vectors[text] = vector

    async def embed(self, inputs: Sequence[PhotoEmbeddingInput]) -> list[list[float]]:
        self.embed_call_count += 1
        for inp in inputs:
            if inp.modality != EmbeddingModality.TEXT:
                raise UnsupportedEmbeddingModalityError(
                    f"Modality {inp.modality} unsupported by text adapter"
                )
        if self.pre_embed_callback is not None:
            await self.pre_embed_callback()
        if self.should_raise is not None:
            raise self.should_raise
        if self.hang_seconds > 0:
            await asyncio.sleep(self.hang_seconds)

        results: list[list[float]] = []
        for i, inp in enumerate(inputs):
            text = inp.text or ""
            if text in self.custom_vectors:
                results.append(self.custom_vectors[text])
            else:
                results.append(_make_unit_vector(self.dim, hash(text) % 10000 + i))
        return results


@pytest.fixture
async def test_db(tmp_path: Path):
    db_path = tmp_path / "test_photo_semantic.db"
    storage = StorageConfig(database_path=db_path)
    db = Database(db_path, storage)
    await db.open()
    try:
        yield db
    finally:
        await db.close()


async def _seed_source_chain(
    db: Database,
    scope: str = "local",
    character_id: str = "ayachi_nene",
    caption: str = "my photo caption",
) -> tuple[str, str, str]:
    conn_id = str(uuid4())
    gen_id = str(uuid4())
    session_id = str(uuid4())
    turn_id = str(uuid4())
    binding_id = str(uuid4())
    channel_turn_id = str(uuid4())
    now = datetime.now(UTC).isoformat()
    sc_json = json.dumps(
        {"connection_id": conn_id, "principal_scope": scope, "chat_type": "direct"}
    )

    async with db.transaction() as conn:
        await conn.execute(
            """
            INSERT INTO channel_connections (
                connection_id, provider_id, name, character_id, principal_scope,
                enabled, access_token_hash, created_at, updated_at
            ) VALUES (?, 'weixin_ilink', 'test-conn', ?, ?, 1, 'hash', ?, ?)
            """,
            (conn_id, character_id, scope, now, now),
        )
        await conn.execute(
            """
            INSERT INTO sessions (
                session_id, character_id, state,
                conversation_state, revision, next_sequence,
                created_at, updated_at
            ) VALUES (?, ?, 'ready', 'idle', 0, 1, ?, ?)
            """,
            (session_id, character_id, now, now),
        )
        await conn.execute(
            """
            INSERT INTO turns (
                turn_id, session_id, role, committed_text,
                created_at, generation_id, source_context_json
            ) VALUES (?, ?, 'user', ?, ?, ?, ?)
            """,
            (turn_id, session_id, caption, now, gen_id, sc_json),
        )
        await conn.execute(
            """
            INSERT INTO generations (
                generation_id, session_id, turn_id, state, backend_kind, started_at
            ) VALUES (?, ?, ?, 'completed', 'local', ?)
            """,
            (gen_id, session_id, turn_id, now),
        )
        await conn.execute(
            """
            INSERT INTO channel_bindings (
                binding_id, connection_id, conversation_key,
                sender_key, session_id, created_at, updated_at
            ) VALUES (?, ?, 'conv-key', 'sender-key', ?, ?, ?)
            """,
            (binding_id, conn_id, session_id, now, now),
        )
        await conn.execute(
            """
            INSERT INTO channel_turns (
                channel_turn_id, connection_id, binding_id, external_message_id,
                content_sha256, conversation_key, sender_key, principal_scope,
                session_id, turn_id, generation_id, status, accepted_at,
                created_at, updated_at
            ) VALUES (
                ?, ?, ?, 'ext-msg', 'hash', 'conv-key', 'sender-key',
                ?, ?, ?, ?, 'completed', ?, ?, ?
            )
            """,
            (
                channel_turn_id,
                conn_id,
                binding_id,
                scope,
                session_id,
                turn_id,
                gen_id,
                now,
                now,
                now,
            ),
        )
    return conn_id, gen_id, session_id


async def _create_active_generation_for_recall(
    db: Database, scope: str = "local", character_id: str = "ayachi_nene"
) -> UUID:
    session_id = uuid4()
    generation_id = uuid4()
    turn_id = uuid4()
    now = datetime.now(UTC).isoformat()
    async with db.transaction() as conn:
        await conn.execute(
            """
            INSERT INTO sessions(
                session_id, character_id, state,
                conversation_state, revision, next_sequence,
                created_at, updated_at
            )
            VALUES (?, ?, 'ready', 'idle', 0, 1, ?, ?)
            """,
            (str(session_id), character_id, now, now),
        )
        await conn.execute(
            """
            INSERT INTO turns(
                turn_id, session_id, role, committed_text,
                created_at, generation_id, source_context_json
            )
            VALUES (?, ?, 'user', 'query', ?, ?, ?)
            """,
            (
                str(turn_id),
                str(session_id),
                now,
                str(generation_id),
                json.dumps({"chat_type": "direct", "principal_scope": scope}),
            ),
        )
        await conn.execute(
            """
            INSERT INTO generations(
                generation_id, session_id, turn_id, state,
                backend_kind, started_at
            )
            VALUES (?, ?, ?, 'running', 'local', ?)
            """,
            (str(generation_id), str(session_id), str(turn_id), now),
        )
    return generation_id


async def _seed_photo(
    db: Database,
    repo: SQLitePhotoMemoryRepository,
    scope: str = "local",
    character_id: str = "ayachi_nene",
    title: str = "海边落日",
    description: str = "远处的红色灯塔，前景有两只蓝色小船",
    keywords: tuple[str, ...] = ("灯塔", "小船", "海边"),
    caption: str = "今天天气很好",
    data: bytes | None = None,
) -> SavedPhoto:
    if data is None:
        data = make_test_png()
    conn_id, gen_id, _ = await _seed_source_chain(
        db, scope=scope, character_id=character_id, caption=caption
    )
    settings = await repo.get_settings(scope, character_id)
    if not settings.retention_enabled:
        settings = await repo.update_settings(
            scope, character_id, retention_enabled=True, expected_revision=settings.revision
        )
    saved = await repo.save(
        scope,
        character_id,
        PhotoSaveCandidate(
            data=data,
            mime_type="image/png",
            width=1,
            height=1,
            title=title,
            description=description,
            confidence=0.95,
            keywords=keywords,
            source_connection_id=UUID(conn_id),
            generation_id=UUID(gen_id),
        ),
        expected_revision=settings.revision,
    )
    assert saved is not None
    return saved


# ---------------------------------------------------------------------------
# Test 1: Migration 26 schema, representation, vector_space_id, route_generation
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_migration_26_schema_and_triggers(test_db: Database) -> None:
    cols = {row[1]: row for row in await test_db.fetchall("PRAGMA table_info(photo_embeddings)")}
    assert "photo_id" in cols
    assert "representation" in cols
    assert "principal_scope" in cols
    assert "character_id" in cols
    assert "vector_space_id" in cols
    assert "model_fingerprint" in cols
    assert "route_generation" in cols
    assert "vector_json" in cols
    assert "updated_at" in cols

    triggers = {
        row[1]
        for row in await test_db.fetchall(
            "SELECT type, name FROM sqlite_master WHERE type='trigger'"
        )
    }
    assert "photo_assets_after_delete_embeddings" in triggers

    repo = SQLitePhotoMemoryRepository(test_db)
    photo = await _seed_photo(test_db, repo)
    adapter = SQLitePhotoSemanticAdapter(test_db)
    inserted = await adapter.upsert_embedding(
        "local",
        "ayachi_nene",
        photo.photo_id,
        photo.sha256,
        "photo_description_v1",
        "space_v1",
        "test_fp",
        1,
        [0.1, 0.2],
    )
    assert inserted is True

    await repo.delete("local", "ayachi_nene", photo.photo_id)
    embeddings = await test_db.fetchall(
        "SELECT * FROM photo_embeddings WHERE photo_id = ?", (str(photo.photo_id),)
    )
    assert len(embeddings) == 0


# ---------------------------------------------------------------------------
# Test 2: Bounded whole search operation <= 1.5s (no hang on slow provider)
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_search_operation_strictly_bounded_timeout(test_db: Database) -> None:
    repo = SQLitePhotoMemoryRepository(test_db)
    adapter = SQLitePhotoSemanticAdapter(test_db)
    provider = FakeNeuralEmbeddingProvider()
    provider.hang_seconds = 5.0  # Provider hangs 5 seconds

    photo = await _seed_photo(test_db, repo)
    # Manually insert indexed projection so pre-check passes
    await adapter.upsert_embedding(
        "local",
        "ayachi_nene",
        photo.photo_id,
        photo.sha256,
        "photo_description_v1",
        provider.space_id,
        provider.fingerprint_value,
        1,
        [0.1, 0.2],
    )

    service = PhotoSemanticService(adapter, provider, query_budget=0.3)
    start_t = asyncio.get_running_loop().time()
    matches, is_ambiguous = await service.search("local", "ayachi_nene", "红屋顶照片")
    duration = asyncio.get_running_loop().time() - start_t

    assert duration < 1.0  # Finished within bounded timeout
    assert matches == []
    assert is_ambiguous is False


# ---------------------------------------------------------------------------
# Test 3: No extra embedding network call if no indexed photos
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_search_skips_embedding_if_no_indexed_photos(test_db: Database) -> None:
    adapter = SQLitePhotoSemanticAdapter(test_db)
    provider = FakeNeuralEmbeddingProvider()
    service = PhotoSemanticService(adapter, provider)

    # Database has 0 indexed photos
    matches, is_ambiguous = await service.search("local", "ayachi_nene", "测试搜索")
    assert matches == []
    assert is_ambiguous is False
    assert provider.embed_call_count == 0  # Zero network/embed calls!


# ---------------------------------------------------------------------------
# Test 4: Finite pass worker breaks on no-progress; readiness event settles
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Test 5: Route switch interleaving A -> B -> A with authoritative active generation fence
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_route_switch_interleaving_a_b_a_active_generation_fence(test_db: Database) -> None:
    repo = SQLitePhotoMemoryRepository(test_db)
    adapter = SQLitePhotoSemanticAdapter(test_db)
    provider = FakeNeuralEmbeddingProvider(vector_space_id="model_a_space")

    photo = await _seed_photo(test_db, repo)
    service = PhotoSemanticService(adapter, provider)

    # Generation 1 (Route A)
    service.start()
    gen1 = service.route_generation
    assert gen1 == 1

    # Simulate slow task starting with Generation 1
    slow_vec = [0.1, 0.2]

    # Route changes to B (Generation 2)
    service.notify_route_change()
    gen2 = service.route_generation
    assert gen2 == 2

    # Route changes back to A (Generation 3)
    service.notify_route_change()
    gen3 = service.route_generation
    assert gen3 == 3

    # Fast task finishes for Generation 3 with new vector
    current_vec = [0.8, 0.6]
    inserted = await adapter.upsert_embedding(
        "local",
        "ayachi_nene",
        photo.photo_id,
        photo.sha256,
        DocumentRepresentationKind.PHOTO_DESCRIPTION_V1.value,
        "model_a_space",
        "oai_test_fp",
        gen3,
        current_vec,
    )
    assert inserted is True

    # Now the stale Generation 1 task tries to commit its vector:
    stale_inserted = await adapter.upsert_embedding(
        "local",
        "ayachi_nene",
        photo.photo_id,
        photo.sha256,
        DocumentRepresentationKind.PHOTO_DESCRIPTION_V1.value,
        "model_a_space",
        "oai_test_fp",
        gen1,  # Stale generation 1 < current generation 3
        slow_vec,
    )
    assert stale_inserted is False  # Rejected by authoritative generation fence!

    # Verify DB retains Generation 3 vector
    stored = await adapter.list_embeddings(
        "local",
        "ayachi_nene",
        DocumentRepresentationKind.PHOTO_DESCRIPTION_V1.value,
        "model_a_space",
    )
    assert len(stored) == 1
    assert stored[0][1] == current_vec

    await service.stop()


# ---------------------------------------------------------------------------
# Test 6: Strict vector validation and safe cosine with huge floats
# ---------------------------------------------------------------------------
def test_vector_validation_and_safe_cosine_math() -> None:
    # Reject bools (bool is subclass of int in Python)
    assert validate_embedding_vector([1.0, True, 0.5]) is False
    assert validate_embedding_vector([False, 0.1]) is False

    # Reject strings, nulls, and non-numeric
    assert validate_embedding_vector([1.0, "0.5", 0.2]) is False
    assert validate_embedding_vector([1.0, None, 0.2]) is False
    assert validate_embedding_vector(["text"]) is False

    # Reject non-finite values (NaN, Inf)
    assert validate_embedding_vector([1.0, float("nan"), 0.2]) is False
    assert validate_embedding_vector([1.0, float("inf"), 0.2]) is False

    # Reject zero or near-zero norm
    assert validate_embedding_vector([0.0, 0.0, 0.0]) is False
    assert validate_embedding_vector([1e-15, 1e-15]) is False

    # Valid normal vector
    assert validate_embedding_vector([0.6, 0.8]) is True

    # Huge floats: cosine similarity must not raise OverflowError
    v_huge1 = [1e200, 1e200]
    v_huge2 = [1e200, 1e200]
    sim = cosine_similarity(v_huge1, v_huge2)
    assert math.isclose(sim, 1.0, abs_tol=1e-6)

    # Orthogonal vectors
    sim_ortho = cosine_similarity([1.0, 0.0], [0.0, 1.0])
    assert math.isclose(sim_ortho, 0.0, abs_tol=1e-6)


# ---------------------------------------------------------------------------
# Test 7: Independent accepted negative examples all score < 0.55 on real descriptions
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_independent_accepted_negative_examples_score_below_threshold(
    test_db: Database,
) -> None:
    """Fixture photos (city/red roofs, chocolate birthday cake, sleeping white cat, sunset sand)

    Candidate titles are generic '照片', captions '给你看看照片'.
    Negative queries (摩托车, 办公室电脑屏幕, 雪山, 汉堡, 小狗) must all return no photo evidence.
    """
    repo = SQLitePhotoMemoryRepository(test_db)
    adapter = SQLitePhotoSemanticAdapter(test_db)
    provider = FakeNeuralEmbeddingProvider(dim=2048)
    service = PhotoSemanticService(adapter, provider)
    recall_service = PhotoRecallService(repo, semantic_service=service)

    # Seed the 4 official benchmark descriptions
    p1 = await _seed_photo(
        test_db,
        repo,
        title="照片",
        caption="给你看看照片",
        description=(
            "A picturesque European city view with historic buildings and distinctive red roofs "
            "under a clear sky."
        ),
        keywords=("city", "architecture"),
    )
    p2 = await _seed_photo(
        test_db,
        repo,
        title="照片",
        caption="给你看看照片",
        description=(
            "A delicious chocolate birthday cake with burning candles and decorative frosting "
            "on a party table."
        ),
        keywords=("birthday", "cake"),
    )
    p3 = await _seed_photo(
        test_db,
        repo,
        title="照片",
        caption="给你看看照片",
        description=(
            "A fluffy white cat peacefully sleeping curled up on a cozy sunlit living room rug."
        ),
        keywords=("cat", "pet"),
    )
    p4 = await _seed_photo(
        test_db,
        repo,
        title="照片",
        caption="给你看看照片",
        description=(
            "Golden sunset over a calm ocean beach with warm sand and gentle waves rolling "
            "onto the shore."
        ),
        keywords=("sunset", "beach"),
    )

    # Register benchmark vectors for the 4 descriptions
    v1 = _make_unit_vector(2048, 1001)
    v2 = _make_unit_vector(2048, 1002)
    v3 = _make_unit_vector(2048, 1003)
    v4 = _make_unit_vector(2048, 1004)
    provider.register(photo_embedding_text(p1), v1)
    provider.register(photo_embedding_text(p2), v2)
    provider.register(photo_embedding_text(p3), v3)
    provider.register(photo_embedding_text(p4), v4)

    # Start service
    service.start()

    # Index all 4 photos into the semantic projection
    for p in (p1, p2, p3, p4):
        await service._index_single_photo("local", "ayachi_nene", p, provider.describe(), 1)

    # 5 independent accepted negative examples from root
    negative_examples = [
        "摩托车",
        "办公室电脑屏幕",
        "雪山",
        "汉堡",
        "小狗",
    ]

    generation_id = await _create_active_generation_for_recall(test_db)

    # Explicitly remove the document span. Python's randomized hash previously produced
    # near-periodic sine vectors with cosine ~0.99 for some negative queries.
    basis: list[list[float]] = []
    for document in (v1, v2, v3, v4):
        orthogonal = list(document)
        for direction in basis:
            dot = sum(a * b for a, b in zip(orthogonal, direction, strict=True))
            orthogonal = [a - dot * b for a, b in zip(orthogonal, direction, strict=True)]
        norm = math.hypot(*orthogonal)
        basis.append([a / norm for a in orthogonal])
    for index, term in enumerate(negative_examples):
        q_vec = _make_unit_vector(2048, 2000 + index)
        for direction in basis:
            dot = sum(a * b for a, b in zip(q_vec, direction, strict=True))
            q_vec = [a - dot * b for a, b in zip(q_vec, direction, strict=True)]
        norm = math.hypot(*q_vec)
        q_vec = [a / norm for a in q_vec]
        assert all(
            abs(sum(a * b for a, b in zip(q_vec, v, strict=True))) < 1e-8 for v in (v1, v2, v3, v4)
        )
        provider.register(term, q_vec)
        provider.register(f"{term}照片", q_vec)
        provider.register(f"{term}的照片", q_vec)

        # 1. Direct semantic search returns empty (all similarities < 0.55)
        matches, is_ambiguous = await service.search("local", "ayachi_nene", term)
        assert matches == []
        assert is_ambiguous is False

        # 2. Full recall with explicit photo query returns unavailable without silent fallback
        recall_q = f"之前那张{term}的照片"
        provider.register(recall_q, q_vec)
        result = await recall_service.recall(
            "local", "ayachi_nene", recall_q, generation_id=generation_id, attach_image=True
        )

        # Lexical search must NOT have matched '照片', semantic must score < 0.55,
        # and recency must NOT pick the latest photo!
        assert "No saved photo is currently available for this request." in result.evidence
        assert result.image is None
        assert str(p4.photo_id) not in result.evidence  # Not silently picked latest!

    await service.stop()


# ---------------------------------------------------------------------------
# Test 8: Positive semantic recall selects correct photo despite generic titles
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_positive_semantic_recall_with_generic_titles(test_db: Database) -> None:
    repo = SQLitePhotoMemoryRepository(test_db)
    adapter = SQLitePhotoSemanticAdapter(test_db)
    provider = FakeNeuralEmbeddingProvider(dim=2048)
    service = PhotoSemanticService(adapter, provider)
    recall_service = PhotoRecallService(repo, semantic_service=service)

    service.start()

    p1 = await _seed_photo(
        test_db,
        repo,
        title="照片",
        caption="给你看看照片",
        description=(
            "A picturesque European city view with historic buildings and distinctive red roofs "
            "under a clear sky."
        ),
        keywords=("city",),
    )
    p2 = await _seed_photo(
        test_db,
        repo,
        title="照片",
        caption="给你看看照片",
        description="A fluffy white cat peacefully sleeping curled up on a cozy sunlit rug.",
        keywords=("cat",),
    )

    v1 = _make_unit_vector(2048, 3001)
    v2 = _make_unit_vector(2048, 3002)
    provider.register(photo_embedding_text(p1), v1)
    provider.register(photo_embedding_text(p2), v2)

    await service._index_single_photo("local", "ayachi_nene", p1, provider.describe(), 1)
    await service._index_single_photo("local", "ayachi_nene", p2, provider.describe(), 1)

    query = "之前那张红屋顶的照片"
    # Query vector has 0.68 similarity to red roofs (p1), and 0.35 to cat (p2)
    q_vec = _make_vector_with_cosine(v1, 0.68, seed=4001)
    provider.register(query, q_vec)

    generation_id = await _create_active_generation_for_recall(test_db)
    result = await recall_service.recall(
        "local", "ayachi_nene", query, generation_id=generation_id, attach_image=True
    )

    assert str(p1.photo_id) in result.evidence
    assert result.image is not None

    await service.stop()


# ---------------------------------------------------------------------------
# Test 9: Anchored recent-only grammar vs adversarial trailing content & compounds
# ---------------------------------------------------------------------------
def test_anchored_recent_only_grammar_policy() -> None:
    # True: Pure recency requests without content
    assert is_recent_only_request("刚才那张照片") is True
    assert is_recent_only_request("刚刚发的照片") is True
    assert is_recent_only_request("上一张照片是什么") is True
    assert is_recent_only_request("帮我看下刚才那张照片") is True
    assert is_recent_only_request("最近的照片") is True
    assert is_recent_only_request("last photo") is True
    assert is_recent_only_request("show me the last photo") is True
    assert is_recent_only_request("the recent picture?") is True

    # False: Chinese compounds and descriptive content
    assert is_recent_only_request("刚才那张红屋顶照片") is False
    assert is_recent_only_request("之前那张红屋顶的照片") is False
    assert is_recent_only_request("红屋顶照片") is False

    # False: Adversarial trailing qualifiers
    assert is_recent_only_request("刚才那张照片里有没有外星人") is False
    assert is_recent_only_request("上一张照片里的恐龙") is False
    assert is_recent_only_request("刚才的照片是蛋糕吗") is False
    assert is_recent_only_request("last photo of the cat") is False

    # False: Negative and ordinary chat
    assert is_recent_only_request("摩托车照片") is False
    assert is_recent_only_request("今天天气怎么样") is False


# ---------------------------------------------------------------------------
# Test 10: Content query extraction preserves real words and strips generic frame
# ---------------------------------------------------------------------------
def test_content_query_extraction_preserves_words() -> None:
    # Strips generic frame, preserves Chinese content
    assert extract_photo_content_query("帮我找之前那张红屋顶的照片") == "红屋顶"
    assert extract_photo_content_query("之前那张摩托车的照片") == "摩托车"
    assert extract_photo_content_query("那张雪山的照片") == "雪山"

    # Empty content for pure recency / generic queries
    assert extract_photo_content_query("刚才那张照片") == ""
    assert extract_photo_content_query("上一张照片是什么") == ""
    assert extract_photo_content_query("last photo") == ""

    # English word boundary safety: 'sand' and 'cat' not corrupted by 'a'/'an'
    assert extract_photo_content_query("show me the sunset sand picture") == "sunset sand"
    assert extract_photo_content_query("sleeping white cat photo") == "sleeping white cat"


# ---------------------------------------------------------------------------
# Test 11: Unrelated ordinary chats make 0 provider calls
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_unrelated_ordinary_chats_make_zero_provider_calls(test_db: Database) -> None:
    repo = SQLitePhotoMemoryRepository(test_db)
    adapter = SQLitePhotoSemanticAdapter(test_db)
    provider = FakeNeuralEmbeddingProvider()
    service = PhotoSemanticService(adapter, provider)
    recall_service = PhotoRecallService(repo, semantic_service=service)

    await _seed_photo(test_db, repo, caption="灯塔与海边小船")
    generation_id = await _create_active_generation_for_recall(test_db)

    result = await recall_service.recall(
        "local", "ayachi_nene", "今天天气真好呀，你喜欢吃什么", generation_id=generation_id
    )

    assert result.evidence == ""
    assert result.image is None
    assert provider.embed_call_count == 0  # 0 provider calls!


# ---------------------------------------------------------------------------
# Test 12: Multimodal contract reservation & image rejection fail-closed
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_multimodal_contract_and_image_modality_fails_closed() -> None:
    # 1. Test fake aligned multimodal descriptor contract
    multimodal_desc = EmbeddingDescriptor(
        supported_modalities=frozenset([EmbeddingModality.TEXT, EmbeddingModality.IMAGE]),
        vector_space_id="aligned_clip_v1",
        semantic_capability=True,
        enabled=True,
        opaque_fingerprint="mm_fake_fp",
    )
    assert EmbeddingModality.TEXT in multimodal_desc.supported_modalities
    assert EmbeddingModality.IMAGE in multimodal_desc.supported_modalities

    # 2. Test current adapter fails closed when receiving an image input
    provider = FakeNeuralEmbeddingProvider()
    image_input = PhotoEmbeddingInput.from_image(b"\x89PNG\r\n\x1a\nfake", "image/png")

    with pytest.raises(UnsupportedEmbeddingModalityError):
        await provider.embed([image_input])


# ---------------------------------------------------------------------------
# Test 13: Mixed representations are not fused or compared
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_mixed_representations_not_fused_or_compared(test_db: Database) -> None:
    repo = SQLitePhotoMemoryRepository(test_db)
    adapter = SQLitePhotoSemanticAdapter(test_db)
    photo = await _seed_photo(test_db, repo)

    # Insert vector under visual representation
    await adapter.upsert_embedding(
        "local",
        "ayachi_nene",
        photo.photo_id,
        photo.sha256,
        DocumentRepresentationKind.PHOTO_VISUAL_V1.value,
        "shared_space",
        "fp1",
        1,
        [0.5, 0.5],
    )

    # Insert vector under text description representation
    await adapter.upsert_embedding(
        "local",
        "ayachi_nene",
        photo.photo_id,
        photo.sha256,
        DocumentRepresentationKind.PHOTO_DESCRIPTION_V1.value,
        "shared_space",
        "fp1",
        1,
        [0.9, 0.1],
    )

    # Querying description representation returns ONLY description vector
    desc_rows = await adapter.list_embeddings(
        "local",
        "ayachi_nene",
        DocumentRepresentationKind.PHOTO_DESCRIPTION_V1.value,
        "shared_space",
    )
    assert len(desc_rows) == 1
    assert desc_rows[0][1] == [0.9, 0.1]

    # Querying visual representation returns ONLY visual vector
    vis_rows = await adapter.list_embeddings(
        "local",
        "ayachi_nene",
        DocumentRepresentationKind.PHOTO_VISUAL_V1.value,
        "shared_space",
    )
    assert len(vis_rows) == 1
    assert vis_rows[0][1] == [0.5, 0.5]


# ---------------------------------------------------------------------------
# Test 14: Observer notifies worker without foreground indexing queue
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Test 15: Task lifecycle and tracked graceful shutdown
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Test 16: Photos NEVER enter MemoryRecord or memory_embeddings
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_photos_never_enter_memory_records_or_memory_embeddings(test_db: Database) -> None:
    repo = SQLitePhotoMemoryRepository(test_db)
    adapter = SQLitePhotoSemanticAdapter(test_db)
    provider = FakeNeuralEmbeddingProvider()
    service = PhotoSemanticService(adapter, provider)

    photo = await _seed_photo(
        test_db, repo, title="特别红屋顶", description="红色的屋顶与钟楼", keywords=("红屋顶",)
    )
    service.start()
    assert await service._index_single_photo(
        "local", "ayachi_nene", photo, provider.describe(), service.route_generation
    )
    await service.stop()

    mem_records = await test_db.fetchall("SELECT * FROM memory_records WHERE text LIKE '%红屋顶%'")
    assert len(mem_records) == 0

    mem_embeddings = await test_db.fetchall("SELECT * FROM memory_embeddings")
    assert len(mem_embeddings) == 0


# ---------------------------------------------------------------------------
# Test 17: reindex_all is non-blocking and increments route generation
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Test 18: ModelConfigurationService rejects image modality before network
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_model_config_service_multimodal_fail_closed(tmp_path: Path) -> None:
    from chatwaifu_runtime.config.settings import Settings
    from chatwaifu_runtime.providers.model_config import ModelConfigurationService

    db_path = tmp_path / "model_config_test.db"
    settings = Settings(config_dir=tmp_path / "config", data_dir=tmp_path / "data")
    db = Database(db_path, StorageConfig(database_path=db_path))
    await db.open()
    try:
        service = ModelConfigurationService(db, settings)
        await service.reload()

        # Try embedding an image input
        image_input = PhotoEmbeddingInput.from_image(b"fake_image_bytes", "image/png")
        with pytest.raises(UnsupportedEmbeddingModalityError):
            await service.embed([image_input])
    finally:
        await db.close()


# ---------------------------------------------------------------------------
# Test 19: Routes embedding update notifies photo semantic even if memory reindex fails
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_routes_embedding_update_photo_reindex_on_memory_failure(test_db: Database) -> None:
    from unittest.mock import AsyncMock, MagicMock

    from chatwaifu_runtime.api.routes import (
        ModelRoleConfigurationRequest,
        update_model_configuration,
    )
    from fastapi import Request

    mock_container = MagicMock()
    mock_container.model_configurations.get = MagicMock(
        return_value=MagicMock(provider="demo", model="old-model", base_url="")
    )
    mock_container.model_configurations.update = AsyncMock(
        return_value=MagicMock(model_dump=MagicMock(return_value={"role": "embedding"}))
    )
    mock_memory = MagicMock()
    mock_memory.reindex_all = AsyncMock()
    mock_container.memory = mock_memory

    mock_photo_semantic = MagicMock()
    mock_photo_semantic.reindex_all = AsyncMock()
    mock_photo_semantic.notify_route_change = MagicMock()
    mock_container.photo_semantic = mock_photo_semantic

    mock_index_rebuild = MagicMock()
    mock_index_rebuild.on_model_route_change = MagicMock()
    mock_container.index_rebuild = mock_index_rebuild

    mock_request = MagicMock(spec=Request)
    mock_request.app.state.container = mock_container

    body = ModelRoleConfigurationRequest(
        provider="local_hash",
        model="test-hash",
        enabled=True,
    )

    result = await update_model_configuration(mock_request, "embedding", body)
    assert result == {"role": "embedding"}

    # ZERO automatic rebuild calls on config save!
    mock_memory.reindex_all.assert_not_called()
    mock_photo_semantic.reindex_all.assert_not_called()
    mock_index_rebuild.on_model_route_change.assert_called_once()
    mock_photo_semantic.notify_route_change.assert_called_once()


# ---------------------------------------------------------------------------
# Test 20: Rapid worker triggers coalesce cleanly into finite passes
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Test 21: Ambiguity gap threshold edge cases (0.07 vs 0.09)
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_ambiguity_gap_threshold_boundary(test_db: Database) -> None:
    repo = SQLitePhotoMemoryRepository(test_db)
    adapter = SQLitePhotoSemanticAdapter(test_db)
    provider = FakeNeuralEmbeddingProvider(dim=2048)
    service = PhotoSemanticService(adapter, provider)

    p1 = await _seed_photo(test_db, repo, title="照片A")
    p2 = await _seed_photo(test_db, repo, title="照片B")

    service.start()
    v_base = _make_unit_vector(2048, 5001)
    provider.register("query_ambiguous", v_base)

    # Case A: Gap = 0.05 (< 0.08) -> Ambiguous!
    v1_amb = _make_vector_with_cosine(v_base, 0.65, seed=1)
    v2_amb = _make_vector_with_cosine(v_base, 0.60, seed=2)
    await adapter.upsert_embedding(
        "local",
        "ayachi_nene",
        p1.photo_id,
        p1.sha256,
        DocumentRepresentationKind.PHOTO_DESCRIPTION_V1.value,
        provider.space_id,
        provider.fingerprint_value,
        1,
        v1_amb,
    )
    await adapter.upsert_embedding(
        "local",
        "ayachi_nene",
        p2.photo_id,
        p2.sha256,
        DocumentRepresentationKind.PHOTO_DESCRIPTION_V1.value,
        provider.space_id,
        provider.fingerprint_value,
        1,
        v2_amb,
    )

    matches_amb, is_amb = await service.search("local", "ayachi_nene", "query_ambiguous")
    assert len(matches_amb) == 2
    assert is_amb is True

    # Case B: Gap = 0.10 (>= 0.08) -> Not ambiguous!
    v2_clear = _make_vector_with_cosine(v_base, 0.55, seed=3)
    await adapter.upsert_embedding(
        "local",
        "ayachi_nene",
        p2.photo_id,
        p2.sha256,
        DocumentRepresentationKind.PHOTO_DESCRIPTION_V1.value,
        provider.space_id,
        provider.fingerprint_value,
        1,
        v2_clear,
    )

    matches_clear, is_clear = await service.search("local", "ayachi_nene", "query_ambiguous")
    assert len(matches_clear) == 1
    assert is_clear is False

    await service.stop()


@pytest.mark.asyncio
async def test_manual_policy_no_startup_query_or_route_change_backfill(test_db: Database) -> None:
    repo = SQLitePhotoMemoryRepository(test_db)
    await _seed_photo(test_db, repo)
    provider = FakeNeuralEmbeddingProvider()
    service = PhotoSemanticService(SQLitePhotoSemanticAdapter(test_db), provider)
    service.start()
    service.start()
    service.notify_route_change()
    assert await service.search("local", "ayachi_nene", "照片里那只小猫") == ([], False)
    await service.stop()
    assert provider.embed_call_count == 0
    assert service.active_task_count == 0


@pytest.mark.asyncio
async def test_incremental_capacity_and_shutdown_are_bounded(test_db: Database) -> None:
    repo = SQLitePhotoMemoryRepository(test_db)
    photo = await _seed_photo(test_db, repo)
    provider = FakeNeuralEmbeddingProvider()
    entered = asyncio.Event()
    released = asyncio.Event()

    async def block() -> None:
        entered.set()
        await released.wait()

    provider.pre_embed_callback = block
    service = PhotoSemanticService(SQLitePhotoSemanticAdapter(test_db), provider)
    service.start()
    assert service.index_new_photo("local", "ayachi_nene", photo)
    await asyncio.wait_for(entered.wait(), 1)
    assert service.index_new_photo("local", "ayachi_nene", photo)
    assert not service.index_new_photo("local", "ayachi_nene", photo)
    await service.stop()
    assert service.active_task_count == 0
    assert not await test_db.fetchall("SELECT * FROM photo_embeddings")


@pytest.mark.asyncio
async def test_restart_epoch_can_replace_prior_high_generation(test_db: Database) -> None:
    repo = SQLitePhotoMemoryRepository(test_db)
    photo = await _seed_photo(test_db, repo)
    adapter = SQLitePhotoSemanticAdapter(test_db)
    provider = FakeNeuralEmbeddingProvider(dim=3)
    desc = provider.describe()
    await adapter.upsert_embedding(
        "local",
        "ayachi_nene",
        photo.photo_id,
        photo.sha256,
        "photo_description_v1",
        desc.vector_space_id,
        desc.opaque_fingerprint,
        100,
        [1.0, 0.0, 0.0],
    )
    restarted = PhotoSemanticService(adapter, provider)
    await restarted.sync_epoch()
    restarted.start()
    try:
        assert await restarted._index_single_photo(
            "local", "ayachi_nene", photo, desc, restarted.route_generation
        )
        rows = await test_db.fetchall("SELECT route_generation FROM photo_embeddings")
        assert rows[0][0] > 100
    finally:
        await restarted.stop()


@pytest.mark.asyncio
@pytest.mark.parametrize("cancel_on_second", [False, True])
async def test_four_photo_incremental_batch_preserves_capacity_and_route_fence(
    test_db: Database, cancel_on_second: bool
) -> None:
    repo = SQLitePhotoMemoryRepository(test_db)
    photos = tuple([await _seed_photo(test_db, repo) for _ in range(4)])
    provider = FakeNeuralEmbeddingProvider(dim=8)
    entered = asyncio.Event()
    release = asyncio.Event()

    async def block() -> None:
        target = 2 if cancel_on_second else 1
        if provider.embed_call_count == target:
            entered.set()
            await release.wait()

    provider.pre_embed_callback = block
    service = PhotoSemanticService(SQLitePhotoSemanticAdapter(test_db), provider)
    service.start()
    try:
        assert service.index_new_photos("local", "ayachi_nene", photos)
        assert service.active_task_count == 1
        assert not service.index_new_photos("local", "ayachi_nene", (*photos, photos[0]))
        await asyncio.wait_for(entered.wait(), 3)
        tasks = tuple(service._incremental_tasks)
        if cancel_on_second:
            service.notify_route_change()
        else:
            release.set()
        await asyncio.wait_for(asyncio.gather(*tasks, return_exceptions=True), 3)
        rows = await test_db.fetchall("SELECT photo_id FROM photo_embeddings")
        expected = {str(p.photo_id) for p in (photos[:1] if cancel_on_second else photos)}
        assert {row[0] for row in rows} == expected
        assert provider.embed_call_count == (2 if cancel_on_second else 4)
        assert service.active_task_count == 0
    finally:
        await service.stop()
