# ruff: noqa: RUF001
"""Regression and unit tests for memory recall intent and bounded recent padding.

Verifies:
- Deterministic full-query predicate `is_broad_inventory_query` behavior across zh/en,
  case, punctuation, whitespace, negations, quotes, multi-intent, and topical negatives.
- Genuine `MemoryRetriever` behavior with real SQLite repository and unrelated recent distractors.
- Topical one-hit and no-hit queries do NOT leak recent distractor records (FP=0).
- Broad inventory queries legitimately supplement recent records (both with 0 hits and 1 valid hit).
- Hybrid contributions (FTS, semantic, temporal, pinned).
- Policy, tombstoned (deleted), and superseded (stale) filtering invariants.
- Context token budget truncation under budget pressure.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from chatwaifu_protocol.base import PrivacyLevel
from chatwaifu_protocol.events import (
    UserTurnCommittedEvent,
    UserTurnCommittedPayload,
)
from chatwaifu_protocol.memory import MemoryRecord, MemorySource
from chatwaifu_runtime.config.settings import StorageConfig
from chatwaifu_runtime.memory.policy import MemoryPolicy
from chatwaifu_runtime.memory.ports import (
    NullSemanticMemoryIndex,
    NullTemporalMemoryGraph,
    ScoredMemoryReference,
)
from chatwaifu_runtime.memory.retrieval import (
    MemoryRetriever,
    is_broad_inventory_query,
)
from chatwaifu_runtime.persistence.database import Database
from chatwaifu_runtime.persistence.event_store import EventStore
from chatwaifu_runtime.persistence.sqlite_memory_repository import SQLiteMemoryRepository

_RetrieverFixture = tuple[Database, SQLiteMemoryRepository, MemoryPolicy, MemoryRetriever, UUID]


# ============================================================================
# 1. Deterministic Classifier Predicate Tests
# ============================================================================


@pytest.mark.parametrize(
    "query",
    [
        # Astra's explicit broad examples
        "你记得我什么？",
        "你还记得我的哪些信息？",
        "你记住了关于我的什么？",
        "我的喜好",
        "What do you remember about me?",
        "List my memories",
        "Tell me what you know about me",
        # Supported zh variations
        "你还记得关于我的什么",
        "你都记得我什么",
        "我的偏好",
        "我的习惯",
        "我的个人信息",
        "关于我的信息",
        "关于我的记忆",
        "关于我你记得什么？",
        "说说我的喜好",
        "说说你记得我什么",
        "列出关于我的记忆",
        "查看我的信息",
        "你记住了什么？",
        "你还记得什么？",
        "你对我有什么记忆？",
        "你对我了解什么？",
        # Supported en variations
        "what do you remember about me",
        "what do you remember",
        "what do you know about me",
        "tell me what you remember about me",
        "tell me what you remember",
        "what memories do you have of me",
        "what memories do you have about me",
        "what memories do you have",
        "list all my memories",
        "list all memories",
        "show my memories",
        "recall my memories",
        "my preferences",
        "my memories",
        "my profile",
        # Case, edge punctuation, and whitespace variations
        "  What do you remember about me?  ",
        "SHOW MY MEMORIES",
        "what do you know about me?!",
        "  你记得我什么？？  ",
        "我的喜好：",
        "List my memories...",
        "TELL ME WHAT YOU KNOW ABOUT ME",
        "What\tdo\nyou remember about me?",
    ],
)
def test_broad_inventory_query_positive_cases(query: str) -> None:
    assert is_broad_inventory_query(query) is True


@pytest.mark.parametrize(
    "query",
    [
        # Astra's explicit topical negatives
        "还记得变成冰棒了的互动梗吗 冰棒",
        "还记得我们去巴黎吗？",
        "你都记得东京行程的哪些细节？",
        "你记得我的生日吗？",
        "What do you remember about me visiting Tokyo?",
        "List my memories about Tokyo",
        # Other topical queries
        "你还记得上次说的那家火锅店吗？",
        "还记得我们上次约好的时间吗",
        "你还记得我的猫叫什么名字吗？",
        "草莓口味雪糕甜点",
        "平时喜好喝什么咖啡 黑咖啡",
        "开发操作系统 Linux",
        "最喜爱主力编程语言 Python",
        "摄影和拍照记录生活",
        "手机号码 13800138000 联系方式",
        "木白 真实姓名",
        "Do you remember the movie we watched?",
        "Remember our trip to Kyoto?",
        "Can you recall the password I mentioned?",
        "Tell me what you know about Python",
        "List memories about travel",
        "信息",
        "喜好",
        "你知道什么？",
        "你知道哪些事情？",
        "What do you know?",
        "Tell me what you know",
    ],
)
def test_broad_inventory_query_topical_negatives(query: str) -> None:
    assert is_broad_inventory_query(query) is False


@pytest.mark.parametrize(
    "query",
    [
        # Astra's explicit negation
        "我不想知道你记得我什么",
        # zh negations
        "别记得我什么",
        "不要记住我的喜好",
        "我不记得了",
        "不用记得我的信息",
        "无意回忆关于我的记忆",
        "别提我的偏好",
        # en negations
        "Don't list my memories",
        "You do not remember about me",
        "Never recall my memories",
        "Stop telling me my preferences",
        "I don't want you to remember me",
        "Cannot recall my profile",
    ],
)
def test_broad_inventory_query_negations(query: str) -> None:
    assert is_broad_inventory_query(query) is False


@pytest.mark.parametrize(
    "query",
    [
        # Quoted inventory phrase inside unrelated sentences
        "他说“你记得我什么”但我不想听",
        "把“我的喜好”作为测试暗号",
        'He asked "What do you remember about me?" casually',
        'Remember "List my memories" is a command',
        # Standalone quoted queries
        "“你记得我什么？”",
        '"What do you remember about me?"',
        "'List my memories'",
        "「我的喜好」",
        "『Tell me what you know about me』",
    ],
)
def test_broad_inventory_query_quotes(query: str) -> None:
    assert is_broad_inventory_query(query) is False


@pytest.mark.parametrize(
    "query",
    [
        # Multi-intent chaining / requests followed by unrelated tasks
        "你记得我什么？顺便帮我查一下天气",
        "我的喜好，然后再查查明天的电影",
        "Tell me what you know about me and also search for flights",
        "List my memories and then set an alarm",
        "你还记得我的哪些信息？还有明天的日程",
        "What do you remember about me? Also what time is it?",
        "Tell me what you know about me; then turn off the lights",
    ],
)
def test_broad_inventory_query_multi_intent(query: str) -> None:
    assert is_broad_inventory_query(query) is False


# ============================================================================
# 2. Genuine MemoryRetriever Integration Tests with SQLite Repository
# ============================================================================


@pytest.fixture
async def sqlite_retriever_setup(
    tmp_path: Path,
) -> AsyncIterator[_RetrieverFixture]:
    db_path = tmp_path / "test_retrieval.db"
    database = Database(db_path, StorageConfig(database_path=db_path))
    await database.open()

    session_id = uuid4()
    now_iso = datetime.now(UTC).isoformat()
    async with database.transaction() as conn:
        await conn.execute(
            """
            INSERT INTO sessions (
                session_id, character_id, user_scope, state, conversation_state,
                revision, next_sequence, created_at, updated_at
            ) VALUES (?, 'default', 'local', 'active', 'idle', 0, 1, ?, ?)
            """,
            (str(session_id), now_iso, now_iso),
        )

    repository = SQLiteMemoryRepository(database)
    policy = MemoryPolicy()
    retriever = MemoryRetriever(
        repository=repository,
        policy=policy,
        semantic_index=NullSemanticMemoryIndex(),
        temporal_graph=NullTemporalMemoryGraph(),
    )

    try:
        yield database, repository, policy, retriever, session_id
    finally:
        await database.close()


async def _insert_test_record(
    database: Database,
    repository: SQLiteMemoryRepository,
    session_id: UUID,
    *,
    memory_id: UUID | None = None,
    namespace: str = "character/default/user/local",
    kind: str = "semantic.preference",
    predicate: str = "preference.test",
    text: str,
    importance: float = 0.6,
    sensitivity: PrivacyLevel = PrivacyLevel.PRIVATE,
    state: str = "active",
    pinned: bool = False,
    supersede_target: UUID | None = None,
    observed_at: datetime | None = None,
    created_at: datetime | None = None,
) -> MemoryRecord:
    mid = memory_id or uuid4()
    sid = uuid4()
    tid = uuid4()
    now = observed_at or datetime.now(UTC)
    c_now = created_at or now

    event_store = EventStore(database)
    evt = UserTurnCommittedEvent(
        event_id=sid,
        session_id=session_id,
        turn_id=tid,
        occurred_at=now,
        source="test",
        payload=UserTurnCommittedPayload(text=text),
    )
    await event_store.append(evt)

    record = MemoryRecord(
        memory_id=mid,
        namespace=namespace,
        kind=kind,  # type: ignore[arg-type]
        subject_id="user",
        predicate=predicate,
        value={"text": text},
        text=text,
        observed_at=now,
        confidence=0.9,
        importance=importance,
        sensitivity=sensitivity,
        state=state,  # type: ignore[arg-type]
        pinned=pinned,
        supersedes=supersede_target,
        created_at=c_now,
        updated_at=c_now,
        source_event_ids=[sid],
    )
    source = MemorySource(
        source_id=uuid4(),
        memory_id=mid,
        source_event_id=sid,
        session_id=session_id,
        source_kind="user_turn",
        created_at=c_now,
    )
    await repository.create_record(record, [source], supersede_target=supersede_target)
    return record


@pytest.mark.asyncio
async def test_topical_one_hit_suppresses_recent_distractor_fp0(
    sqlite_retriever_setup: _RetrieverFixture,
) -> None:
    """Verifies the core Q06 fix: a topical 1-hit query does NOT pad unrelated recent memories."""
    database, repository, _, retriever, session_id = sqlite_retriever_setup
    namespaces = ["character/default/user/local"]

    # 1. Target joke record
    rec_joke = await _insert_test_record(
        database,
        repository,
        session_id,
        text="和用户关于变成冰棒的互动梗 冰棒",
        kind="episodic.shared_event",
        predicate="shared_joke.冰棒",
        importance=0.85,
    )

    # 2. Unrelated recent dessert distractor record
    rec_distractor = await _insert_test_record(
        database,
        repository,
        session_id,
        text="夏天喜欢吃草莓口味雪糕甜点",
        kind="semantic.preference",
        predicate="preference.dessert",
        importance=0.60,
    )

    # Topical query with candidate count == 1 < 3
    query = "还记得变成冰棒了的互动梗吗 冰棒"
    packet = await retriever.retrieve_context(query, namespaces, token_budget=700, limit=12)

    all_excerpts = (
        packet.pinned_facts
        + packet.recent_episodes
        + packet.relevant_memories
        + packet.open_commitments
        + packet.relationship_context
    )
    retrieved_ids = {item.memory_id for item in all_excerpts}

    # Must contain the joke, and MUST NOT contain the distractor
    assert rec_joke.memory_id in retrieved_ids
    assert rec_distractor.memory_id not in retrieved_ids
    assert len(retrieved_ids) == 1


@pytest.mark.asyncio
async def test_topical_no_hit_suppresses_recent_distractor(
    sqlite_retriever_setup: _RetrieverFixture,
) -> None:
    """A query targeting an absent topic does NOT pad unrelated recent records."""
    database, repository, _, retriever, session_id = sqlite_retriever_setup
    namespaces = ["character/default/user/local"]

    await _insert_test_record(
        database,
        repository,
        session_id,
        text="夏天喜欢吃草莓口味雪糕甜点",
        importance=0.70,
    )

    # Topic not in database
    query_zh = "还记得我们去巴黎吗？"
    packet_zh = await retriever.retrieve_context(query_zh, namespaces, token_budget=700)
    assert len(packet_zh.relevant_memories) == 0

    query_en = "What do you remember about me visiting Tokyo?"
    packet_en = await retriever.retrieve_context(query_en, namespaces, token_budget=700)
    assert len(packet_en.relevant_memories) == 0


@pytest.mark.asyncio
async def test_broad_inventory_query_supplements_recent_records_with_zero_search_hits(
    sqlite_retriever_setup: _RetrieverFixture,
) -> None:
    """An open general inventory query recalls recent memories when 0 FTS hits exist."""
    database, repository, _, retriever, session_id = sqlite_retriever_setup
    namespaces = ["character/default/user/local"]

    rec1 = await _insert_test_record(
        database, repository, session_id, text="喜欢深色模式界面", importance=0.7
    )
    rec2 = await _insert_test_record(
        database, repository, session_id, text="常用键盘是红轴机械键盘", importance=0.8
    )

    query = "你记得我什么？"
    packet = await retriever.retrieve_context(query, namespaces, token_budget=700)
    retrieved_ids = {m.memory_id for m in packet.relevant_memories}

    assert rec1.memory_id in retrieved_ids
    assert rec2.memory_id in retrieved_ids
    for item in packet.relevant_memories:
        assert "recent" in item.retrieval_sources


@pytest.mark.asyncio
async def test_broad_inventory_query_supplements_when_one_hit_already_present(
    sqlite_retriever_setup: _RetrieverFixture,
) -> None:
    """In accordance with Astra's decision (Point 2): broad inventory queries with 1 valid hit

    legitimately supplement with recent records up to candidate bound < 3.
    """
    database, repository, _, retriever, session_id = sqlite_retriever_setup
    namespaces = ["character/default/user/local"]

    # Record 1: matches "喜好" in FTS
    rec_pref = await _insert_test_record(
        database, repository, session_id, text="记录用户的各项喜好和兴趣", importance=0.8
    )
    # Record 2: recent record that does not mention "喜好"
    rec_editor = await _insert_test_record(
        database, repository, session_id, text="开发主力工具是VSCode", importance=0.7
    )

    query = "我的喜好"
    packet = await retriever.retrieve_context(query, namespaces, token_budget=700)
    retrieved_ids = {m.memory_id for m in packet.relevant_memories}

    assert rec_pref.memory_id in retrieved_ids
    assert rec_editor.memory_id in retrieved_ids
    pref_item = next(m for m in packet.relevant_memories if m.memory_id == rec_pref.memory_id)
    assert "fts" in pref_item.retrieval_sources


@pytest.mark.asyncio
async def test_negations_and_quotes_conservative_denial(
    sqlite_retriever_setup: _RetrieverFixture,
) -> None:
    """Negated or quoted requests conservatively decline recency fallback."""
    database, repository, _, retriever, session_id = sqlite_retriever_setup
    namespaces = ["character/default/user/local"]

    await _insert_test_record(
        database, repository, session_id, text="喜欢喝美式黑咖啡", importance=0.7
    )

    # Negation
    packet_neg = await retriever.retrieve_context("我不想知道你记得我什么", namespaces)
    assert len(packet_neg.relevant_memories) == 0

    # Quotes
    packet_quote = await retriever.retrieve_context("“你记得我什么？”", namespaces)
    assert len(packet_quote.relevant_memories) == 0

    # Multi-intent
    packet_multi = await retriever.retrieve_context("你记得我什么？顺便帮我查一下天气", namespaces)
    assert len(packet_multi.relevant_memories) == 0


@pytest.mark.asyncio
async def test_pinned_records_and_hybrid_scoring(
    sqlite_retriever_setup: _RetrieverFixture,
) -> None:
    """Pinned records have score 1.0 and hybrid sources aggregate properly."""
    database, repository, policy, _, session_id = sqlite_retriever_setup
    namespaces = ["character/default/user/local"]

    # 1. Pinned record
    _ = await _insert_test_record(
        database,
        repository,
        session_id,
        text="用户姓名是木白",
        pinned=True,
        importance=0.9,
    )
    # 2. Hybrid record
    rec_hybrid = await _insert_test_record(
        database,
        repository,
        session_id,
        text="宠物是一只可爱的布偶猫",
        importance=0.7,
    )

    class _MockSemantic:
        async def search(
            self, query: str, namespaces: list[str], limit: int
        ) -> list[ScoredMemoryReference]:
            return [ScoredMemoryReference(rec_hybrid.memory_id, 0.8)]

    class _MockTemporal:
        async def search(
            self, query: str, namespaces: list[str], now: datetime, limit: int
        ) -> list[ScoredMemoryReference]:
            return [ScoredMemoryReference(rec_hybrid.memory_id, 0.6)]

    retriever = MemoryRetriever(
        repository=repository,
        policy=policy,
        semantic_index=_MockSemantic(),  # type: ignore[arg-type]
        temporal_graph=_MockTemporal(),  # type: ignore[arg-type]
    )

    packet = await retriever.retrieve_context("布偶猫", namespaces, token_budget=700)

    assert len(packet.pinned_facts) == 1
    assert packet.pinned_facts[0].relevance == 1.0
    assert packet.pinned_facts[0].retrieval_sources == ["pinned"]

    assert len(packet.relevant_memories) == 1
    hybrid_item = packet.relevant_memories[0]
    assert hybrid_item.memory_id == rec_hybrid.memory_id
    assert "fts" in hybrid_item.retrieval_sources
    assert "semantic" in hybrid_item.retrieval_sources
    assert "temporal" in hybrid_item.retrieval_sources
    assert hybrid_item.semantic_relevance == 0.8
    assert hybrid_item.temporal_relevance == 0.6


@pytest.mark.asyncio
async def test_policy_sensitive_tombstoned_and_superseded_filtering(
    sqlite_retriever_setup: _RetrieverFixture,
) -> None:
    """Verifies that sensitive, tombstoned, and superseded records are excluded from retrieval."""
    database, repository, _, retriever, session_id = sqlite_retriever_setup
    namespaces = ["character/default/user/local"]
    now = datetime.now(UTC)

    # Sensitive fact
    await _insert_test_record(
        database,
        repository,
        session_id,
        text="手机号 13800000000",
        sensitivity=PrivacyLevel.SENSITIVE,
    )
    # Active fact to be superseded
    rec_old = await _insert_test_record(database, repository, session_id, text="居住在杭州西湖区")
    # New active fact superseding rec_old
    await _insert_test_record(
        database,
        repository,
        session_id,
        text="定居在上海徐汇区",
        supersede_target=rec_old.memory_id,
    )
    # Active fact to be tombstoned
    rec_tomb = await _insert_test_record(database, repository, session_id, text="喝温热牛奶助眠")
    await repository.tombstone(rec_tomb.memory_id, now)

    # Broad query fallback
    packet = await retriever.retrieve_context("你记得我什么？", namespaces, token_budget=700)
    retrieved_texts = {m.text for m in packet.relevant_memories}

    assert "手机号 13800000000" not in retrieved_texts
    assert "居住在杭州西湖区" not in retrieved_texts
    assert "喝温热牛奶助眠" not in retrieved_texts
    assert "定居在上海徐汇区" in retrieved_texts


@pytest.mark.asyncio
async def test_constrained_token_budget_truncation(
    sqlite_retriever_setup: _RetrieverFixture,
) -> None:
    """Verifies that tight token budget truncates lower-ranked records deterministically."""
    database, repository, _, retriever, session_id = sqlite_retriever_setup
    namespaces = ["character/default/user/local"]

    rec1 = await _insert_test_record(
        database, repository, session_id, text="早晨习惯喝一杯黑咖啡", importance=0.9
    )
    await _insert_test_record(
        database, repository, session_id, text="下午习惯休息半小时散步", importance=0.5
    )

    # Generous budget: both fit
    packet_gen = await retriever.retrieve_context("习惯", namespaces, token_budget=700)
    assert len(packet_gen.relevant_memories) == 2

    # Constrained budget: only 1 fits
    packet_con = await retriever.retrieve_context("习惯", namespaces, token_budget=15)
    assert len(packet_con.relevant_memories) == 1
    assert packet_con.relevant_memories[0].memory_id == rec1.memory_id


@pytest.mark.asyncio
async def test_expanded_model_budget_recovers_records_at_sqlite_retrieval_boundary(
    sqlite_retriever_setup: _RetrieverFixture,
) -> None:
    from chatwaifu_protocol.character import ModelContextBudget
    from chatwaifu_runtime.providers.context_budget import resolve_context_budget

    database, repository, _, retriever, session_id = sqlite_retriever_setup
    records = [
        await _insert_test_record(
            database,
            repository,
            session_id,
            text=f"budgetrecord item {index}: " + "verified context " * 5,
        )
        for index in range(16)
    ]
    legacy = await retriever.retrieve_context(
        "budgetrecord", ["character/default/user/local"], token_budget=700, limit=12
    )
    configured = ModelContextBudget(
        output_reserve_tokens=8192,
        section_policy="scaled",
        estimate_margin_ratio=0.15,
        memory_candidate_limit=24,
    )
    resolved = resolve_context_budget(32768, configured)
    expanded = await retriever.retrieve_context(
        "budgetrecord",
        ["character/default/user/local"],
        token_budget=resolved.retrieval_characters,
        limit=configured.memory_candidate_limit,
    )
    assert len(legacy.relevant_memories) < 12
    assert {item.memory_id for item in expanded.relevant_memories} == {
        record.memory_id for record in records
    }
    assert expanded.token_budget_used == sum(len(record.text) for record in records)
