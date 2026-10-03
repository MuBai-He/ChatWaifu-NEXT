# pyright: reportPrivateUsage=false
"""SQLite owner opt-in admission, with immutable episodes and provider facts."""

from __future__ import annotations

import base64
import hashlib
import json
from dataclasses import replace
from datetime import UTC, datetime
from typing import cast
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo

import aiosqlite
from chatwaifu_protocol.channel_proactive import (
    ChannelOutboundIntentStatus,
    ChannelProactivePolicy,
    ChannelProactivePolicyUpdate,
    ChannelProactiveReason,
)
from chatwaifu_protocol.channels import ChannelDeliveryStatus, ChannelTextDeliveryPartPayload
from chatwaifu_protocol.errors import StructuredError
from chatwaifu_protocol.events import GenericCoreEvent, PrivacyLevel

from chatwaifu_runtime.external_channels.models import DeliveryTransitionResult
from chatwaifu_runtime.external_channels.proactive_models import (
    ChannelOutboundAuthorization,
    ChannelOutboundIntentRecord,
    ChannelOutboundIntentRecordPage,
    ChannelOutboundReservationResult,
    ChannelProactiveContext,
    ChannelProactivePolicyRecord,
    evaluate_proactive_context,
    proactive_quiet_hours,
)
from chatwaifu_runtime.external_channels.proactive_ports import ChannelProactiveRepository
from chatwaifu_runtime.persistence.database import Database
from chatwaifu_runtime.persistence.event_store import EventStore
from chatwaifu_runtime.persistence.sqlite_external_channels import (
    _TURN_SELECT,
    SQLiteExternalChannelRepository,
    _binding_record,
    _connection_record,
    _datetime,
    _error_from_json,
    _error_json,
    _required_datetime,
    _turn_record,
)

_INTENT_SELECT = """
SELECT i.*, d.delivery_id, d.status AS delivery_status,
       EXISTS(SELECT 1 FROM channel_delivery_parts p WHERE p.delivery_id=d.delivery_id
              AND p.status='delivered' AND p.provider_message_id IS NOT NULL)
         AS provider_receipt_present
FROM channel_outbound_intents i
LEFT JOIN channel_deliveries d ON d.outbound_intent_id=i.request_id
"""


async def _one(
    connection: aiosqlite.Connection, sql: str, parameters: tuple[object, ...] = ()
) -> aiosqlite.Row | None:
    cursor = await connection.execute(sql, parameters)
    row = await cursor.fetchone()
    await cursor.close()
    return row


async def _rows(
    connection: aiosqlite.Connection, sql: str, parameters: tuple[object, ...] = ()
) -> tuple[aiosqlite.Row, ...]:
    cursor = await connection.execute(sql, parameters)
    rows = cast(list[aiosqlite.Row], await cursor.fetchall())
    await cursor.close()
    return tuple(rows)


def _now(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("proactive timestamps must be timezone aware")
    return value.astimezone(UTC)


def _limit(value: int, maximum: int) -> int:
    if not 1 <= value <= maximum:
        raise ValueError(f"limit must be between 1 and {maximum}")
    return value


class SQLiteChannelProactiveRepository(ChannelProactiveRepository):
    def __init__(
        self,
        database: Database,
        event_store: EventStore | None = None,
        *,
        deliveries: SQLiteExternalChannelRepository | None = None,
    ) -> None:
        self._database = database
        self._events = event_store
        self._deliveries = deliveries or SQLiteExternalChannelRepository(database, event_store)
        if self._deliveries._database is not database:
            raise ValueError("proactive and delivery repositories must share one database")

    async def _intent_event_tx(
        self,
        connection: aiosqlite.Connection,
        intent: ChannelOutboundIntentRecord,
        event_type: str,
        stamp: datetime,
    ) -> ChannelOutboundIntentRecord:
        if self._events is None:
            return intent
        event = GenericCoreEvent.model_validate(
            {
                "event_id": uuid4(),
                "event_type": event_type,
                "session_id": intent.session_id,
                "turn_id": intent.turn_id,
                "generation_id": intent.generation_id,
                "occurred_at": stamp,
                "source": "runtime.external_channels",
                "privacy": PrivacyLevel.PRIVATE,
                "payload": {
                    "connection_id": str(intent.connection_id),
                    "binding_id": str(intent.binding_id),
                    "outbound_intent_id": str(intent.request_id),
                    "source_event_key": intent.source_event_key,
                    "policy_revision": intent.policy_revision,
                    "route_revision": intent.route_revision,
                    "revision": intent.revision,
                    "status": intent.status.value,
                    "reason": intent.settled_reason,
                },
            }
        )
        persisted = await self._events.append_in_transaction(connection, event)
        return replace(intent, persisted_events=(persisted,))

    async def _policy_tx(
        self, connection: aiosqlite.Connection, connection_id: UUID
    ) -> ChannelProactivePolicyRecord:
        row = await _one(
            connection,
            "SELECT * FROM channel_proactive_policies WHERE connection_id=?",
            (str(connection_id),),
        )
        if row is None:
            return ChannelProactivePolicyRecord(
                connection_id=connection_id, policy=ChannelProactivePolicy()
            )
        return ChannelProactivePolicyRecord(
            connection_id=connection_id,
            policy=ChannelProactivePolicy.model_validate_json(str(row["policy_json"])),
            revision=int(row["revision"]),
            binding_id=UUID(str(row["binding_id"])) if row["binding_id"] else None,
            authorized_route_revision=int(row["authorized_route_revision"])
            if row["authorized_route_revision"] is not None
            else None,
            updated_at=_datetime(row["updated_at"]),
        )

    async def get_policy(self, connection_id: UUID) -> ChannelProactivePolicyRecord:
        async with self._database.transaction() as connection:
            return await self._policy_tx(connection, connection_id)

    async def _context_tx(
        self, connection: aiosqlite.Connection, connection_id: UUID, as_of: datetime
    ) -> ChannelProactiveContext:
        policy = await self._policy_tx(connection, connection_id)
        row = await _one(
            connection,
            "SELECT * FROM channel_connections WHERE connection_id=?",
            (str(connection_id),),
        )
        route = _connection_record(row) if row is not None else None
        binding = None
        anchor = None
        busy = False
        episode = None
        episode_reserved = False
        pending = None
        if route is not None and len(route.configuration.allowed_sender_keys) == 1:
            config = route.configuration
            owner = config.allowed_sender_keys[0]
            bindings = await _rows(
                connection,
                """
                SELECT b.* FROM channel_bindings b JOIN sessions s ON s.session_id=b.session_id
                WHERE b.connection_id=? AND b.sender_key=? AND b.conversation_key=?
                  AND s.character_id=? AND s.user_scope=? AND s.scene_kind='private'
                LIMIT 2
                """,
                (
                    str(connection_id),
                    owner,
                    f"direct:{owner}",
                    config.character_id,
                    config.principal_scope,
                ),
            )
            if len(bindings) == 1 and config.account_key:
                binding = _binding_record(bindings[0])
                row = await _one(
                    connection,
                    _TURN_SELECT
                    + """
                    WHERE t.binding_id=? AND t.account_key=? AND t.sender_key=?
                      AND t.conversation_key=? AND t.principal_scope=? AND t.chat_type='direct'
                      AND t.accepted_at > ?
                      AND NOT EXISTS (
                        SELECT 1 FROM memory_scope_resets r
                        WHERE ((r.character_id=? AND r.user_scope=?) OR r.character_id='__all__')
                          AND t.accepted_at <= r.reset_at)
                    ORDER BY t.accepted_at DESC, t.channel_turn_id DESC LIMIT 1
                    """,
                    (
                        str(binding.binding_id),
                        config.account_key,
                        owner,
                        binding.conversation_key,
                        config.principal_scope,
                        policy.updated_at.isoformat() if policy.updated_at else "",
                        config.character_id,
                        config.principal_scope,
                    ),
                )
                anchor = _turn_record(row) if row is not None else None
                busy = (
                    await _one(
                        connection,
                        """
                    SELECT 1 WHERE EXISTS(SELECT 1 FROM channel_turns WHERE binding_id=?
                        AND status IN ('accepted','processing','cancelling'))
                      OR EXISTS(SELECT 1 FROM generations WHERE session_id=?
                        AND state NOT IN ('completed','cancelled','failed','timed_out')
                        AND invalidated_at IS NULL)
                      OR EXISTS(SELECT 1 FROM channel_deliveries WHERE binding_id=?
                        AND status IN ('pending','sending'))
                    """,
                        (str(binding.binding_id), str(binding.session_id), str(binding.binding_id)),
                    )
                    is not None
                )
                pending = await _one(
                    connection,
                    "SELECT request_id FROM channel_outbound_intents "
                    "WHERE binding_id=? AND status!='settled' LIMIT 1",
                    (str(binding.binding_id),),
                )
                if anchor is not None:
                    episode = await _one(
                        connection,
                        (
                            "SELECT * FROM channel_proactive_episodes WHERE binding_id=? AND source"
                            "='idle_check_in' AND anchor_channel_turn_id=?"
                        ),
                        (str(binding.binding_id), str(anchor.channel_turn_id)),
                    )
                    episode_reserved = (
                        await _one(
                            connection,
                            (
                                "SELECT 1 FROM channel_outbound_intents WHERE binding_id=? AND source='"
                                "idle_check_in' AND anchor_channel_turn_id=?"
                            ),
                            (str(binding.binding_id), str(anchor.channel_turn_id)),
                        )
                        is not None
                    )
        budget_day = as_of.astimezone(ZoneInfo(policy.policy.timezone)).date().isoformat()
        counts = await _one(
            connection,
            "SELECT count(*) AS today FROM channel_outbound_intents "
            "WHERE connection_id=? AND budget_day=?",
            (str(connection_id), budget_day),
        )
        latest = await _one(
            connection,
            "SELECT created_at FROM channel_outbound_intents WHERE connection_id=? "
            "ORDER BY created_at DESC,request_id DESC LIMIT 1",
            (str(connection_id),),
        )
        active = await _one(
            connection,
            "SELECT count(*) AS total FROM channel_outbound_intents WHERE status!='settled'",
        )
        return ChannelProactiveContext(
            policy=policy,
            connection=route,
            binding=binding,
            last_owner_turn=anchor,
            reserved_today=int(counts["today"]) if counts else 0,
            last_reserved_at=_datetime(latest["created_at"]) if latest else None,
            pending_request_id=UUID(str(pending["request_id"])) if pending else None,
            global_active_count=int(active["total"]) if active else 0,
            conversation_busy=busy,
            episode_not_before_at=_datetime(episode["not_before_at"]) if episode else None,
            episode_expires_at=_datetime(episode["expires_at"]) if episode else None,
            episode_reserved=episode_reserved,
            episode_revoked=episode is not None and episode["revoked_at"] is not None,
        )

    async def get_context(self, connection_id: UUID, *, as_of: datetime) -> ChannelProactiveContext:
        async with self._database.transaction() as connection:
            return await self._context_tx(connection, connection_id, _now(as_of))

    async def list_contexts(
        self, *, as_of: datetime, limit: int = 32, after_connection_id: UUID | None = None
    ) -> tuple[ChannelProactiveContext, ...]:
        async with self._database.transaction() as connection:
            rows = await _rows(
                connection,
                "SELECT connection_id FROM channel_connections "
                "WHERE provider_id='qq_napcat' AND deleted_at IS NULL AND connection_id>? "
                "ORDER BY connection_id LIMIT ?",
                (str(after_connection_id) if after_connection_id else "", _limit(limit, 32)),
            )
            return tuple(
                [
                    await self._context_tx(connection, UUID(str(row["connection_id"])), _now(as_of))
                    for row in rows
                ]
            )

    async def update_policy(
        self, connection_id: UUID, update: ChannelProactivePolicyUpdate, *, updated_at: datetime
    ) -> ChannelProactivePolicyRecord:
        updated_at = _now(updated_at)
        async with self._database.transaction() as connection:
            context = await self._context_tx(connection, connection_id, updated_at)
            route = context.connection
            if route is None or route.deleted_at is not None:
                raise KeyError(f"unknown channel connection {connection_id}")
            if route.configuration.provider_id != "qq_napcat":
                raise ValueError("proactive policy requires a QQ connection")
            if context.policy.revision != update.expected_revision:
                raise ValueError("proactive policy revision conflict")
            if update.policy.enabled and (
                not route.configuration.enabled or context.binding is None
            ):
                raise ValueError("enabled proactive policy requires a fixed paired owner binding")
            persisted_events: list[GenericCoreEvent] = []
            rows = await _rows(
                connection,
                "SELECT request_id FROM channel_outbound_intents "
                "WHERE connection_id=? AND status!='settled' LIMIT 32",
                (str(connection_id),),
            )
            for row in rows:
                settled = await self._settle_tx(
                    connection,
                    UUID(str(row["request_id"])),
                    "policy_changed",
                    updated_at,
                    cancel=True,
                )
                persisted_events.extend(settled.persisted_events)
            await connection.execute(
                (
                    "UPDATE channel_proactive_episodes SET revoked_at=COALESCE(revoked_at,?"
                    ") WHERE binding_id IN (SELECT binding_id FROM channel_bindings WHERE c"
                    "onnection_id=?)"
                ),
                (updated_at.isoformat(), str(connection_id)),
            )
            await connection.execute(
                """
                INSERT INTO channel_proactive_policies VALUES(?,?,?,?,?,?)
                ON CONFLICT(connection_id) DO UPDATE SET policy_json=excluded.policy_json,
                  revision=excluded.revision,binding_id=excluded.binding_id,
                  authorized_route_revision=excluded.authorized_route_revision,updated_at=excluded.updated_at
                """,
                (
                    str(connection_id),
                    update.policy.model_dump_json(),
                    context.policy.revision + 1,
                    str(context.binding.binding_id) if context.binding else None,
                    route.revision,
                    updated_at.isoformat(),
                ),
            )
            policy = await self._policy_tx(connection, connection_id)
            if self._events is not None and context.binding is not None:
                event = GenericCoreEvent.model_validate(
                    {
                        "event_id": uuid4(),
                        "event_type": "channel.proactive_policy_updated",
                        "session_id": context.binding.session_id,
                        "occurred_at": updated_at,
                        "source": "runtime.external_channels",
                        "privacy": PrivacyLevel.PRIVATE,
                        "payload": {
                            "connection_id": str(connection_id),
                            "binding_id": str(context.binding.binding_id),
                            "revision": policy.revision,
                            "enabled": policy.policy.enabled,
                        },
                    }
                )
                persisted_events.append(await self._events.append_in_transaction(connection, event))
            return replace(policy, persisted_events=tuple(persisted_events))

    async def reserve_intent(
        self, connection_id: UUID, *, as_of: datetime, generation_active: bool = False
    ) -> ChannelOutboundReservationResult:
        as_of = _now(as_of)
        async with self._database.transaction() as connection:
            context = await self._context_tx(connection, connection_id, as_of)
            preview = evaluate_proactive_context(
                context, as_of=as_of, generation_active=generation_active
            )
            if not preview.eligible:
                return ChannelOutboundReservationResult(None, False, preview.reason)
            route, binding, anchor = context.connection, context.binding, context.last_owner_turn
            if (
                route is None
                or binding is None
                or anchor is None
                or not route.configuration.account_key
            ):
                raise RuntimeError("eligible proactive context has no fixed owner")
            due, expiry = preview.next_eligible_at, preview.expires_at
            if due is None or expiry is None:
                raise RuntimeError("eligible proactive context has no episode window")
            await connection.execute(
                "INSERT OR IGNORE INTO channel_proactive_episodes VALUES "
                "(?,'idle_check_in',?,?,?,NULL)",
                (
                    str(binding.binding_id),
                    str(anchor.channel_turn_id),
                    due.isoformat(),
                    expiry.isoformat(),
                ),
            )
            ids = [uuid4() for _ in range(4)]
            request_id, turn_id, generation_id, audio_id = ids
            source_key = f"qq-idle:{binding.binding_id}:{anchor.channel_turn_id}"
            config = route.configuration
            await connection.execute(
                """
                INSERT INTO channel_outbound_intents(
                    request_id,connection_id,binding_id,source,source_event_key,anchor_channel_turn_id,
                    account_key,sender_key,conversation_key,character_id,principal_scope,session_id,
                    turn_id,generation_id,audio_stream_id,policy_revision,route_revision,revision,status,
                    budget_day,not_before_at,expires_at,created_at,updated_at
                ) VALUES(?,?,?,'idle_check_in',?,?,?,?,?,?,?,?,?,?,?,?,?,0,'pending',?,?,?,?,?)
                """,
                (
                    str(request_id),
                    str(connection_id),
                    str(binding.binding_id),
                    source_key,
                    str(anchor.channel_turn_id),
                    config.account_key,
                    binding.sender_key,
                    binding.conversation_key,
                    config.character_id,
                    config.principal_scope,
                    str(binding.session_id),
                    str(turn_id),
                    str(generation_id),
                    str(audio_id),
                    context.policy.revision,
                    route.revision,
                    as_of.astimezone(ZoneInfo(context.policy.policy.timezone)).date().isoformat(),
                    due.isoformat(),
                    expiry.isoformat(),
                    as_of.isoformat(),
                    as_of.isoformat(),
                ),
            )
            intent = await self._required_tx(connection, request_id)
            intent = await self._intent_event_tx(
                connection, intent, "channel.outbound_intent_reserved", as_of
            )
            return ChannelOutboundReservationResult(intent, True, ChannelProactiveReason.ELIGIBLE)

    async def _get_tx(
        self, connection: aiosqlite.Connection, request_id: UUID
    ) -> ChannelOutboundIntentRecord | None:
        row = await _one(connection, _INTENT_SELECT + " WHERE i.request_id=?", (str(request_id),))
        return _intent_record(row) if row is not None else None

    async def _required_tx(
        self, connection: aiosqlite.Connection, request_id: UUID
    ) -> ChannelOutboundIntentRecord:
        result = await self._get_tx(connection, request_id)
        if result is None:
            raise KeyError(f"unknown proactive intent {request_id}")
        return result

    async def get_intent(self, request_id: UUID) -> ChannelOutboundIntentRecord | None:
        async with self._database.transaction() as connection:
            return await self._get_tx(connection, request_id)

    async def list_intents(
        self, connection_id: UUID, *, limit: int = 50, cursor: str | None = None
    ) -> ChannelOutboundIntentRecordPage:
        limit = _limit(limit, 50)
        params: tuple[object, ...] = (str(connection_id),)
        condition = " WHERE i.connection_id=?"
        if cursor is not None:
            try:
                if len(cursor) > 256:
                    raise ValueError("cursor too long")
                raw = cast(object, json.loads(base64.urlsafe_b64decode(cursor.encode("ascii"))))
                if not isinstance(raw, list):
                    raise ValueError("invalid cursor")
                values = cast(list[object], raw)
                if len(values) != 3:
                    raise ValueError("invalid cursor")
                if str(values[0]) != str(connection_id):
                    raise ValueError("cursor connection mismatch")
                stamp = _now(datetime.fromisoformat(str(values[1]))).isoformat()
                key = str(UUID(str(values[2])))
                condition += " AND (i.created_at,i.request_id)<(?,?)"
                params += (stamp, key)
            except (ValueError, UnicodeError) as error:
                raise ValueError("invalid proactive history cursor") from error
        async with self._database.transaction() as connection:
            rows = await _rows(
                connection,
                _INTENT_SELECT
                + condition
                + " ORDER BY i.created_at DESC,i.request_id DESC LIMIT ?",
                (*params, limit + 1),
            )
        records = tuple(_intent_record(row) for row in rows[:limit])
        next_cursor = None
        if len(rows) > limit:
            last = records[-1]
            next_cursor = base64.urlsafe_b64encode(
                json.dumps(
                    [str(connection_id), last.created_at.isoformat(), str(last.request_id)],
                    separators=(",", ":"),
                ).encode()
            ).decode()
        return ChannelOutboundIntentRecordPage(records, next_cursor)

    async def list_active_intents(
        self, connection_id: UUID | None = None, *, limit: int = 32
    ) -> tuple[ChannelOutboundIntentRecord, ...]:
        condition = " WHERE i.status!='settled'"
        params: tuple[object, ...] = ()
        if connection_id is not None:
            condition += " AND i.connection_id=?"
            params = (str(connection_id),)
        async with self._database.transaction() as connection:
            rows = await _rows(
                connection,
                _INTENT_SELECT + condition + " ORDER BY i.created_at,i.request_id LIMIT ?",
                (*params, _limit(limit, 32)),
            )
            return tuple(_intent_record(row) for row in rows)

    async def _authorize_tx(
        self, connection: aiosqlite.Connection, intent: ChannelOutboundIntentRecord, as_of: datetime
    ) -> ChannelOutboundAuthorization:
        if (
            intent.status is ChannelOutboundIntentStatus.SETTLED
            or intent.cancel_requested_at is not None
        ):
            return ChannelOutboundAuthorization(False, "settled")
        if as_of < intent.not_before_at or as_of >= intent.expires_at:
            return ChannelOutboundAuthorization(False, "idle_window_expired")
        context = await self._context_tx(connection, intent.connection_id, as_of)
        policy, route, binding, anchor = (
            context.policy,
            context.connection,
            context.binding,
            context.last_owner_turn,
        )
        if not policy.policy.enabled or policy.revision != intent.policy_revision:
            return ChannelOutboundAuthorization(False, "policy_changed")
        if route is None or not route.configuration.enabled or route.deleted_at is not None:
            return ChannelOutboundAuthorization(False, "connection_unavailable")
        config = route.configuration
        if (
            route.revision != intent.route_revision
            or policy.authorized_route_revision != route.revision
            or config.provider_id != "qq_napcat"
            or route.status.value != "ready"
            or config.account_key != intent.account_key
            or config.character_id != intent.character_id
            or config.principal_scope != intent.principal_scope
            or config.allowed_sender_keys != [intent.sender_key]
        ):
            return ChannelOutboundAuthorization(False, "route_changed")
        if (
            binding is None
            or binding.binding_id != intent.binding_id
            or policy.binding_id != intent.binding_id
            or binding.session_id != intent.session_id
            or binding.sender_key != intent.sender_key
            or binding.conversation_key != intent.conversation_key
        ):
            return ChannelOutboundAuthorization(False, "owner_binding_changed")
        if (
            anchor is None
            or anchor.channel_turn_id != intent.anchor_channel_turn_id
            or context.episode_revoked
        ):
            return ChannelOutboundAuthorization(False, "owner_activity_superseded")
        if proactive_quiet_hours(policy.policy, as_of):
            return ChannelOutboundAuthorization(False, "quiet_hours")
        generation = await _one(
            connection,
            "SELECT invalidated_at,state,session_id,turn_id FROM generations WHERE generation_id=?",
            (str(intent.generation_id),),
        )
        if generation is not None and (
            generation["invalidated_at"] is not None
            or str(generation["session_id"]) != str(intent.session_id)
            or str(generation["turn_id"]) != str(intent.turn_id)
            or generation["state"] in {"failed", "cancelled", "timed_out"}
        ):
            return ChannelOutboundAuthorization(False, "generation_invalidated")
        if intent.status is ChannelOutboundIntentStatus.PLANNED and (
            generation is None or generation["state"] != "completed"
        ):
            return ChannelOutboundAuthorization(False, "generation_invalidated")
        busy = await _one(
            connection,
            """
            SELECT 1 WHERE EXISTS(SELECT 1 FROM channel_turns WHERE binding_id=?
                AND status IN ('accepted','processing','cancelling'))
              OR EXISTS(SELECT 1 FROM generations WHERE session_id=? AND generation_id!=?
                AND invalidated_at IS NULL AND state NOT IN
                ('completed','cancelled','failed','timed_out'))
            """,
            (str(intent.binding_id), str(intent.session_id), str(intent.generation_id)),
        )
        if busy is not None:
            return ChannelOutboundAuthorization(False, "conversation_busy")
        return ChannelOutboundAuthorization(True, "eligible")

    async def authorize_intent(
        self, request_id: UUID, *, as_of: datetime
    ) -> ChannelOutboundAuthorization:
        async with self._database.transaction() as connection:
            return await self._authorize_tx(
                connection, await self._required_tx(connection, request_id), _now(as_of)
            )

    async def claim_generation(
        self, request_id: UUID, *, expected_revision: int, claimed_at: datetime
    ) -> ChannelOutboundIntentRecord | None:
        claimed_at = _now(claimed_at)
        async with self._database.transaction() as connection:
            intent = await self._required_tx(connection, request_id)
            if (
                intent.status is not ChannelOutboundIntentStatus.PENDING
                or intent.revision != expected_revision
            ):
                return None
            allowed = await self._authorize_tx(connection, intent, claimed_at)
            if not allowed.allowed:
                await self._settle_tx(
                    connection, request_id, allowed.reason, claimed_at, cancel=True
                )
                return None
            await connection.execute(
                "UPDATE channel_outbound_intents SET status='generating', "
                "revision=revision+1,updated_at=? WHERE request_id=?",
                (claimed_at.isoformat(), str(request_id)),
            )
            return await self._intent_event_tx(
                connection,
                await self._required_tx(connection, request_id),
                "channel.outbound_intent_generating",
                claimed_at,
            )

    async def create_outbound_text_plan(
        self, request_id: UUID, *, reply_text: str, created_at: datetime
    ) -> DeliveryTransitionResult:
        created_at = _now(created_at)
        if not 1 <= len(reply_text) <= 2000 or not reply_text.strip():
            raise ValueError("proactive reply must contain 1 to 2000 text characters")
        async with self._database.transaction() as connection:
            intent = await self._required_tx(connection, request_id)
            if intent.delivery_id is not None:
                if intent.reply_text != reply_text:
                    raise ValueError("proactive plan reply differs from fixed generation")
                plan = await self._deliveries._get_delivery_plan_tx(connection, intent.delivery_id)
                if plan is None:
                    raise RuntimeError("proactive delivery disappeared")
                return DeliveryTransitionResult(plan, None, False, ())
            authorization = await self._authorize_tx(connection, intent, created_at)
            if not authorization.allowed:
                raise ValueError(f"proactive plan is unauthorized: {authorization.reason}")
            generation = await _one(
                connection,
                """
                SELECT g.output_text FROM generations g JOIN turns u ON u.turn_id=g.turn_id
                WHERE g.generation_id=? AND g.session_id=? AND g.turn_id=? AND g.state='completed'
                  AND g.invalidated_at IS NULL AND u.role='system' AND u.session_id=g.session_id
                  AND json_extract(u.source_context_json,'$.outbound_intent_id')=?
                """,
                (
                    str(intent.generation_id),
                    str(intent.session_id),
                    str(intent.turn_id),
                    str(request_id),
                ),
            )
            if intent.status is not ChannelOutboundIntentStatus.GENERATING or generation is None:
                raise ValueError("proactive plan requires its completed SYSTEM generation")
            if str(generation["output_text"]) != reply_text:
                raise ValueError("proactive reply does not match completed generation")
            delivery_id, part_id = uuid4(), uuid4()
            stamp = created_at.isoformat()
            await connection.execute(
                """
                INSERT INTO
                channel_deliveries(delivery_id,outbound_intent_id,connection_id,binding_id,
                    status,attempt,created_at,updated_at) VALUES(?,?,?,?,'pending',1,?,?)
                """,
                (
                    str(delivery_id),
                    str(request_id),
                    str(intent.connection_id),
                    str(intent.binding_id),
                    stamp,
                    stamp,
                ),
            )
            await connection.execute(
                """
                INSERT INTO
                channel_delivery_parts(part_id,delivery_id,ordinal,kind,payload_json,required,
                    status,provider_client_id,created_at,updated_at)
                VALUES(?,?,0,'text',?,1,'pending',?,?,?)
                """,
                (
                    str(part_id),
                    str(delivery_id),
                    ChannelTextDeliveryPartPayload(text=reply_text).model_dump_json(),
                    f"chatwaifu-{delivery_id.hex}-000",
                    stamp,
                    stamp,
                ),
            )
            await connection.execute(
                "UPDATE channel_outbound_intents SET status='planned',reply_text=?, "
                "reply_sha256=?,revision=revision+1,updated_at=? WHERE request_id=?",
                (
                    reply_text,
                    hashlib.sha256(reply_text.encode()).hexdigest(),
                    stamp,
                    str(request_id),
                ),
            )
            events: tuple[GenericCoreEvent, ...] = ()
            if self._events is not None:
                event = GenericCoreEvent.model_validate(
                    dict(
                        event_id=uuid4(),
                        event_type="channel.delivery_plan_created",
                        session_id=intent.session_id,
                        turn_id=intent.turn_id,
                        generation_id=intent.generation_id,
                        occurred_at=created_at,
                        source="runtime.external_channels",
                        privacy=PrivacyLevel.PRIVATE,
                        payload=dict(
                            connection_id=str(intent.connection_id),
                            channel_turn_id=None,
                            outbound_intent_id=str(request_id),
                            delivery_id=str(delivery_id),
                            part_count=1,
                            chat_type="direct",
                            conversation_key=intent.conversation_key,
                            sender_key=intent.sender_key,
                        ),
                    )
                )
                events = (await self._events.append_in_transaction(connection, event),)
            plan = await self._deliveries._get_delivery_plan_tx(connection, delivery_id)
            if plan is None:
                raise RuntimeError("proactive plan disappeared")
            return DeliveryTransitionResult(plan, None, True, events)

    async def _settle_tx(
        self,
        connection: aiosqlite.Connection,
        request_id: UUID,
        reason: str,
        stamp: datetime,
        *,
        cancel: bool,
        error: StructuredError | None = None,
    ) -> ChannelOutboundIntentRecord:
        intent = await self._required_tx(connection, request_id)
        if intent.status is ChannelOutboundIntentStatus.SETTLED:
            return intent
        if cancel and intent.delivery_id is not None:
            await connection.execute(
                "UPDATE channel_deliveries SET "
                "cancel_requested_at=COALESCE(cancel_requested_at,?), "
                "updated_at=? WHERE delivery_id=?",
                (stamp.isoformat(), stamp.isoformat(), str(intent.delivery_id)),
            )
            await connection.execute(
                "UPDATE channel_delivery_parts SET status='cancelled',updated_at=? "
                "WHERE delivery_id=? AND status='pending'",
                (stamp.isoformat(), str(intent.delivery_id)),
            )
            await self._deliveries._derive_delivery_plan_state_tx(
                connection, intent.delivery_id, stamp
            )
        await connection.execute(
            """
            UPDATE channel_outbound_intents SET status='settled',settled_at=?,settled_reason=?,
                cancel_requested_at=CASE WHEN ? THEN COALESCE(cancel_requested_at,?) ELSE
                cancel_requested_at END,
                cancel_reason=CASE WHEN ? THEN COALESCE(cancel_reason,?) ELSE cancel_reason END,
                error_json=COALESCE(?,error_json),revision=revision+1,updated_at=? WHERE
                request_id=?
            """,
            (
                stamp.isoformat(),
                reason,
                int(cancel),
                stamp.isoformat(),
                int(cancel),
                reason,
                _error_json(error),
                stamp.isoformat(),
                str(request_id),
            ),
        )
        return await self._intent_event_tx(
            connection,
            await self._required_tx(connection, request_id),
            "channel.outbound_intent_settled",
            stamp,
        )

    async def settle_intent(
        self,
        request_id: UUID,
        *,
        reason: str,
        settled_at: datetime,
        cancel: bool = False,
        expected_revision: int | None = None,
        error: StructuredError | None = None,
    ) -> ChannelOutboundIntentRecord:
        if not 1 <= len(reason) <= 128:
            raise ValueError("settlement reason must contain 1 to 128 characters")
        async with self._database.transaction() as connection:
            current = await self._required_tx(connection, request_id)
            if current.status is ChannelOutboundIntentStatus.SETTLED:
                return current
            if expected_revision is not None and current.revision != expected_revision:
                raise ValueError("proactive intent revision conflict")
            return await self._settle_tx(
                connection, request_id, reason, _now(settled_at), cancel=cancel, error=error
            )

    async def _cancel(
        self, column: str, key: UUID, *, reason: str, requested_at: datetime
    ) -> tuple[ChannelOutboundIntentRecord, ...]:
        if not 1 <= len(reason) <= 128:
            raise ValueError("cancellation reason must contain 1 to 128 characters")
        async with self._database.transaction() as connection:
            episode_condition = (
                "binding_id=?"
                if column == "binding_id"
                else (
                    "binding_id IN (SELECT binding_id FROM channel_bindings WHERE connection_id=?)"
                )
            )
            await connection.execute(
                "UPDATE channel_proactive_episodes SET revoked_at=COALESCE(revoked_at,?) WHERE "
                + episode_condition,
                (_now(requested_at).isoformat(), str(key)),
            )
            rows = await _rows(
                connection,
                f"SELECT request_id FROM channel_outbound_intents WHERE {column}=? "
                "AND status!='settled' ORDER BY created_at,request_id LIMIT 32",
                (str(key),),
            )
            return tuple(
                [
                    await self._settle_tx(
                        connection,
                        UUID(str(row["request_id"])),
                        reason,
                        _now(requested_at),
                        cancel=True,
                    )
                    for row in rows
                ]
            )

    async def cancel_for_connection(
        self, connection_id: UUID, *, reason: str, requested_at: datetime
    ) -> tuple[ChannelOutboundIntentRecord, ...]:
        return await self._cancel(
            "connection_id", connection_id, reason=reason, requested_at=requested_at
        )

    async def cancel_for_binding(
        self, binding_id: UUID, *, reason: str, requested_at: datetime
    ) -> tuple[ChannelOutboundIntentRecord, ...]:
        return await self._cancel(
            "binding_id", binding_id, reason=reason, requested_at=requested_at
        )

    async def sync_delivery_result(
        self, request_id: UUID, *, updated_at: datetime
    ) -> ChannelOutboundIntentRecord:
        async with self._database.transaction() as connection:
            intent = await self._required_tx(connection, request_id)
            if intent.delivery_status in {
                ChannelDeliveryStatus.DELIVERED,
                ChannelDeliveryStatus.FAILED,
                ChannelDeliveryStatus.CANCELLED,
            }:
                return await self._settle_tx(
                    connection,
                    request_id,
                    str(intent.delivery_status),
                    _now(updated_at),
                    cancel=False,
                )
            return intent

    async def is_channel_session(self, session_id: UUID) -> bool:
        async with self._database.transaction() as connection:
            return (
                await _one(
                    connection,
                    "SELECT 1 WHERE EXISTS(SELECT 1 FROM channel_bindings WHERE session_id=?) "
                    "OR EXISTS(SELECT 1 FROM channel_turns WHERE session_id=?) "
                    "OR EXISTS(SELECT 1 FROM channel_outbound_intents WHERE session_id=?)",
                    (str(session_id), str(session_id), str(session_id)),
                )
                is not None
            )


def _intent_record(row: aiosqlite.Row) -> ChannelOutboundIntentRecord:
    return ChannelOutboundIntentRecord(
        request_id=UUID(str(row["request_id"])),
        connection_id=UUID(str(row["connection_id"])),
        binding_id=UUID(str(row["binding_id"])),
        source_event_key=str(row["source_event_key"]),
        anchor_channel_turn_id=UUID(str(row["anchor_channel_turn_id"])),
        account_key=str(row["account_key"]),
        sender_key=str(row["sender_key"]),
        conversation_key=str(row["conversation_key"]),
        character_id=str(row["character_id"]),
        principal_scope=str(row["principal_scope"]),
        session_id=UUID(str(row["session_id"])),
        turn_id=UUID(str(row["turn_id"])),
        generation_id=UUID(str(row["generation_id"])),
        audio_stream_id=UUID(str(row["audio_stream_id"])),
        policy_revision=int(row["policy_revision"]),
        route_revision=int(row["route_revision"]),
        revision=int(row["revision"]),
        status=ChannelOutboundIntentStatus(str(row["status"])),
        budget_day=str(row["budget_day"]),
        not_before_at=_required_datetime(row["not_before_at"]),
        expires_at=_required_datetime(row["expires_at"]),
        created_at=_required_datetime(row["created_at"]),
        updated_at=_required_datetime(row["updated_at"]),
        settled_at=_datetime(row["settled_at"]),
        settled_reason=str(row["settled_reason"]) if row["settled_reason"] else None,
        cancel_requested_at=_datetime(row["cancel_requested_at"]),
        cancel_reason=str(row["cancel_reason"]) if row["cancel_reason"] else None,
        reply_text=str(row["reply_text"]) if row["reply_text"] else None,
        reply_sha256=str(row["reply_sha256"]) if row["reply_sha256"] else None,
        delivery_id=UUID(str(row["delivery_id"])) if row["delivery_id"] else None,
        delivery_status=ChannelDeliveryStatus(str(row["delivery_status"]))
        if row["delivery_status"]
        else None,
        provider_receipt_present=bool(row["provider_receipt_present"]),
        error=_error_from_json(row["error_json"]),
    )
