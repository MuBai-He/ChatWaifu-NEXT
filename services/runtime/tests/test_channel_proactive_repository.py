"""Real SQLite admission and receipt fences; no provider or model calls."""

import asyncio
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo

import aiosqlite
import pytest
from chatwaifu_protocol.channel_proactive import (
    ChannelOutboundIntentStatus,
    ChannelProactivePolicy,
    ChannelProactivePolicyUpdate,
    ChannelProactiveReason,
)
from chatwaifu_protocol.channels import (
    ChannelChatType,
    ChannelConnectionConfiguration,
    ChannelConnectionStatus,
    ChannelDeliveryPartAcknowledgement,
    ChannelDeliveryPartClaimRequest,
    ChannelDeliveryPartStatus,
    ChannelDeliveryStatus,
    ChannelTurnStatus,
)
from chatwaifu_runtime.config.settings import StorageConfig
from chatwaifu_runtime.external_channels.models import ChannelTurnRecord
from chatwaifu_runtime.external_channels.proactive_models import (
    ChannelOutboundIntentRecord,
    evaluate_proactive_context,
    proactive_quiet_hours,
)
from chatwaifu_runtime.persistence.database import Database
from chatwaifu_runtime.persistence.event_store import EventStore
from chatwaifu_runtime.persistence.migrations import MIGRATIONS
from chatwaifu_runtime.persistence.sqlite_channel_proactive import SQLiteChannelProactiveRepository
from chatwaifu_runtime.persistence.sqlite_external_channels import SQLiteExternalChannelRepository

NOW = datetime(2026, 10, 3, 2, tzinfo=UTC)


async def _open(path: Path, *, legacy: bool = False) -> Database:
    database = Database(
        path,
        StorageConfig(database_path=path),
        migrations=tuple(item for item in MIGRATIONS if item[0] < 38) if legacy else MIGRATIONS,
    )
    await database.open()
    return database


async def _owner(
    database: Database, *, enabled: bool = True, policy: ChannelProactivePolicy | None = None
) -> tuple[SQLiteExternalChannelRepository, SQLiteChannelProactiveRepository, UUID, UUID, UUID]:
    events = EventStore(database)
    deliveries = SQLiteExternalChannelRepository(database, events)
    proactive = SQLiteChannelProactiveRepository(database, events, deliveries=deliveries)
    connection_id, session_id, binding_id = uuid4(), uuid4(), uuid4()
    await deliveries.create_connection(
        ChannelConnectionConfiguration(
            connection_id=connection_id,
            provider_id="qq_napcat",
            name="test QQ",
            character_id="character",
            principal_scope="local",
            account_key="account",
            allowed_sender_keys=["owner"],
            enabled=enabled,
        ),
        access_token_hash="test-hash",
        created_at=NOW,
    )
    await database.execute(
        "INSERT INTO sessions(session_id,character_id,state,conversation_state,"
        "created_at,updated_at) VALUES(?,'character','ready','idle',?,?)",
        (str(session_id), NOW.isoformat(), NOW.isoformat()),
    )
    await deliveries.create_binding(
        binding_id=binding_id,
        connection_id=connection_id,
        conversation_key="direct:owner",
        sender_key="owner",
        session_id=session_id,
        created_at=NOW,
    )
    if enabled:
        await deliveries.touch_connection(
            connection_id, status=ChannelConnectionStatus.READY, seen_at=NOW
        )
    if policy is not None:
        await proactive.update_policy(
            connection_id,
            ChannelProactivePolicyUpdate(expected_revision=0, policy=policy),
            updated_at=NOW,
        )
    return deliveries, proactive, connection_id, session_id, binding_id


def _enabled(**kwargs: object) -> ChannelProactivePolicy:
    return ChannelProactivePolicy.model_validate(
        dict(enabled=True, quiet_hours_enabled=False, idle_minutes=1, cooldown_minutes=1, **kwargs)
    )


async def _input(
    deliveries: SQLiteExternalChannelRepository,
    connection_id: UUID,
    session_id: UUID,
    binding_id: UUID,
    *,
    at: datetime = NOW + timedelta(seconds=1),
) -> ChannelTurnRecord:
    return await deliveries.create_turn(
        ChannelTurnRecord(
            channel_turn_id=uuid4(),
            connection_id=connection_id,
            binding_id=binding_id,
            external_message_id=str(uuid4()),
            content_sha256="a" * 64,
            account_key="account",
            conversation_key="direct:owner",
            chat_type=ChannelChatType.DIRECT,
            conversation_label=None,
            sender_key="owner",
            sender_display_name=None,
            principal_scope="local",
            session_id=session_id,
            turn_id=uuid4(),
            generation_id=uuid4(),
            status=ChannelTurnStatus.COMPLETED,
            reply_text=None,
            error=None,
            delivery_id=None,
            delivery_status=None,
            revision=0,
            accepted_at=at,
            created_at=at,
            updated_at=at,
            completed_at=at,
        )
    )


async def _reserved(
    proactive: SQLiteChannelProactiveRepository,
    connection_id: UUID,
    *,
    at: datetime = NOW + timedelta(minutes=2),
) -> ChannelOutboundIntentRecord:
    result = await proactive.reserve_intent(connection_id, as_of=at)
    assert result.created and result.intent is not None, result.reason
    return result.intent


async def _completed(
    database: Database,
    intent: ChannelOutboundIntentRecord,
    *,
    text: str = "local test reply",
    role: str = "system",
    source: UUID | None = None,
) -> None:
    context = json.dumps(dict(outbound_intent_id=str(source or intent.request_id)))
    await database.execute(
        "INSERT INTO turns(turn_id,session_id,role,source_context_json,created_at) "
        "VALUES(?,?,?,?,?)",
        (str(intent.turn_id), str(intent.session_id), role, context, NOW.isoformat()),
    )
    await database.execute(
        "INSERT INTO generations(generation_id,session_id,turn_id,state,backend_kind,"
        "output_text,completed_at) VALUES(?,?,?,'completed','test',?,?)",
        (
            str(intent.generation_id),
            str(intent.session_id),
            str(intent.turn_id),
            text,
            NOW.isoformat(),
        ),
    )


@pytest.mark.asyncio
async def test_default_preview_and_activation_fence_do_not_write(tmp_path: Path) -> None:
    db = await _open(tmp_path / "default.db")
    try:
        d, p, c, s, b = await _owner(db)
        await _input(d, c, s, b, at=NOW)
        before = await db.fetchall("SELECT * FROM channel_proactive_policies")
        context = await p.get_context(c, as_of=NOW + timedelta(minutes=2))
        assert context.policy.revision == 0 and not context.policy.policy.enabled
        assert context.binding is not None and context.policy.binding_id is None
        assert (
            evaluate_proactive_context(context, as_of=NOW).reason is ChannelProactiveReason.DISABLED
        )
        assert await db.fetchall("SELECT * FROM channel_proactive_policies") == before
        assert await db.fetchall("SELECT * FROM channel_proactive_episodes") == []
        policy = await p.update_policy(
            c, ChannelProactivePolicyUpdate(expected_revision=0, policy=_enabled()), updated_at=NOW
        )
        assert policy.revision == 1 and policy.binding_id == b
        assert (
            await p.reserve_intent(c, as_of=NOW + timedelta(minutes=2))
        ).reason is ChannelProactiveReason.NO_OWNER_ACTIVITY
        await _input(d, c, s, b)
        intent = await _reserved(p, c)
        assert intent.not_before_at == NOW + timedelta(minutes=1, seconds=1)
        assert intent.expires_at == intent.not_before_at + timedelta(minutes=15)
        assert intent.session_id == s and intent.generation_id != intent.turn_id
        assert (
            await db.fetchone(
                "SELECT 1 FROM generations WHERE generation_id=?", (str(intent.generation_id),)
            )
            is None
        )
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_episode_cancel_revision_midnight_never_refund_or_revive(tmp_path: Path) -> None:
    db = await _open(tmp_path / "episode.db")
    try:
        d, p, c, s, b = await _owner(db, policy=_enabled(daily_budget=1))
        anchor = await _input(d, c, s, b)
        intent = await _reserved(p, c)
        cancelled = await p.settle_intent(
            intent.request_id,
            reason="operator_cancelled",
            settled_at=NOW + timedelta(minutes=2),
            cancel=True,
        )
        assert cancelled.status is ChannelOutboundIntentStatus.SETTLED
        assert (
            await p.reserve_intent(c, as_of=NOW + timedelta(minutes=3))
        ).reason is ChannelProactiveReason.EPISODE_ALREADY_RESERVED
        assert (
            await p.reserve_intent(c, as_of=NOW + timedelta(days=1))
        ).reason is ChannelProactiveReason.EPISODE_ALREADY_RESERVED
        assert (await p.get_context(c, as_of=NOW + timedelta(minutes=3))).reserved_today == 1
        await _input(d, c, s, b, at=NOW + timedelta(minutes=4))
        assert (
            await p.reserve_intent(c, as_of=NOW + timedelta(minutes=6))
        ).reason is ChannelProactiveReason.DAILY_BUDGET_EXHAUSTED
        await p.update_policy(
            c,
            ChannelProactivePolicyUpdate(expected_revision=1, policy=_enabled(ttl_minutes=60)),
            updated_at=NOW + timedelta(minutes=7),
        )
        assert (
            await p.reserve_intent(c, as_of=NOW + timedelta(minutes=9))
        ).reason is ChannelProactiveReason.NO_OWNER_ACTIVITY
        fixed = await p.get_intent(intent.request_id)
        assert fixed is not None and fixed.expires_at == intent.expires_at
        assert fixed.anchor_channel_turn_id == anchor.channel_turn_id
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_reservation_and_cas_races_use_real_independent_sqlite_connections(
    tmp_path: Path,
) -> None:
    path = tmp_path / "race.db"
    db = await _open(path)
    second = await _open(path)
    try:
        d, p, c, s, b = await _owner(db, policy=_enabled())
        await _input(d, c, s, b)
        other = SQLiteChannelProactiveRepository(second)
        async with asyncio.timeout(5):
            results = await asyncio.gather(
                p.reserve_intent(c, as_of=NOW + timedelta(minutes=2)),
                other.reserve_intent(c, as_of=NOW + timedelta(minutes=2)),
            )
        assert sum(result.created for result in results) == 1
        assert (await p.get_context(c, as_of=NOW + timedelta(minutes=2))).reserved_today == 1
        async with asyncio.timeout(5):
            updates = await asyncio.gather(
                *[
                    repo.update_policy(
                        c,
                        ChannelProactivePolicyUpdate(expected_revision=1, policy=_enabled()),
                        updated_at=NOW + timedelta(minutes=3),
                    )
                    for repo in (p, other)
                ],
                return_exceptions=True,
            )
        assert sum(isinstance(result, ValueError) for result in updates) == 1
        assert (await p.get_policy(c)).revision == 2
        assert len(await p.list_active_intents()) == 0
    finally:
        await second.close()
        await db.close()


@pytest.mark.asyncio
async def test_reopen_retains_unique_lineage_and_claim_once(tmp_path: Path) -> None:
    path = tmp_path / "reopen.db"
    db = await _open(path)
    d, p, c, s, b = await _owner(db, policy=_enabled())
    await _input(d, c, s, b)
    intent = await _reserved(p, c)
    claimed = await p.claim_generation(
        intent.request_id, expected_revision=0, claimed_at=NOW + timedelta(minutes=2)
    )
    assert claimed is not None and claimed.status is ChannelOutboundIntentStatus.GENERATING
    await db.close()
    db = await _open(path)
    try:
        p = SQLiteChannelProactiveRepository(db)
        saved = await p.get_intent(intent.request_id)
        assert saved is not None and saved.generation_id == intent.generation_id
        assert saved.audio_stream_id == intent.audio_stream_id
        assert (
            await p.claim_generation(
                intent.request_id, expected_revision=0, claimed_at=NOW + timedelta(minutes=2)
            )
            is None
        )
        assert not (await p.reserve_intent(c, as_of=NOW + timedelta(minutes=2))).created
    finally:
        await db.close()


@pytest.mark.parametrize("fence", ["reset", "new_owner", "route", "policy", "expiry", "generation"])
@pytest.mark.asyncio
async def test_fixed_authorization_and_atomic_claim_fences(tmp_path: Path, fence: str) -> None:
    db = await _open(tmp_path / f"{fence}.db")
    try:
        d, p, c, s, b = await _owner(db, policy=_enabled())
        await _input(d, c, s, b)
        intent = await _reserved(p, c)
        at = NOW + timedelta(minutes=2)
        if fence == "reset":
            await db.execute(
                "INSERT INTO memory_scope_resets VALUES('character','local',?)", (at.isoformat(),)
            )
        elif fence == "new_owner":
            await _input(d, c, s, b, at=at)
        elif fence == "route":
            await db.execute(
                "UPDATE channel_connections SET revision=revision+1 WHERE connection_id=?",
                (str(c),),
            )
        elif fence == "policy":
            await p.update_policy(
                c,
                ChannelProactivePolicyUpdate(expected_revision=1, policy=_enabled()),
                updated_at=at,
            )
        elif fence == "expiry":
            at = intent.expires_at
        else:
            await _completed(db, intent)
            await db.execute(
                "UPDATE generations SET invalidated_at=? WHERE generation_id=?",
                (at.isoformat(), str(intent.generation_id)),
            )
        assert not (await p.authorize_intent(intent.request_id, as_of=at)).allowed
        refused = await p.claim_generation(intent.request_id, expected_revision=0, claimed_at=at)
        if fence == "policy":
            assert refused is None  # Policy PUT already settled and returned that event.
        else:
            assert refused is not None and refused.status is ChannelOutboundIntentStatus.SETTLED
            assert len(refused.persisted_events) == 1
            assert refused.persisted_events[0].event_type == "channel.outbound_intent_settled"
        settled = await p.get_intent(intent.request_id)
        assert settled is not None and settled.status is ChannelOutboundIntentStatus.SETTLED
        assert (await p.get_context(c, as_of=at)).reserved_today == 1
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_text_plan_source_quote_late_known_receipt_after_cancel_and_reopen(
    tmp_path: Path,
) -> None:
    path = tmp_path / "receipt.db"
    db = await _open(path)
    d, p, c, s, b = await _owner(db, policy=_enabled())
    await _input(d, c, s, b)
    intent = await _reserved(p, c)
    await p.claim_generation(
        intent.request_id, expected_revision=0, claimed_at=NOW + timedelta(minutes=2)
    )
    await _completed(db, intent)
    result = await p.create_outbound_text_plan(
        intent.request_id, reply_text="local test reply", created_at=NOW + timedelta(minutes=2)
    )
    assert (
        result.plan.channel_turn_id is None and result.plan.outbound_intent_id == intent.request_id
    )
    part = result.plan.parts[0]
    claim = await d.claim_next_delivery_part(
        ChannelDeliveryPartClaimRequest(delivery_id=result.plan.delivery_id, lease_id=uuid4()),
        claimed_at=NOW + timedelta(minutes=2),
    )
    assert claim is not None and claim.part is not None and claim.part.attempt == 1
    lease = claim.part.lease_id
    assert lease is not None
    await d.set_adapter_cursor(
        c, cursor=json.dumps({part.provider_client_id: "provider-success"}), updated_at=NOW
    )
    await p.update_policy(
        c,
        ChannelProactivePolicyUpdate(expected_revision=1, policy=ChannelProactivePolicy()),
        updated_at=NOW + timedelta(minutes=3),
    )
    assert part.provider_client_id in await d.retained_send_journal_keys(
        c, [part.provider_client_id]
    )
    # The ordinary ACK is not weakened merely because a trusted journal exists.
    with pytest.raises(ValueError, match="lease expired"):
        await d.acknowledge_delivery_part(
            ChannelDeliveryPartAcknowledgement(
                delivery_id=result.plan.delivery_id,
                part_id=part.part_id,
                lease_id=lease,
                status=ChannelDeliveryPartStatus.DELIVERED,
                provider_message_id="provider-success",
                acknowledged_at=NOW + timedelta(minutes=5),
            ),
            updated_at=NOW + timedelta(minutes=5),
        )
    await db.close()
    db = await _open(path)
    try:
        events = EventStore(db)
        d = SQLiteExternalChannelRepository(db, events)
        p = SQLiteChannelProactiveRepository(db, events, deliveries=d)
        recovered = await d.reconcile_known_delivery_part_receipt(
            c, part.provider_client_id, "provider-success", observed_at=NOW + timedelta(minutes=5)
        )
        assert recovered.applied and recovered.plan.status is ChannelDeliveryStatus.DELIVERED
        assert len(recovered.persisted_events) == 2
        assert all(
            event.turn_id == intent.turn_id and event.generation_id == intent.generation_id
            for event in recovered.persisted_events
        )
        assert all(
            event.payload.get("channel_turn_id") is None
            and event.payload.get("outbound_intent_id") == str(intent.request_id)
            for event in recovered.persisted_events
        )
        duplicate = await d.reconcile_known_delivery_part_receipt(
            c, part.provider_client_id, "provider-success", observed_at=NOW + timedelta(minutes=6)
        )
        assert not duplicate.applied and duplicate.persisted_events == ()
        saved = await p.sync_delivery_result(
            intent.request_id, updated_at=NOW + timedelta(minutes=6)
        )
        assert saved.settled_reason == "policy_changed" and saved.provider_receipt_present
        assert (
            saved.cancel_requested_at is not None
            and saved.delivery_status is ChannelDeliveryStatus.DELIVERED
        )
        assert await d.retained_send_journal_keys(c, [part.provider_client_id]) == frozenset()
        with pytest.raises(ValueError, match="conflicts"):
            await d.reconcile_known_delivery_part_receipt(
                c, part.provider_client_id, "different", observed_at=NOW
            )
        assert await d.quoted_reply_target(intent.turn_id) is None
        # Policy revocation retains a confirmed quote on the enabled inbound route.
        quote = await d.resolve_quoted_message(c, b, "provider-success")
        assert quote is not None and quote.role == "assistant" and quote.text == "local test reply"
        assert await d.resolve_quoted_message(c, uuid4(), "provider-success") is None
        await db.execute(
            "INSERT INTO memory_scope_resets VALUES('character','local',?)",
            ((NOW + timedelta(minutes=8)).isoformat(),),
        )
        assert await d.resolve_quoted_message(c, b, "provider-success") is None
        await d.soft_delete_connection(c, deleted_at=NOW + timedelta(minutes=9))
        assert c in await d.list_send_journal_connection_ids()
        assert await p.is_channel_session(s)
    finally:
        await db.close()


@pytest.mark.parametrize(
    "role,source,reply",
    [
        ("user", None, "local test reply"),
        ("system", "wrong", "local test reply"),
        ("system", None, "changed"),
    ],
)
@pytest.mark.asyncio
async def test_plan_rejects_fake_inbound_or_cross_generation_output(
    tmp_path: Path, role: str, source: str | None, reply: str
) -> None:
    db = await _open(tmp_path / "forgery.db")
    try:
        d, p, c, s, b = await _owner(db, policy=_enabled())
        await _input(d, c, s, b)
        intent = await _reserved(p, c)
        await p.claim_generation(
            intent.request_id, expected_revision=0, claimed_at=NOW + timedelta(minutes=2)
        )
        await _completed(db, intent, role=role, source=uuid4() if source else None)
        with pytest.raises(ValueError):
            await p.create_outbound_text_plan(
                intent.request_id, reply_text=reply, created_at=NOW + timedelta(minutes=2)
            )
        assert await db.fetchall("SELECT * FROM channel_deliveries") == []
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_unknown_send_keys_and_missing_keys_survive_terminal_cancellation(
    tmp_path: Path,
) -> None:
    db = await _open(tmp_path / "unknown.db")
    try:
        d, p, c, s, b = await _owner(db, policy=_enabled())
        await _input(d, c, s, b)
        intent = await _reserved(p, c)
        await p.claim_generation(
            intent.request_id, expected_revision=0, claimed_at=NOW + timedelta(minutes=2)
        )
        await _completed(db, intent)
        result = await p.create_outbound_text_plan(
            intent.request_id, reply_text="local test reply", created_at=NOW + timedelta(minutes=2)
        )
        part = result.plan.parts[0]
        await d.claim_next_delivery_part(
            ChannelDeliveryPartClaimRequest(delivery_id=result.plan.delivery_id, lease_id=uuid4()),
            claimed_at=NOW + timedelta(minutes=2),
        )
        await p.settle_intent(
            intent.request_id,
            reason="unknown_send",
            settled_at=NOW + timedelta(minutes=3),
            cancel=True,
        )
        assert await d.retained_send_journal_keys(
            c, [part.provider_client_id, "unmatched"]
        ) == frozenset({part.provider_client_id, "unmatched"})
        with pytest.raises(ValueError, match="bounds"):
            await d.retained_send_journal_keys(c, ["x"] * 257)
        assert (
            await d.claim_next_delivery_part(
                ChannelDeliveryPartClaimRequest(
                    delivery_id=result.plan.delivery_id, lease_id=uuid4()
                ),
                claimed_at=NOW + timedelta(minutes=4),
            )
            is None
        )
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_bound_capacity_and_history_cursor(tmp_path: Path) -> None:
    db = await _open(tmp_path / "capacity.db")
    try:
        owners: list[
            tuple[
                SQLiteExternalChannelRepository, SQLiteChannelProactiveRepository, UUID, UUID, UUID
            ]
        ] = []
        for _ in range(33):
            d, p, c, s, b = await _owner(db, policy=_enabled())
            await _input(d, c, s, b)
            owners.append((d, p, c, s, b))
        results = await asyncio.gather(
            *[p.reserve_intent(c, as_of=NOW + timedelta(minutes=2)) for _, p, c, _, _ in owners]
        )
        assert sum(result.created for result in results) == 32
        assert results[-1].reason is ChannelProactiveReason.CAPACITY_REACHED
        d, p, c, s, b = owners[0]
        first = results[0].intent
        assert first is not None
        assert len(await p.list_active_intents()) == 32
        await _input(d, c, s, b, at=NOW + timedelta(minutes=3))
        assert not (await p.reserve_intent(c, as_of=NOW + timedelta(minutes=5))).created
        await p.settle_intent(
            first.request_id, reason="cancelled", settled_at=NOW + timedelta(minutes=5), cancel=True
        )
        second = await _reserved(p, c, at=NOW + timedelta(minutes=5))
        page = await p.list_intents(c, limit=1)
        assert page.items[0].request_id == second.request_id and page.next_cursor is not None
        next_page = await p.list_intents(c, limit=1, cursor=page.next_cursor)
        assert next_page.items[0].request_id == first.request_id and next_page.next_cursor is None
        with pytest.raises(ValueError, match="cursor"):
            await p.list_intents(owners[1][2], cursor=page.next_cursor)
        page1 = await p.list_contexts(as_of=NOW, limit=32)
        page2 = await p.list_contexts(as_of=NOW, after_connection_id=page1[-1].policy.connection_id)
        assert len(page1) == 32 and len(page2) == 1
    finally:
        await db.close()


@pytest.mark.parametrize(
    "local",
    [
        datetime(2026, 3, 8, 1, 59, tzinfo=ZoneInfo("America/New_York")),
        datetime(2026, 11, 1, 1, 59, tzinfo=ZoneInfo("America/New_York"), fold=0),
        datetime(2026, 11, 1, 1, 59, tzinfo=ZoneInfo("America/New_York"), fold=1),
    ],
)
@pytest.mark.asyncio
async def test_timezone_dst_and_utc_storage(tmp_path: Path, local: datetime) -> None:
    db = await _open(tmp_path / "dst.db")
    try:
        policy = _enabled(timezone="America/New_York")
        d, p, c, s, b = await _owner(db)
        activation = local.astimezone(UTC) - timedelta(minutes=5)
        await p.update_policy(
            c,
            ChannelProactivePolicyUpdate(expected_revision=0, policy=policy),
            updated_at=activation.astimezone(ZoneInfo("Asia/Shanghai")),
        )
        anchor = await _input(d, c, s, b, at=local)
        due = local.astimezone(UTC) + timedelta(minutes=1)
        intent = await _reserved(p, c, at=due.astimezone(ZoneInfo("Asia/Shanghai")))
        assert anchor.accepted_at.utcoffset() == timedelta(0)
        assert intent.not_before_at == due and intent.expires_at == due + timedelta(minutes=15)
        assert intent.budget_day == due.astimezone(ZoneInfo(policy.timezone)).date().isoformat()
        row = await db.fetchone(
            "SELECT accepted_at FROM channel_turns WHERE channel_turn_id=?",
            (str(anchor.channel_turn_id),),
        )
        assert row is not None and str(row["accepted_at"]).endswith("+00:00")
    finally:
        await db.close()


def test_quiet_hours_local_midnight_and_dst_folds() -> None:
    policy = ChannelProactivePolicy(
        timezone="America/New_York", quiet_start="23:00", quiet_end="08:00"
    )
    for fold in (0, 1):
        assert proactive_quiet_hours(
            policy, datetime(2026, 11, 1, 1, 30, tzinfo=ZoneInfo(policy.timezone), fold=fold)
        )
    assert not proactive_quiet_hours(
        policy, datetime(2026, 11, 1, 8, 0, tzinfo=ZoneInfo(policy.timezone))
    )


@pytest.mark.asyncio
async def test_migration38_preserves_actual_old_ledger_parent_parts_and_foreign_keys(
    tmp_path: Path,
) -> None:
    path = tmp_path / "legacy37.db"
    db = await _open(path, legacy=True)
    connection_id, session_id, binding_id = uuid4(), uuid4(), uuid4()
    await db.execute(
        (
            "INSERT INTO "
            "channel_connections(connection_id,provider_id,name,character_id,principal_scope,"
            "access_token_hash,created_at,updated_at) "
            "VALUES(?,'qq_napcat','legacy','character','local','hash',?,?)"
        ),
        (str(connection_id), NOW.isoformat(), NOW.isoformat()),
    )
    await db.execute(
        (
            "INSERT INTO "
            "sessions(session_id,character_id,state,conversation_state,created_at,updated_at) "
            "VALUES(?,'character','ready','idle',?,?)"
        ),
        (str(session_id), NOW.isoformat(), NOW.isoformat()),
    )
    await db.execute(
        "INSERT INTO channel_bindings VALUES(?,?,'direct:owner','owner',?,?,?)",
        (str(binding_id), str(connection_id), str(session_id), NOW.isoformat(), NOW.isoformat()),
    )
    for status in ("pending", "sending", "delivered", "failed", "cancelled"):
        channel_turn_id, delivery_id, part_id, lease = uuid4(), uuid4(), uuid4(), uuid4()
        stamp = NOW.isoformat()
        await db.execute(
            """
            INSERT INTO channel_turns(channel_turn_id,connection_id,binding_id,external_message_id,
                content_sha256,conversation_key,sender_key,principal_scope,session_id,turn_id,generation_id,
                status,delivery_id,accepted_at,created_at,updated_at)
            VALUES(?,?,?,?,'hash','direct:owner','owner','local',?,?,?,'completed',?,?,?,?)
            """,
            (
                str(channel_turn_id),
                str(connection_id),
                str(binding_id),
                str(uuid4()),
                str(session_id),
                str(uuid4()),
                str(uuid4()),
                str(delivery_id),
                stamp,
                stamp,
                stamp,
            ),
        )
        await db.execute(
            """
            INSERT INTO channel_deliveries(delivery_id,channel_turn_id,connection_id,status,attempt,
                provider_message_id,created_at,updated_at,delivered_at,lease_id,lease_expires_at,
                plan_version,cancel_requested_at) VALUES(?,?,?,?,2,?,?,?,?,?,?,2,?)
            """,
            (
                str(delivery_id),
                str(channel_turn_id),
                str(connection_id),
                status,
                "provider" if status == "delivered" else None,
                stamp,
                stamp,
                stamp if status == "delivered" else None,
                str(lease) if status == "sending" else None,
                (NOW + timedelta(minutes=1)).isoformat() if status == "sending" else None,
                stamp if status == "cancelled" else None,
            ),
        )
        await db.execute(
            """
            INSERT INTO
            channel_delivery_parts(part_id,delivery_id,ordinal,kind,payload_json,required,
                status,attempt,lease_id,lease_expires_at,provider_client_id,provider_message_id,
                created_at,updated_at,delivered_at) VALUES(?,?,0,'text',?,1,?,2,?,?,?,?,?,?,?)
            """,
            (
                str(part_id),
                str(delivery_id),
                json.dumps(dict(kind="text", text="legacy fact")),
                status,
                str(lease) if status == "sending" else None,
                (NOW + timedelta(minutes=1)).isoformat() if status == "sending" else None,
                f"legacy-{delivery_id}",
                "provider" if status == "delivered" else None,
                stamp,
                stamp,
                stamp if status == "delivered" else None,
            ),
        )
    old_parents = [
        dict(row)
        for row in await db.fetchall("SELECT * FROM channel_deliveries ORDER BY delivery_id")
    ]
    old_parts = [
        dict(row)
        for row in await db.fetchall("SELECT * FROM channel_delivery_parts ORDER BY part_id")
    ]
    old_ledger = [
        dict(row) for row in await db.fetchall("SELECT * FROM schema_migrations ORDER BY version")
    ]
    await db.close()
    db = await _open(path)
    try:
        new_parents = [
            dict(row)
            for row in await db.fetchall("SELECT * FROM channel_deliveries ORDER BY delivery_id")
        ]
        assert [{key: row[key] for key in old_parents[0]} for row in new_parents] == old_parents
        assert all(
            row["binding_id"] == str(binding_id) and row["outbound_intent_id"] is None
            for row in new_parents
        )
        assert [
            dict(row)
            for row in await db.fetchall("SELECT * FROM channel_delivery_parts ORDER BY part_id")
        ] == old_parts
        assert [
            dict(row)
            for row in await db.fetchall(
                "SELECT * FROM schema_migrations WHERE version<38 ORDER BY version"
            )
        ] == old_ledger
        assert await db.fetchall("PRAGMA foreign_key_check") == []
        foreign_keys = await db.fetchall("PRAGMA foreign_key_list(channel_delivery_parts)")
        assert any(row["table"] == "channel_deliveries" for row in foreign_keys)
        assert (await db.fetchone("PRAGMA foreign_keys"))[0] == 1  # type: ignore[index]
        with pytest.raises(aiosqlite.IntegrityError, match="CHECK constraint"):
            await db.execute(
                "UPDATE channel_deliveries SET outbound_intent_id=? WHERE delivery_id=?",
                (str(uuid4()), str(new_parents[0]["delivery_id"])),
            )
        d = SQLiteExternalChannelRepository(db)
        assert len(await d.list_active_delivery_plans_for_binding(binding_id)) == 2
        await db.close()
        db = await _open(path)
        assert [
            dict(row)
            for row in await db.fetchall("SELECT * FROM channel_delivery_parts ORDER BY part_id")
        ] == old_parts
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_cancel_without_intent_fences_existing_episode_until_new_owner(
    tmp_path: Path,
) -> None:
    db = await _open(tmp_path / "stop.db")
    try:
        d, p, c, s, b = await _owner(db, policy=_enabled())
        await _input(d, c, s, b)
        assert (
            await p.cancel_for_binding(
                b, reason="stopped", requested_at=NOW + timedelta(seconds=30)
            )
            == ()
        )
        assert (
            await p.reserve_intent(c, as_of=NOW + timedelta(minutes=2))
        ).reason is ChannelProactiveReason.EPISODE_ALREADY_RESERVED
        await _input(d, c, s, b, at=NOW + timedelta(minutes=3))
        assert (await p.reserve_intent(c, as_of=NOW + timedelta(minutes=5))).created
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_atomic_sanitized_lifecycle_events_and_failed_audit_roll_back_budget(
    tmp_path: Path,
) -> None:
    db = await _open(tmp_path / "audit.db")
    try:
        d, p, c, s, b = await _owner(db, policy=_enabled())
        await _input(d, c, s, b)
        await db.execute(
            "CREATE TRIGGER reject_reserved_audit BEFORE INSERT ON events "
            "WHEN NEW.event_type='channel.outbound_intent_reserved' "
            "BEGIN SELECT RAISE(ABORT,'injected audit failure'); END"
        )
        with pytest.raises(aiosqlite.IntegrityError, match="injected audit failure"):
            await p.reserve_intent(c, as_of=NOW + timedelta(minutes=2))
        assert (await p.get_context(c, as_of=NOW + timedelta(minutes=2))).reserved_today == 0
        assert await p.list_active_intents() == ()
        await db.execute("DROP TRIGGER reject_reserved_audit")
        intent = await _reserved(p, c)
        assert [event.event_type for event in intent.persisted_events] == [
            "channel.outbound_intent_reserved"
        ]
        claimed = await p.claim_generation(
            intent.request_id, expected_revision=0, claimed_at=NOW + timedelta(minutes=2)
        )
        assert claimed is not None and [event.event_type for event in claimed.persisted_events] == [
            "channel.outbound_intent_generating"
        ]
        settled = await p.settle_intent(
            intent.request_id,
            reason="operator_cancelled",
            settled_at=NOW + timedelta(minutes=3),
            cancel=True,
        )
        assert [event.event_type for event in settled.persisted_events] == [
            "channel.outbound_intent_settled"
        ]
        duplicate = await p.settle_intent(
            intent.request_id, reason="ignored", settled_at=NOW + timedelta(minutes=4), cancel=True
        )
        assert duplicate.persisted_events == () and duplicate.settled_reason == "operator_cancelled"
        rows = await db.fetchall(
            "SELECT event_type,payload_json FROM events WHERE session_id=? ORDER BY sequence",
            (str(s),),
        )
        assert [row["event_type"] for row in rows] == [
            "channel.proactive_policy_updated",
            "channel.outbound_intent_reserved",
            "channel.outbound_intent_generating",
            "channel.outbound_intent_settled",
        ]
        for row in rows:
            payload = json.loads(str(row["payload_json"]))
            assert set(payload) <= {
                "connection_id",
                "binding_id",
                "outbound_intent_id",
                "source_event_key",
                "policy_revision",
                "route_revision",
                "revision",
                "status",
                "reason",
                "enabled",
            }
            assert (
                "account" not in payload and "owner" not in payload and "reply_text" not in payload
            )
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_false_policy_without_binding_has_durable_revision_and_no_fabricated_event_session(
    tmp_path: Path,
) -> None:
    db = await _open(tmp_path / "no_binding.db")
    try:
        d = SQLiteExternalChannelRepository(db)
        p = SQLiteChannelProactiveRepository(db, EventStore(db))
        c = uuid4()
        await d.create_connection(
            ChannelConnectionConfiguration(
                connection_id=c,
                provider_id="qq_napcat",
                name="unpaired",
                character_id="character",
                principal_scope="local",
                allowed_sender_keys=[],
            ),
            access_token_hash="hash",
            created_at=NOW,
        )
        result = await p.update_policy(
            c,
            ChannelProactivePolicyUpdate(expected_revision=0, policy=ChannelProactivePolicy()),
            updated_at=NOW,
        )
        assert result.revision == 1 and result.binding_id is None and result.persisted_events == ()
        assert await db.fetchall("SELECT * FROM events") == []
        row = await db.fetchone(
            "SELECT revision FROM channel_proactive_policies WHERE connection_id=?", (str(c),)
        )
        assert row is not None and row["revision"] == 1
        with pytest.raises(ValueError, match="fixed paired owner"):
            await p.update_policy(
                c,
                ChannelProactivePolicyUpdate(expected_revision=1, policy=_enabled()),
                updated_at=NOW,
            )
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_expired_outbound_part_claim_returns_its_settlement_events_without_claim(
    tmp_path: Path,
) -> None:
    db = await _open(tmp_path / "part_claim.db")
    try:
        d, p, c, s, b = await _owner(db, policy=_enabled())
        await _input(d, c, s, b)
        intent = await _reserved(p, c)
        await p.claim_generation(
            intent.request_id, expected_revision=0, claimed_at=NOW + timedelta(minutes=2)
        )
        await _completed(db, intent)
        plan = await p.create_outbound_text_plan(
            intent.request_id, reply_text="local test reply", created_at=NOW + timedelta(minutes=2)
        )
        result = await d.claim_next_delivery_part(
            ChannelDeliveryPartClaimRequest(delivery_id=plan.plan.delivery_id, lease_id=uuid4()),
            claimed_at=intent.expires_at.astimezone(ZoneInfo("Asia/Shanghai")),
        )
        assert result is not None and result.part is None and not result.applied
        assert result.plan.status is ChannelDeliveryStatus.CANCELLED
        assert (
            len(result.persisted_events) == 1
            and result.persisted_events[0].event_type == "channel.outbound_intent_settled"
        )
        assert all(part.attempt == 0 for part in result.plan.parts)
        assert (
            await d.claim_next_delivery_part(
                ChannelDeliveryPartClaimRequest(
                    delivery_id=plan.plan.delivery_id, lease_id=uuid4()
                ),
                claimed_at=intent.expires_at,
            )
            is None
        )
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_terminal_cancel_cas_requires_current_revision_even_after_settlement(
    tmp_path: Path,
) -> None:
    db = await _open(tmp_path / "terminal_cas.db")
    try:
        d, p, c, s, b = await _owner(db, policy=_enabled())
        await _input(d, c, s, b)
        intent = await _reserved(p, c)
        settled = await p.settle_intent(
            intent.request_id,
            reason="operator_cancelled",
            settled_at=NOW + timedelta(minutes=2),
            cancel=True,
            expected_revision=0,
        )
        assert settled.revision == 1
        with pytest.raises(ValueError, match="revision conflict"):
            await p.settle_intent(
                intent.request_id,
                reason="operator_cancelled",
                settled_at=NOW + timedelta(minutes=3),
                cancel=True,
                expected_revision=0,
            )
        same = await p.settle_intent(
            intent.request_id,
            reason="operator_cancelled",
            settled_at=NOW + timedelta(minutes=3),
            cancel=True,
            expected_revision=1,
        )
        assert same.revision == settled.revision and same.persisted_events == ()
    finally:
        await db.close()
