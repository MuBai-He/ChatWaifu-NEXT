# ruff: noqa: RUF001
"""Comprehensive tests for Phase 17.1B: Instant Messaging Bubble Planning and Durable Cadence."""

from __future__ import annotations

import unicodedata
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import cast
from uuid import UUID, uuid4

import pytest
from chatwaifu_protocol.channels import (
    ChannelChatType,
    ChannelConnectionConfiguration,
    ChannelDeliveryPartAcknowledgement,
    ChannelDeliveryPartClaimRequest,
    ChannelDeliveryPartKind,
    ChannelDeliveryPartStatus,
    ChannelPresentationPolicy,
    ChannelPresentationProfile,
    ChannelTurnStatus,
)
from chatwaifu_protocol.character import (
    AffectState,
    CharacterKernelSnapshot,
    RelationshipState,
    ResponsePlan,
)
from chatwaifu_protocol.memory import MemoryContextPacket
from chatwaifu_runtime.character_kernel.prompt import PromptCompiler
from chatwaifu_runtime.characters.service import CharacterService
from chatwaifu_runtime.config.settings import StorageConfig
from chatwaifu_runtime.conversation.models import ConversationSourceContext
from chatwaifu_runtime.external_channels.models import (
    ChannelDeliveryPartRecord,
    ChannelDeliveryPlanRecord,
    ChannelTurnRecord,
)
from chatwaifu_runtime.external_channels.presentation import (
    BubbleSplitter,
    CadenceCalculator,
    InstantMessageDeliveryPlanFactory,
    SingleTextDeliveryPlanFactory,
)
from chatwaifu_runtime.external_channels.scheduler import (
    ChannelDeliveryScheduler,
    DeliveryPartExecutionResult,
    DeliveryPartOutcome,
)
from chatwaifu_runtime.persistence.database import Database
from chatwaifu_runtime.persistence.event_store import EventStore
from chatwaifu_runtime.persistence.sqlite_external_channels import (
    SQLiteExternalChannelRepository,
)
from chatwaifu_runtime.providers.model_config import ModelConfigurationService

CHARACTERS_ROOT = Path(__file__).resolve().parents[3] / "characters"


class _RecordingExecutor:
    def __init__(self) -> None:
        self.executed_parts: list[tuple[UUID, int, str]] = []

    async def execute_part(
        self,
        plan: ChannelDeliveryPlanRecord,
        part: ChannelDeliveryPartRecord,
    ) -> DeliveryPartExecutionResult:
        text = part.payload.text if hasattr(part.payload, "text") else ""
        self.executed_parts.append((plan.delivery_id, part.ordinal, text))
        return DeliveryPartExecutionResult(
            outcome=DeliveryPartOutcome.DELIVERED,
            provider_message_id=f"pmsg-{plan.delivery_id.hex[:8]}-{part.ordinal}",
        )


# =========================================================================
# 1. BubbleSplitter and Atomic Protection Tests
# =========================================================================


def test_casual_chinese_splitting_into_natural_bubbles() -> None:
    splitter = BubbleSplitter()
    policy = ChannelPresentationPolicy(
        profile=ChannelPresentationProfile.INSTANT_MESSAGE,
        max_parts=3,
        preferred_chars_per_part=30,
        soft_max_chars_per_part=60,
    )
    text = (
        "主人你终于回来啦！今天在外面工作辛苦了吧？"
        "我刚刚泡好了你最喜欢的热红茶，快趁热喝一点暖暖身子吧。"
        "等会儿想听什么音乐，我随时为你播放哦～"
    )
    res = splitter.split(text, policy)
    assert 2 <= res.part_count <= 3
    # Check lossless preservation (rejoining reproduces text)
    rejoined = "".join(res.parts)
    assert unicodedata.normalize("NFC", rejoined) == unicodedata.normalize(
        "NFC", text.replace(" ", "")
    )


def test_atomic_spans_urls_and_markdown_links_preserved() -> None:
    splitter = BubbleSplitter()
    policy = ChannelPresentationPolicy(max_parts=3, preferred_chars_per_part=25)
    text = (
        "详细的文档在这里：https://chatwaifu.example.com/docs/v2/api-reference?session=123&token=xyz#overview。"
        "另外你也可以参考这个 [官方开发指南](https://chatwaifu.example.com/guide) 哦！"
    )
    res = splitter.split(text, policy)
    # The URL and markdown link must be fully contained in one part without being cut in the middle
    url_found = any(
        "https://chatwaifu.example.com/docs/v2/api-reference?session=123&token=xyz#overview" in p
        for p in res.parts
    )
    link_found = any("[官方开发指南](https://chatwaifu.example.com/guide)" in p for p in res.parts)
    assert url_found
    assert link_found


def test_atomic_spans_numbers_decimals_and_paired_quotes() -> None:
    splitter = BubbleSplitter()
    policy = ChannelPresentationPolicy(max_parts=3, preferred_chars_per_part=25)
    text = (
        "现在的准确温度是 36.5 度，CPU 占用率为 85.25%，预计还需要 10:30 完成。"
        "正如老师常说的：“学而不思则罔，思而不学则殆”，我们一定要多动手实践！"
    )
    res = splitter.split(text, policy)
    # Number decimals and quotes intact
    assert any("36.5" in p for p in res.parts)
    assert any("85.25%" in p for p in res.parts)
    assert any("“学而不思则罔，思而不学则殆”" in p for p in res.parts)


def test_atomic_spans_emoji_grapheme_clusters() -> None:
    splitter = BubbleSplitter()
    policy = ChannelPresentationPolicy(max_parts=3, preferred_chars_per_part=20)
    # 👨‍👩‍👧‍👦 is a ZWJ sequence of 7 Unicode codepoints
    text = "祝你们全家幸福 👨‍👩‍👧‍👦！生活美满 ❤️，天天开心 ✨！"
    res = splitter.split(text, policy)
    # The family emoji must remain intact
    assert any("👨‍👩‍👧‍👦" in p for p in res.parts)


def test_technical_code_and_long_form_bypass() -> None:
    splitter = BubbleSplitter()
    policy = ChannelPresentationPolicy(
        profile=ChannelPresentationProfile.INSTANT_MESSAGE,
        max_parts=3,
        bypass_long_form=True,
    )

    code_text = (
        "这里是具体的 Python 实现：\n"
        "```python\n"
        "def fibonacci(n: int) -> int:\n"
        "    if n <= 1:\n"
        "        return n\n"
        "    return fibonacci(n - 1) + fibonacci(n - 2)\n"
        "```\n"
        "这个函数的时间复杂度是 O(2^n)，建议使用记忆化优化。"
    )
    res = splitter.split(code_text, policy)
    assert res.part_count == 1
    assert res.fallback_reason == "code_block_detected"
    assert res.parts[0] == unicodedata.normalize("NFC", code_text.strip())

    table_text = (
        "对比表格如下：\n| 参数 | 说明 |\n| --- | --- |\n| host | 监听地址 |\n| port | 监听端口 |\n"
    )
    res_table = splitter.split(table_text, policy)
    assert res_table.part_count == 1
    assert res_table.fallback_reason == "markdown_table_detected"


def test_single_text_profile_bypasses_splitting() -> None:
    splitter = BubbleSplitter()
    policy = ChannelPresentationPolicy(profile=ChannelPresentationProfile.SINGLE_TEXT)
    text = "第一句话。第二句话！第三句话？"
    res = splitter.split(text, policy)
    assert res.part_count == 1
    assert res.fallback_reason == "single_text_profile"
    assert res.parts[0] == text


# =========================================================================
# 2. CadenceCalculator Tests
# =========================================================================


def test_cadence_calculator_inter_bubble_delays_and_terminal_zero() -> None:
    calculator = CadenceCalculator(ms_per_grapheme=20)
    policy = ChannelPresentationPolicy(
        min_delay_ms=500,
        max_delay_ms=2000,
        total_cadence_delay_ceiling_ms=5000,
    )
    parts = ("第一条气泡！", "第二条稍微长一点的气泡？", "这是最后一条气泡。")
    delays = calculator.calculate_delays(parts, policy)
    assert len(delays) == 3
    # First part has delay
    assert delays[0] >= 500
    # Second part has delay
    assert delays[1] >= 500
    # Terminal part MUST have 0 delay
    assert delays[2] == 0
    # Total cumulative delay within ceiling
    assert sum(delays) <= 5000


def test_cadence_calculator_disabled_cadence() -> None:
    calculator = CadenceCalculator()
    policy = ChannelPresentationPolicy(cadence_enabled=False)
    parts = ("气泡一", "气泡二", "气泡三")
    delays = calculator.calculate_delays(parts, policy)
    assert delays == (0, 0, 0)


# =========================================================================
# 3. DeliveryPlanFactory Tests
# =========================================================================


def test_instant_message_delivery_plan_factory_drafts() -> None:
    factory = InstantMessageDeliveryPlanFactory()
    policy = ChannelPresentationPolicy(
        profile=ChannelPresentationProfile.INSTANT_MESSAGE,
        max_parts=3,
        preferred_chars_per_part=30,
    )
    reply = (
        "主人你终于回来啦！今天在外面工作辛苦了吧？"
        "我刚刚泡好了你最喜欢的热红茶，快趁热喝一点暖暖身子吧。"
        "等会儿想听什么音乐，我随时为你播放哦～"
    )
    drafts = factory.create_parts(reply, policy=policy)
    assert len(drafts) >= 2
    for i, draft in enumerate(drafts):
        assert draft.ordinal == i
        assert draft.kind == ChannelDeliveryPartKind.TEXT
        assert draft.required is True
        assert draft.not_before_at is None
        if i == len(drafts) - 1:
            assert draft.delay_after_ms == 0
        else:
            assert draft.delay_after_ms > 0


def test_single_text_delivery_plan_factory_draft() -> None:
    factory = SingleTextDeliveryPlanFactory()
    drafts = factory.create_parts("一整条回复内容")
    assert len(drafts) == 1
    assert drafts[0].ordinal == 0
    assert drafts[0].payload.text == "一整条回复内容"
    assert drafts[0].delay_after_ms == 0


# =========================================================================
# 4. Durable Cadence SQLite Integration & Recovery Tests
# =========================================================================


async def _seed_channel_and_turn(
    db: Database,
    repo: SQLiteExternalChannelRepository,
    connection_id: UUID | None = None,
    presentation_policy: ChannelPresentationPolicy | None = None,
) -> tuple[UUID, UUID, UUID, UUID]:
    now = datetime.now(UTC)
    conn_id = connection_id or uuid4()
    session_id = uuid4()
    binding_id = uuid4()
    turn_id = uuid4()

    await repo.create_connection(
        ChannelConnectionConfiguration(
            connection_id=conn_id,
            provider_id="weixin_ilink",
            name="测试微信",
            character_id="default",
            principal_scope="local",
            presentation_policy=presentation_policy
            or ChannelPresentationPolicy(profile=ChannelPresentationProfile.INSTANT_MESSAGE),
        ),
        access_token_hash="token-hash",
        created_at=now,
    )

    async with db.transaction() as conn:
        await conn.execute(
            """
            INSERT INTO sessions(
                session_id, character_id, state, conversation_state,
                revision, next_sequence, created_at, updated_at
            ) VALUES (?, 'default', 'ready', 'idle', 0, 1, ?, ?)
            """,
            (str(session_id), now.isoformat(), now.isoformat()),
        )

    await repo.create_binding(
        binding_id=binding_id,
        connection_id=conn_id,
        conversation_key="c_key",
        sender_key="s_key",
        session_id=session_id,
        created_at=now,
    )

    turn = ChannelTurnRecord(
        channel_turn_id=turn_id,
        connection_id=conn_id,
        binding_id=binding_id,
        external_message_id=f"ext-{uuid4()}",
        content_sha256="sha256fake",
        account_key="bot-1",
        conversation_key="c_key",
        chat_type=ChannelChatType.DIRECT,
        conversation_label="label",
        sender_key="s_key",
        sender_display_name="Tester",
        principal_scope="local",
        session_id=session_id,
        turn_id=uuid4(),
        generation_id=uuid4(),
        status=ChannelTurnStatus.ACCEPTED,
        reply_text=None,
        error=None,
        delivery_id=None,
        delivery_status=None,
        revision=0,
        accepted_at=now,
        created_at=now,
        updated_at=now,
        completed_at=None,
    )
    await repo.create_turn(turn)
    return conn_id, session_id, binding_id, turn_id


@pytest.fixture
async def channel_db(tmp_path: Path) -> AsyncIterator[Database]:
    db_path = tmp_path / "cadence_test.db"
    db = Database(db_path, StorageConfig(database_path=db_path))
    await db.open()
    try:
        yield db
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_durable_cadence_part_ack_sets_next_part_not_before_at(
    channel_db: Database,
) -> None:
    event_store = EventStore(channel_db)
    repo = SQLiteExternalChannelRepository(channel_db, event_store)

    policy = ChannelPresentationPolicy(
        profile=ChannelPresentationProfile.INSTANT_MESSAGE,
        max_parts=3,
        preferred_chars_per_part=15,
        min_delay_ms=1000,
        max_delay_ms=2000,
    )
    _, _, _, turn_id = await _seed_channel_and_turn(channel_db, repo, presentation_policy=policy)

    factory = InstantMessageDeliveryPlanFactory()
    text = (
        "第一段内容十分重要，请务必留意！"
        "第二段内容也同样关键，请仔细查看！"
        "第三段内容收尾，祝你一切顺利！"
    )
    drafts = factory.create_parts(text, policy=policy)
    assert len(drafts) >= 2
    assert drafts[0].delay_after_ms >= 1000

    now = datetime.now(UTC)
    deliv_id = uuid4()

    # Create delivery plan directly via complete_turn
    await repo.complete_turn(
        channel_turn_id=turn_id,
        reply_text=text,
        delivery_id=deliv_id,
        completed_at=now,
        parts=drafts,
    )
    plan = await repo.get_delivery_plan(deliv_id)
    assert plan is not None
    # Part 0 has not_before_at = None
    assert plan.parts[0].not_before_at is None
    # Part 1 initially has not_before_at = None
    assert plan.parts[1].not_before_at is None

    # 1. Claim Part 0
    lease_id = uuid4()
    claim_res = await repo.claim_next_delivery_part(
        ChannelDeliveryPartClaimRequest(
            delivery_id=deliv_id,
            lease_id=lease_id,
            lease_seconds=30,
        ),
        claimed_at=now,
    )
    assert claim_res is not None
    assert claim_res.part is not None
    assert claim_res.part.ordinal == 0

    # 2. Acknowledge Part 0 as DELIVERED at ack_time
    ack_time = now + timedelta(seconds=1)
    ack_res = await repo.acknowledge_delivery_part(
        ChannelDeliveryPartAcknowledgement(
            delivery_id=deliv_id,
            part_id=claim_res.part.part_id,
            lease_id=lease_id,
            status=ChannelDeliveryPartStatus.DELIVERED,
            provider_message_id="msg-part-0",
            acknowledged_at=ack_time,
        ),
        updated_at=ack_time,
    )
    assert ack_res.applied is True

    # 3. Verify in database: Part 1's not_before_at MUST BE SET
    updated_plan = await repo.get_delivery_plan(deliv_id)
    assert updated_plan is not None
    part_0 = updated_plan.parts[0]
    part_1 = updated_plan.parts[1]
    assert part_0.status == ChannelDeliveryPartStatus.DELIVERED
    assert part_1.status == ChannelDeliveryPartStatus.PENDING
    assert part_1.not_before_at is not None

    expected_not_before = ack_time + timedelta(milliseconds=part_0.delay_after_ms)
    assert part_1.not_before_at == expected_not_before

    # 4. Attempt to claim Part 1 BEFORE not_before_at -> MUST RETURN NONE
    too_early = ack_time + timedelta(milliseconds=part_0.delay_after_ms - 100)
    claim_early = await repo.claim_next_delivery_part(
        ChannelDeliveryPartClaimRequest(
            delivery_id=deliv_id,
            lease_id=uuid4(),
            lease_seconds=30,
        ),
        claimed_at=too_early,
    )
    assert claim_early is None

    # 5. Attempt to claim Part 1 AT OR AFTER not_before_at -> SUCCEEDS
    on_time = ack_time + timedelta(milliseconds=part_0.delay_after_ms + 10)
    claim_on_time = await repo.claim_next_delivery_part(
        ChannelDeliveryPartClaimRequest(
            delivery_id=deliv_id,
            lease_id=uuid4(),
            lease_seconds=30,
        ),
        claimed_at=on_time,
    )
    assert claim_on_time is not None
    assert claim_on_time.part is not None
    assert claim_on_time.part.ordinal == 1


@pytest.mark.asyncio
async def test_durable_cadence_crash_recovery_preserves_not_before_and_never_resends(
    channel_db: Database,
) -> None:
    event_store = EventStore(channel_db)
    repo = SQLiteExternalChannelRepository(channel_db, event_store)

    policy = ChannelPresentationPolicy(
        profile=ChannelPresentationProfile.INSTANT_MESSAGE,
        max_parts=2,
        preferred_chars_per_part=15,
        min_delay_ms=2000,
        max_delay_ms=2000,
    )
    conn_id, _, _, turn_id = await _seed_channel_and_turn(
        channel_db, repo, presentation_policy=policy
    )

    factory = InstantMessageDeliveryPlanFactory()
    text = "第一段短语详细展开说明具体情况！第二段短语继续补充后续注意事项！"
    drafts = factory.create_parts(text, policy=policy)
    assert len(drafts) == 2

    now = datetime.now(UTC)
    deliv_id = uuid4()
    await repo.complete_turn(
        channel_turn_id=turn_id,
        reply_text=text,
        delivery_id=deliv_id,
        completed_at=now,
        parts=drafts,
    )

    # Claim & deliver Part 0
    l_id = uuid4()
    c = await repo.claim_next_delivery_part(
        ChannelDeliveryPartClaimRequest(delivery_id=deliv_id, lease_id=l_id, lease_seconds=30),
        claimed_at=now,
    )
    assert c is not None and c.part is not None
    ack_time = now + timedelta(seconds=1)
    await repo.acknowledge_delivery_part(
        ChannelDeliveryPartAcknowledgement(
            delivery_id=deliv_id,
            part_id=c.part.part_id,
            lease_id=l_id,
            status=ChannelDeliveryPartStatus.DELIVERED,
            provider_message_id="msg-crash-0",
            acknowledged_at=ack_time,
        ),
        updated_at=ack_time,
    )

    # SIMULATE RUNTIME CRASH AND RESTART
    # A fresh repository and scheduler instance connect to the same SQLite database
    restarted_repo = SQLiteExternalChannelRepository(channel_db, event_store)
    executor = _RecordingExecutor()
    scheduler = ChannelDeliveryScheduler(
        repository=restarted_repo,
        executor=executor,
        connection_id=conn_id,
        poll_interval_seconds=0.1,
    )

    # Step at crash time (too early for Part 1)
    crash_time = ack_time + timedelta(milliseconds=500)
    progress = await scheduler.step(now=crash_time)
    assert progress is False
    # Executor was NOT invoked, Part 0 was NOT resent
    assert len(executor.executed_parts) == 0

    # Step after cadence duration (now >= not_before_at)
    resumed_time = ack_time + timedelta(milliseconds=2100)
    progress_resumed = await scheduler.step(now=resumed_time)
    assert progress_resumed is True
    # Part 1 was executed! Part 0 was NEVER resent!
    assert len(executor.executed_parts) == 1
    assert executor.executed_parts[0][1] == 1  # ordinal 1


# =========================================================================
# 5. Prompt Contract Verification
# =========================================================================


@pytest.mark.asyncio
async def test_prompt_compiler_with_source_context_injects_instant_message_contract() -> None:
    characters = CharacterService(CHARACTERS_ROOT)
    characters.start()
    character = characters.get("default")
    assert character is not None

    class _PromptModels:
        def get(self, role: str) -> SimpleNamespace:
            assert role == "chat"
            return SimpleNamespace(context_window=1024)

    compiler = PromptCompiler(cast(ModelConfigurationService, _PromptModels()))
    now = datetime.now(UTC)
    kernel = CharacterKernelSnapshot(
        character_id="default",
        user_scope="local",
        revision=1,
        affect=AffectState(updated_at=now),
        relationship=RelationshipState(updated_at=now),
    )
    source_ctx = ConversationSourceContext(
        provider_id="weixin_ilink",
        connection_id=uuid4(),
        account_key="owner-acc",
        principal_scope="local",
        chat_type="direct",
        conversation_key="owner-chat",
        sender_key="owner-user",
        received_at=now,
    )
    compiled = await compiler.compile(
        character=character,
        kernel=kernel,
        plan=ResponsePlan(
            intent="answer",
            tone="gentle",
            expression="neutral",
            rationale="test",
        ),
        memory=MemoryContextPacket(token_budget_used=0),
        history=(),
        user_text="你好呀！",
        source_context=source_ctx,
    )
    system_text = compiled.system_prompt
    assert "You are messaging in an instant chat" in system_text
    assert "keep responses short, natural, and conversational" in system_text
    assert "Do not output internal tags, delimiters" in system_text
