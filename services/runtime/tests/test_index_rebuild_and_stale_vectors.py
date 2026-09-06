"""Tests for index rebuild orchestration, stale text vector searchability, and fences."""

from __future__ import annotations

import asyncio
import io
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast
from uuid import UUID, uuid4

import pytest
from chatwaifu_protocol.memory import MemoryRecord, PrivacyLevel
from chatwaifu_protocol.photo_memory import SavedPhoto
from chatwaifu_runtime.config.settings import StorageConfig
from chatwaifu_runtime.index_orchestration.contracts import (
    DomainRebuildState,
    OverallRebuildState,
)
from chatwaifu_runtime.index_orchestration.service import IndexRebuildService
from chatwaifu_runtime.memory.semantic_index import SQLiteSemanticMemoryIndex
from chatwaifu_runtime.persistence.database import Database
from chatwaifu_runtime.persistence.sqlite_memory_repository import SQLiteMemoryRepository
from chatwaifu_runtime.persistence.sqlite_photo_memory import SQLitePhotoMemoryRepository
from chatwaifu_runtime.persistence.sqlite_photo_semantic import SQLitePhotoSemanticAdapter
from chatwaifu_runtime.photo_memory.models import PhotoSaveCandidate
from chatwaifu_runtime.photo_memory.ports import (
    DocumentRepresentationKind,
    EmbeddingDescriptor,
    EmbeddingModality,
    PhotoEmbeddingInput,
)
from chatwaifu_runtime.photo_memory.semantic import PhotoSemanticService
from PIL import Image


def make_test_png() -> bytes:
    img = Image.new("RGB", (1, 1), (255, 0, 0))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


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
            ) VALUES (?, ?, 'user', 'query', ?, ?, ?)
            """,
            (turn_id, session_id, now, gen_id, sc_json),
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



async def _seed_memory_record(
    test_db: Database,
    *,
    text: str = "Test memory",
    namespace: str = "character/default/user/local",
    state: str = "active",
) -> MemoryRecord:
    session_id = str(uuid4())
    turn_id = str(uuid4())
    event_id = str(uuid4())
    mem_id = uuid4()
    source_id = uuid4()
    now = datetime.now(UTC).isoformat()
    sc_json = json.dumps(
        {
            "provider_id": "test",
            "connection_id": str(uuid4()),
            "principal_scope": "local",
            "chat_type": "direct",
            "conversation_key": "k",
            "sender_key": "s",
        }
    )
    async with test_db.transaction() as conn:
        await conn.execute(
            """
            INSERT INTO sessions (
                session_id, character_id, state,
                conversation_state, revision, next_sequence,
                created_at, updated_at
            ) VALUES (?, 'default', 'ready', 'idle', 0, 1, ?, ?)
            """,
            (session_id, now, now),
        )
        await conn.execute(
            """
            INSERT INTO turns (
                turn_id, session_id, role, committed_text,
                created_at, source_context_json
            ) VALUES (?, ?, 'user', ?, ?, ?)
            """,
            (turn_id, session_id, text, now, sc_json),
        )
        await conn.execute(
            """
            INSERT INTO events (
                event_id, session_id, sequence, event_type, schema_version,
                occurred_at, source, payload_json, envelope_json
            ) VALUES (?, ?, 1, 'user.turn_committed', '1.0', ?, 'test', '{}', '{}')
            """,
            (event_id, session_id, now),
        )
        await conn.execute(
            """
            INSERT INTO memory_records(
                memory_id, namespace, kind, subject_id, predicate, value_json,
                text, normalized_text, search_terms, observed_at, valid_from,
                confidence, importance, sensitivity, state, pinned,
                created_at, updated_at
            ) VALUES (
                ?, ?, 'semantic.fact', 'user',
                'preference', 'null', ?,
                ?, ?, ?, ?,
                0.9, 0.8, 'private', ?, 0, ?, ?
            )
            """,
            (
                str(mem_id),
                namespace,
                text,
                text.lower(),
                text.lower(),
                now,
                now,
                state,
                now,
                now,
            ),
        )
        await conn.execute(
            """
            INSERT INTO memory_sources(
                source_id, memory_id, source_event_id, session_id, turn_id,
                source_kind, created_at
            ) VALUES (?, ?, ?, ?, ?, 'user_turn', ?)
            """,
            (str(source_id), str(mem_id), str(event_id), session_id, turn_id, now),
        )
    return MemoryRecord(
        memory_id=mem_id,
        namespace=namespace,
        kind="semantic.fact",
        subject_id="user",
        predicate="preference",
        value=None,
        text=text,
        source_event_ids=[UUID(event_id)],
        observed_at=datetime.fromisoformat(now),
        valid_from=datetime.fromisoformat(now),
        confidence=0.9,
        importance=0.8,
        sensitivity=PrivacyLevel.PRIVATE,
        state=state,  # type: ignore[arg-type]
        created_at=datetime.fromisoformat(now),
        updated_at=datetime.fromisoformat(now),
    )



@pytest.fixture
async def test_db(tmp_path: Path):
    db_path = tmp_path / "test_rebuild.db"
    storage = StorageConfig(database_path=db_path)
    db = Database(db_path, storage)
    await db.open()
    try:
        yield db
    finally:
        await db.close()


class FakeEmbeddingService:
    def __init__(
        self,
        vector_space_id: str = "space_a",
        dimension: int = 4,
        fingerprint: str = "fp_a",
        semantic_capability: bool = True,
    ) -> None:
        self.vector_space_id = vector_space_id
        self.dimension = dimension
        self.fingerprint = fingerprint
        self.semantic_capability = semantic_capability
        self.embed_calls: list[list[PhotoEmbeddingInput | str]] = []

    def describe(self) -> EmbeddingDescriptor:
        return EmbeddingDescriptor(
            supported_modalities=frozenset([EmbeddingModality.TEXT]),
            vector_space_id=self.vector_space_id,
            semantic_capability=self.semantic_capability,
            enabled=True,
            opaque_fingerprint=self.fingerprint,
        )

    def embedding_fingerprint(self) -> str:
        return self.fingerprint

    async def embed(self, inputs: Any) -> list[list[float]]:
        self.embed_calls.append(list(inputs))
        vectors: list[list[float]] = []
        for item in inputs:
            text = (item.text or "") if isinstance(item, PhotoEmbeddingInput) else str(item)
            val = float(len(text) % 10 + 1)
            vec = [val] * self.dimension
            norm = sum(x * x for x in vec) ** 0.5
            vectors.append([x / norm for x in vec])
        return vectors


@pytest.mark.asyncio
async def test_stale_same_dim_text_vectors_participate_in_photo_search(test_db: Database) -> None:
    """Stale TEXT vectors of the SAME dimension still participate in search as user accepted."""
    repo = SQLitePhotoMemoryRepository(test_db)
    adapter = SQLitePhotoSemanticAdapter(test_db)
    photo = await _seed_photo(test_db, repo, title="red roof", description="red roof house")

    # Old model generated a 4-dim vector in "space_old"
    old_vector = [0.5, 0.5, 0.5, 0.5]
    await adapter.upsert_embedding(
        "local",
        "ayachi_nene",
        photo.photo_id,
        photo.sha256,
        DocumentRepresentationKind.PHOTO_DESCRIPTION_V1.value,
        "space_old",
        "fp_old",
        1,
        old_vector,
    )

    # Now user switches embedding model to new model (also 4-dim, but "space_new")
    new_provider = FakeEmbeddingService(
        vector_space_id="space_new", dimension=4, fingerprint="fp_new"
    )
    service = PhotoSemanticService(adapter, cast(Any, new_provider))
    service.start()

    # Query uses the new model, but matches the old stored vector
    matches, ambiguous = await service.search("local", "ayachi_nene", "red roof house")
    assert len(matches) == 1
    assert matches[0].photo_id == photo.photo_id
    assert matches[0].score >= 0.55
    assert ambiguous is False


@pytest.mark.asyncio
async def test_incompatible_dimensions_skipped_in_photo_search(test_db: Database) -> None:
    """Old model vectors with incompatible dimensions are skipped without crashing."""
    repo = SQLitePhotoMemoryRepository(test_db)
    adapter = SQLitePhotoSemanticAdapter(test_db)
    photo = await _seed_photo(test_db, repo)

    # Old model generated a 3-dim vector
    old_vector = [0.577, 0.577, 0.577]
    await adapter.upsert_embedding(
        "local",
        "ayachi_nene",
        photo.photo_id,
        photo.sha256,
        DocumentRepresentationKind.PHOTO_DESCRIPTION_V1.value,
        "space_old",
        "fp_old",
        1,
        old_vector,
    )

    # New model produces 4-dim vectors
    new_provider = FakeEmbeddingService(
        vector_space_id="space_new", dimension=4, fingerprint="fp_new"
    )
    service = PhotoSemanticService(adapter, cast(Any, new_provider))
    service.start()

    # Query has 4 dimensions; 3-dim stored vector must be skipped!
    matches, _ = await service.search("local", "ayachi_nene", "red roof")
    assert len(matches) == 0


@pytest.mark.asyncio
async def test_stale_same_dim_vectors_participate_in_memory_search(test_db: Database) -> None:
    """Structured memory semantic search allows same-dimensional stale vectors to participate."""
    provider = FakeEmbeddingService(vector_space_id="space_old", dimension=4, fingerprint="fp_old")
    mem_index = SQLiteSemanticMemoryIndex(test_db, cast(Any, provider))

    # Save memory record under old model
    rec = await _seed_memory_record(test_db, text="I love matcha latte")
    await mem_index.upsert(rec)

    # Switch provider to fp_new (same 4-dim)
    provider.fingerprint = "fp_new"
    provider.vector_space_id = "space_new"

    # Search with new model must still find the old vector
    results = await mem_index.search("matcha latte", [rec.namespace], limit=5)
    assert len(results) == 1
    assert results[0].memory_id == rec.memory_id
    assert results[0].score > 0.0


@pytest.mark.asyncio
async def test_incompatible_dimension_skipped_in_memory_search(test_db: Database) -> None:
    """Structured memory skips vectors of mismatched dimensions."""
    provider = FakeEmbeddingService(vector_space_id="space_old", dimension=3, fingerprint="fp_old")
    mem_index = SQLiteSemanticMemoryIndex(test_db, cast(Any, provider))

    rec = await _seed_memory_record(test_db, text="Likes cats")
    await mem_index.upsert(rec)

    # Switch provider to 5-dim
    provider.dimension = 5
    provider.fingerprint = "fp_new"

    results = await mem_index.search("cats", [rec.namespace], limit=5)
    assert len(results) == 0


@pytest.mark.asyncio
async def test_index_rebuild_singleflight_and_both_domains(test_db: Database) -> None:
    """Manual rebuild orchestrates both memory and photo domains in singleflight."""
    repo = SQLitePhotoMemoryRepository(test_db)
    adapter = SQLitePhotoSemanticAdapter(test_db)
    mem_repo = SQLiteMemoryRepository(test_db)
    provider = FakeEmbeddingService(
        vector_space_id="space_active", dimension=4, fingerprint="fp_active"
    )
    mem_index = SQLiteSemanticMemoryIndex(test_db, cast(Any, provider))
    photo_sem = PhotoSemanticService(adapter, cast(Any, provider))

    # Seed 2 memory records and 1 photo
    await _seed_memory_record(test_db, text="Coffee lover")
    await _seed_memory_record(test_db, text="Tea lover")
    await _seed_photo(test_db, repo)

    orchestrator = IndexRebuildService(
        cast(Any, provider),
        mem_repo,
        mem_index,
        repo,
        photo_sem,
        adapter,
    )

    # Start rebuild
    status = await orchestrator.start_rebuild()
    assert status.state == OverallRebuildState.RUNNING

    # Calling start_rebuild again while running returns same status (singleflight!)
    status_dup = await orchestrator.start_rebuild()
    assert status_dup.job_id == status.job_id

    # Wait for completion
    for _ in range(50):
        await asyncio.sleep(0.05)
        current = orchestrator.get_status()
        if current.state in (OverallRebuildState.COMPLETED, OverallRebuildState.FAILED):
            break

    final_status = orchestrator.get_status()
    assert final_status.state == OverallRebuildState.COMPLETED
    assert final_status.domains["memory"].state == DomainRebuildState.COMPLETED
    assert final_status.domains["memory"].indexed_count >= 1
    assert final_status.domains["memory"].failed_count == 0
    assert final_status.domains["photo"].state == DomainRebuildState.COMPLETED
    assert final_status.domains["photo"].indexed_count == 1
    assert final_status.domains["photo"].failed_count == 0


@pytest.mark.asyncio
async def test_deleted_records_not_resurrected_during_rebuild(test_db: Database) -> None:
    """Records or photos deleted during rebuild cannot be resurrected in projection."""
    repo = SQLitePhotoMemoryRepository(test_db)
    adapter = SQLitePhotoSemanticAdapter(test_db)
    mem_repo = SQLiteMemoryRepository(test_db)

    # Provider delays embedding to simulate slow network call
    class SlowEmbeddingProvider(FakeEmbeddingService):
        async def embed(self, inputs: Any) -> list[list[float]]:
            await asyncio.sleep(0.1)
            return await super().embed(inputs)

    provider = SlowEmbeddingProvider(
        vector_space_id="space_active", dimension=4, fingerprint="fp_active"
    )
    mem_index = SQLiteSemanticMemoryIndex(test_db, cast(Any, provider))
    photo_sem = PhotoSemanticService(adapter, cast(Any, provider))

    photo = await _seed_photo(test_db, repo)
    mem_rec = await _seed_memory_record(test_db, text="Delete me")
    mem_id = mem_rec.memory_id

    orchestrator = IndexRebuildService(
        cast(Any, provider),
        mem_repo,
        mem_index,
        repo,
        photo_sem,
        adapter,
    )

    # Start rebuild task
    await orchestrator.start_rebuild()

    # Immediately delete both memory record and photo before slow embedding finishes
    async with test_db.transaction() as conn:
        await conn.execute(
            "UPDATE memory_records SET state = 'tombstoned' WHERE memory_id = ?",
            (str(mem_id),),
        )
        await conn.execute("DELETE FROM photo_assets WHERE photo_id = ?", (str(photo.photo_id),))

    # Wait for completion
    for _ in range(50):
        await asyncio.sleep(0.05)
        current = orchestrator.get_status()
        if current.state in (OverallRebuildState.COMPLETED, OverallRebuildState.FAILED):
            break

    # Verify no embeddings exist for the deleted photo or tombstoned memory
    photo_embeddings = await adapter.list_embeddings(
        "local", "ayachi_nene", DocumentRepresentationKind.PHOTO_DESCRIPTION_V1.value
    )
    assert not any(p_id == photo.photo_id for p_id, _ in photo_embeddings)

    async with test_db.transaction() as conn:
        cursor = await conn.execute(
            "SELECT * FROM memory_embeddings WHERE memory_id = ?", (str(mem_id),)
        )
        rows = list(await cursor.fetchall())
        assert len(rows) == 0


@pytest.mark.asyncio
async def test_model_route_change_cancels_running_rebuild_safely(test_db: Database) -> None:
    """Changing embedding model route during rebuild cancels old job safely."""
    repo = SQLitePhotoMemoryRepository(test_db)
    adapter = SQLitePhotoSemanticAdapter(test_db)
    mem_repo = SQLiteMemoryRepository(test_db)

    class HangingEmbeddingProvider(FakeEmbeddingService):
        async def embed(self, inputs: Any) -> list[list[float]]:
            await asyncio.sleep(10.0)
            return await super().embed(inputs)

    provider = HangingEmbeddingProvider(
        vector_space_id="space_initial", dimension=4, fingerprint="fp_initial"
    )
    mem_index = SQLiteSemanticMemoryIndex(test_db, cast(Any, provider))
    photo_sem = PhotoSemanticService(adapter, cast(Any, provider))

    await _seed_photo(test_db, repo)
    orchestrator = IndexRebuildService(
        cast(Any, provider),
        mem_repo,
        mem_index,
        repo,
        photo_sem,
        adapter,
    )

    await orchestrator.start_rebuild()
    assert orchestrator.get_status().state == OverallRebuildState.RUNNING

    # User changes model route
    provider.fingerprint = "fp_switched"
    provider.vector_space_id = "space_switched"
    orchestrator.on_model_route_change()

    assert orchestrator.get_status().state == OverallRebuildState.CANCELLED
