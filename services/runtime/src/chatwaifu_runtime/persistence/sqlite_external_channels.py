"""SQLite adapter for the External Channel Gateway repository port."""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import cast
from uuid import UUID, uuid4

import aiosqlite
from chatwaifu_protocol.channels import (
    ChannelChatType,
    ChannelConnectionConfiguration,
    ChannelConnectionStatus,
    ChannelDeliveryAcknowledgement,
    ChannelDeliveryClaimRequest,
    ChannelDeliveryPartAcknowledgement,
    ChannelDeliveryPartClaimRequest,
    ChannelDeliveryPartDraft,
    ChannelDeliveryPartKind,
    ChannelDeliveryPartPayload,
    ChannelDeliveryPartsCancelRequest,
    ChannelDeliveryPartStatus,
    ChannelDeliveryStatus,
    ChannelTextDeliveryPartPayload,
    ChannelTurnStatus,
)
from chatwaifu_protocol.errors import StructuredError
from chatwaifu_protocol.events import GenericCoreEvent, PrivacyLevel
from pydantic import TypeAdapter

from chatwaifu_runtime.external_channels.models import (
    ChannelBindingRecord,
    ChannelConnectionRecord,
    ChannelDeliveryPartDeferRequest,
    ChannelDeliveryPartRecord,
    ChannelDeliveryPlanRecord,
    ChannelDeliveryRecord,
    ChannelTurnRecord,
    CompleteTurnResult,
    DeliveryTransitionResult,
)
from chatwaifu_runtime.external_channels.ports import ExternalChannelRepository
from chatwaifu_runtime.persistence.database import Database
from chatwaifu_runtime.persistence.event_store import EventStore

_PART_PAYLOAD_ADAPTER: TypeAdapter[ChannelDeliveryPartPayload] = TypeAdapter(
    ChannelDeliveryPartPayload
)


@dataclass(frozen=True, slots=True)
class _TurnDeliveryContext:
    session_id: UUID
    turn_id: UUID
    generation_id: UUID
    connection_id: UUID
    channel_turn_id: UUID
    chat_type: ChannelChatType
    conversation_key: str
    sender_key: str


def _single_part_draft(reply_text: str) -> tuple[ChannelDeliveryPartDraft, ...]:
    return (
        ChannelDeliveryPartDraft(
            ordinal=0,
            kind=ChannelDeliveryPartKind.TEXT,
            payload=ChannelTextDeliveryPartPayload(text=reply_text),
            required=True,
        ),
    )


class SQLiteExternalChannelRepository(ExternalChannelRepository):
    def __init__(self, database: Database, event_store: EventStore | None = None) -> None:
        self._database = database
        self._event_store = event_store

    async def list_connections(self) -> tuple[ChannelConnectionRecord, ...]:
        rows = await self._database.fetchall(
            """
            SELECT * FROM channel_connections
            WHERE deleted_at IS NULL ORDER BY created_at ASC
            """
        )
        return tuple(_connection_record(row) for row in rows)

    async def get_connection(self, connection_id: UUID) -> ChannelConnectionRecord | None:
        row = await self._database.fetchone(
            """
            SELECT * FROM channel_connections
            WHERE connection_id = ? AND deleted_at IS NULL
            """,
            (str(connection_id),),
        )
        return _connection_record(row) if row is not None else None

    async def create_connection(
        self,
        configuration: ChannelConnectionConfiguration,
        *,
        access_token_hash: str,
        created_at: datetime,
    ) -> ChannelConnectionRecord:
        status = (
            ChannelConnectionStatus.UNTESTED
            if configuration.enabled
            else ChannelConnectionStatus.DISABLED
        )
        try:
            await self._database.execute(
                """
                INSERT INTO channel_connections(
                    connection_id, provider_id, name, character_id, principal_scope,
                    account_key, allowed_sender_keys_json, enabled, timeout_seconds,
                    access_token_hash, status, revision, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?)
                """,
                (
                    str(configuration.connection_id),
                    configuration.provider_id,
                    configuration.name,
                    configuration.character_id,
                    configuration.principal_scope,
                    configuration.account_key,
                    json.dumps(configuration.allowed_sender_keys, ensure_ascii=False),
                    int(configuration.enabled),
                    configuration.timeout_seconds,
                    access_token_hash,
                    status.value,
                    created_at.isoformat(),
                    created_at.isoformat(),
                ),
            )
        except aiosqlite.IntegrityError as error:
            raise ValueError(
                f"channel connection {configuration.connection_id} already exists"
            ) from error
        created = await self.get_connection(configuration.connection_id)
        if created is None:
            raise RuntimeError("channel connection disappeared after creation")
        return created

    async def update_connection(
        self,
        configuration: ChannelConnectionConfiguration,
        *,
        expected_revision: int,
        access_token_hash: str | None,
        updated_at: datetime,
    ) -> ChannelConnectionRecord:
        async with self._database.transaction() as connection:
            cursor = await connection.execute(
                """
                SELECT revision, access_token_hash FROM channel_connections
                WHERE connection_id = ? AND deleted_at IS NULL
                """,
                (str(configuration.connection_id),),
            )
            current = await cursor.fetchone()
            await cursor.close()
            if current is None:
                raise KeyError(f"unknown channel connection {configuration.connection_id}")
            if int(current["revision"]) != expected_revision:
                raise ValueError("channel connection revision conflict")
            token_hash = access_token_hash or str(current["access_token_hash"])
            status = (
                ChannelConnectionStatus.UNTESTED
                if configuration.enabled
                else ChannelConnectionStatus.DISABLED
            )
            await connection.execute(
                """
                UPDATE channel_connections SET
                    provider_id = ?, name = ?, character_id = ?, principal_scope = ?,
                    account_key = ?, allowed_sender_keys_json = ?, enabled = ?,
                    timeout_seconds = ?, access_token_hash = ?, status = ?,
                    last_error_json = NULL, revision = revision + 1, updated_at = ?
                WHERE connection_id = ? AND revision = ? AND deleted_at IS NULL
                """,
                (
                    configuration.provider_id,
                    configuration.name,
                    configuration.character_id,
                    configuration.principal_scope,
                    configuration.account_key,
                    json.dumps(configuration.allowed_sender_keys, ensure_ascii=False),
                    int(configuration.enabled),
                    configuration.timeout_seconds,
                    token_hash,
                    status.value,
                    updated_at.isoformat(),
                    str(configuration.connection_id),
                    expected_revision,
                ),
            )
        updated = await self.get_connection(configuration.connection_id)
        if updated is None:
            raise RuntimeError("channel connection disappeared after update")
        return updated

    async def soft_delete_connection(self, connection_id: UUID, *, deleted_at: datetime) -> bool:
        async with self._database.transaction() as connection:
            cursor = await connection.execute(
                """
                SELECT 1 FROM channel_turns
                WHERE connection_id = ? AND status IN ('accepted', 'processing', 'cancelling')
                LIMIT 1
                """,
                (str(connection_id),),
            )
            inflight = await cursor.fetchone()
            await cursor.close()
            if inflight is not None:
                raise ValueError("cannot delete a channel connection with an active turn")
            result = await connection.execute(
                """
                UPDATE channel_connections
                SET enabled = 0, status = 'disabled', deleted_at = ?, updated_at = ?,
                    revision = revision + 1
                WHERE connection_id = ? AND deleted_at IS NULL
                """,
                (deleted_at.isoformat(), deleted_at.isoformat(), str(connection_id)),
            )
            changed = result.rowcount > 0
            await result.close()
        return changed

    async def touch_connection(
        self,
        connection_id: UUID,
        *,
        status: ChannelConnectionStatus,
        seen_at: datetime,
        last_error: StructuredError | None = None,
    ) -> ChannelConnectionRecord:
        await self._database.execute(
            """
            UPDATE channel_connections
            SET status = ?, last_error_json = ?, last_seen_at = ?, updated_at = ?
            WHERE connection_id = ? AND deleted_at IS NULL
            """,
            (
                status.value,
                _error_json(last_error),
                seen_at.isoformat(),
                seen_at.isoformat(),
                str(connection_id),
            ),
        )
        updated = await self.get_connection(connection_id)
        if updated is None:
            raise KeyError(f"unknown channel connection {connection_id}")
        return updated

    async def set_connection_status(
        self,
        connection_id: UUID,
        *,
        status: ChannelConnectionStatus,
        last_error: StructuredError | None,
        updated_at: datetime,
    ) -> ChannelConnectionRecord:
        await self._database.execute(
            """
            UPDATE channel_connections
            SET status = ?, last_error_json = ?, updated_at = ?
            WHERE connection_id = ? AND deleted_at IS NULL
            """,
            (
                status.value,
                _error_json(last_error),
                updated_at.isoformat(),
                str(connection_id),
            ),
        )
        updated = await self.get_connection(connection_id)
        if updated is None:
            raise KeyError(f"unknown channel connection {connection_id}")
        return updated

    async def get_adapter_cursor(self, connection_id: UUID) -> str:
        row = await self._database.fetchone(
            """
            SELECT cursor FROM channel_adapter_checkpoints
            WHERE connection_id = ?
            """,
            (str(connection_id),),
        )
        return str(row["cursor"]) if row is not None else ""

    async def set_adapter_cursor(
        self,
        connection_id: UUID,
        *,
        cursor: str,
        updated_at: datetime,
    ) -> None:
        await self._database.execute(
            """
            INSERT INTO channel_adapter_checkpoints(connection_id, cursor, updated_at)
            VALUES (?, ?, ?)
            ON CONFLICT(connection_id) DO UPDATE SET
                cursor = excluded.cursor,
                updated_at = excluded.updated_at
            """,
            (str(connection_id), cursor, updated_at.isoformat()),
        )

    async def find_binding(
        self, connection_id: UUID, conversation_key: str
    ) -> ChannelBindingRecord | None:
        row = await self._database.fetchone(
            """
            SELECT * FROM channel_bindings
            WHERE connection_id = ? AND conversation_key = ?
            """,
            (str(connection_id), conversation_key),
        )
        return _binding_record(row) if row is not None else None

    async def create_binding(
        self,
        *,
        binding_id: UUID,
        connection_id: UUID,
        conversation_key: str,
        sender_key: str,
        session_id: UUID,
        created_at: datetime,
    ) -> ChannelBindingRecord:
        await self._database.execute(
            """
            INSERT INTO channel_bindings(
                binding_id, connection_id, conversation_key, sender_key,
                session_id, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                str(binding_id),
                str(connection_id),
                conversation_key,
                sender_key,
                str(session_id),
                created_at.isoformat(),
                created_at.isoformat(),
            ),
        )
        created = await self.find_binding(connection_id, conversation_key)
        if created is None:
            raise RuntimeError("channel binding disappeared after creation")
        return created

    async def find_turn_by_external_message(
        self, connection_id: UUID, external_message_id: str
    ) -> ChannelTurnRecord | None:
        row = await self._database.fetchone(
            _TURN_SELECT + " WHERE t.connection_id = ? AND t.external_message_id = ?",
            (str(connection_id), external_message_id),
        )
        return _turn_record(row) if row is not None else None

    async def get_turn(self, channel_turn_id: UUID) -> ChannelTurnRecord | None:
        row = await self._database.fetchone(
            _TURN_SELECT + " WHERE t.channel_turn_id = ?",
            (str(channel_turn_id),),
        )
        return _turn_record(row) if row is not None else None

    async def list_inflight_turns(
        self, connection_id: UUID | None = None
    ) -> tuple[ChannelTurnRecord, ...]:
        if connection_id is not None:
            rows = await self._database.fetchall(
                _TURN_SELECT
                + " WHERE t.status IN ('accepted', 'processing', 'cancelling')"
                + " AND t.connection_id = ?"
                + " ORDER BY t.created_at ASC",
                (str(connection_id),),
            )
        else:
            rows = await self._database.fetchall(
                _TURN_SELECT
                + " WHERE t.status IN ('accepted', 'processing', 'cancelling')"
                + " ORDER BY t.created_at ASC"
            )
        return tuple(_turn_record(row) for row in rows)

    async def has_inflight_turn(self, binding_id: UUID) -> bool:
        row = await self._database.fetchone(
            """
            SELECT 1 FROM channel_turns
            WHERE binding_id = ? AND status IN ('accepted', 'processing', 'cancelling')
            LIMIT 1
            """,
            (str(binding_id),),
        )
        return row is not None

    async def create_turn(self, turn: ChannelTurnRecord) -> ChannelTurnRecord:
        await self._database.execute(
            """
            INSERT INTO channel_turns(
                channel_turn_id, connection_id, binding_id, external_message_id,
                content_sha256, account_key, conversation_key, chat_type,
                conversation_label, sender_key, sender_display_name, principal_scope,
                session_id, turn_id, generation_id, status, reply_text, error_json,
                delivery_id, revision, accepted_at, created_at, updated_at, completed_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                str(turn.channel_turn_id),
                str(turn.connection_id),
                str(turn.binding_id),
                turn.external_message_id,
                turn.content_sha256,
                turn.account_key,
                turn.conversation_key,
                turn.chat_type.value,
                turn.conversation_label,
                turn.sender_key,
                turn.sender_display_name,
                turn.principal_scope,
                str(turn.session_id),
                str(turn.turn_id),
                str(turn.generation_id),
                turn.status.value,
                turn.reply_text,
                _error_json(turn.error),
                str(turn.delivery_id) if turn.delivery_id is not None else None,
                turn.revision,
                turn.accepted_at.isoformat(),
                turn.created_at.isoformat(),
                turn.updated_at.isoformat(),
                turn.completed_at.isoformat() if turn.completed_at is not None else None,
            ),
        )
        created = await self.get_turn(turn.channel_turn_id)
        if created is None:
            raise RuntimeError("channel turn disappeared after creation")
        return created

    async def set_turn_processing(
        self, channel_turn_id: UUID, *, updated_at: datetime
    ) -> ChannelTurnRecord:
        await self._update_turn_status(
            channel_turn_id,
            status=ChannelTurnStatus.PROCESSING,
            error=None,
            updated_at=updated_at,
        )
        return await self._required_turn(channel_turn_id)

    async def complete_turn(
        self,
        channel_turn_id: UUID,
        *,
        reply_text: str,
        delivery_id: UUID,
        completed_at: datetime,
        parts: Sequence[ChannelDeliveryPartDraft] | None = None,
    ) -> CompleteTurnResult:
        if not reply_text:
            raise ValueError("reply text cannot be empty")
        if parts is None:
            draft_parts = _single_part_draft(reply_text)
        else:
            if not parts:
                raise ValueError("delivery parts cannot be empty")
            for idx, draft in enumerate(parts):
                if draft.ordinal != idx:
                    raise ValueError(
                        f"delivery plan part ordinals must be strictly continuous "
                        f"starting from 0 (expected {idx}, got {draft.ordinal})"
                    )
                if draft.kind != draft.payload.kind:
                    raise ValueError("part kind does not match payload kind")
                if not draft.required:
                    raise ValueError("Phase 17.1A only supports required parts")
                if draft.kind is not ChannelDeliveryPartKind.TEXT:
                    raise ValueError(f"Phase 17.1A only supports text parts, got {draft.kind}")
            draft_parts = parts

        persisted_events: list[GenericCoreEvent] = []
        async with self._database.transaction() as connection:
            cursor = await connection.execute(
                "SELECT delivery_id, status FROM channel_turns WHERE channel_turn_id = ?",
                (str(channel_turn_id),),
            )
            row = await cursor.fetchone()
            await cursor.close()
            if row is None:
                raise KeyError(f"unknown channel turn {channel_turn_id}")
            existing_delivery = row["delivery_id"]
            if existing_delivery is None:
                await connection.execute(
                    """
                    INSERT INTO channel_deliveries(
                        delivery_id, channel_turn_id, connection_id, status,
                        attempt, created_at, updated_at, plan_version, cancel_requested_at
                    )
                    SELECT ?, channel_turn_id, connection_id, 'pending', 1, ?, ?, 1, NULL
                    FROM channel_turns WHERE channel_turn_id = ?
                    """,
                    (
                        str(delivery_id),
                        completed_at.isoformat(),
                        completed_at.isoformat(),
                        str(channel_turn_id),
                    ),
                )
                resolved_delivery = delivery_id
                for draft in draft_parts:
                    part_id = uuid4()
                    provider_client_id = f"chatwaifu-{resolved_delivery.hex}-{draft.ordinal:03d}"
                    await connection.execute(
                        """
                        INSERT INTO channel_delivery_parts(
                            part_id, delivery_id, ordinal, kind, payload_json, required,
                            status, delay_after_ms, not_before_at, attempt, lease_id,
                            lease_expires_at, provider_client_id, provider_message_id,
                            last_error_json, created_at, updated_at, delivered_at
                        ) VALUES (
                            ?, ?, ?, ?, ?, ?, 'pending', ?, ?, 0, NULL, NULL,
                            ?, NULL, NULL, ?, ?, NULL
                        )
                        """,
                        (
                            str(part_id),
                            str(resolved_delivery),
                            draft.ordinal,
                            draft.kind.value,
                            draft.payload.model_dump_json(),
                            1 if draft.required else 0,
                            draft.delay_after_ms,
                            draft.not_before_at.isoformat()
                            if draft.not_before_at is not None
                            else None,
                            provider_client_id,
                            completed_at.isoformat(),
                            completed_at.isoformat(),
                        ),
                    )
                if self._event_store is not None:
                    ctx = await self._get_turn_context_tx(connection, resolved_delivery)
                    if ctx is not None:
                        persisted = await self._event_store.append_in_transaction(
                            connection,
                            GenericCoreEvent.model_validate(
                                {
                                    "event_id": uuid4(),
                                    "event_type": "channel.delivery_plan_created",
                                    "session_id": ctx.session_id,
                                    "turn_id": ctx.turn_id,
                                    "generation_id": ctx.generation_id,
                                    "occurred_at": completed_at,
                                    "source": "runtime.external_channels",
                                    "privacy": PrivacyLevel.PRIVATE,
                                    "payload": {
                                        "connection_id": str(ctx.connection_id),
                                        "channel_turn_id": str(ctx.channel_turn_id),
                                        "delivery_id": str(resolved_delivery),
                                        "part_count": len(draft_parts),
                                        "chat_type": ctx.chat_type.value,
                                        "conversation_key": ctx.conversation_key,
                                        "sender_key": ctx.sender_key,
                                    },
                                }
                            ),
                        )
                        persisted_events.append(persisted)
            else:
                resolved_delivery = UUID(str(existing_delivery))
            await connection.execute(
                """
                UPDATE channel_turns SET status = 'completed', reply_text = ?,
                    error_json = NULL, delivery_id = ?, revision = revision + 1,
                    updated_at = ?, completed_at = COALESCE(completed_at, ?)
                WHERE channel_turn_id = ?
                """,
                (
                    reply_text,
                    str(resolved_delivery),
                    completed_at.isoformat(),
                    completed_at.isoformat(),
                    str(channel_turn_id),
                ),
            )
        turn_rec = await self._required_turn(channel_turn_id)
        return CompleteTurnResult(turn=turn_rec, persisted_events=tuple(persisted_events))

    async def create_delivery_plan(
        self,
        channel_turn_id: UUID,
        *,
        delivery_id: UUID,
        parts: Sequence[ChannelDeliveryPartDraft],
        created_at: datetime,
    ) -> DeliveryTransitionResult:
        if not parts:
            raise ValueError("delivery plan parts cannot be empty")
        for idx, draft in enumerate(parts):
            if draft.ordinal != idx:
                raise ValueError(
                    f"delivery plan part ordinals must be strictly continuous "
                    f"starting from 0 (expected {idx}, got {draft.ordinal})"
                )
            if draft.kind != draft.payload.kind:
                raise ValueError("part kind does not match payload kind")
            if not draft.required:
                raise ValueError("Phase 17.1A only supports required parts")
            if draft.kind is not ChannelDeliveryPartKind.TEXT:
                raise ValueError(f"Phase 17.1A only supports text parts, got {draft.kind}")

        persisted_events: list[GenericCoreEvent] = []
        async with self._database.transaction() as connection:
            cursor = await connection.execute(
                "SELECT connection_id, delivery_id FROM channel_turns WHERE channel_turn_id = ?",
                (str(channel_turn_id),),
            )
            turn_row = await cursor.fetchone()
            await cursor.close()
            if turn_row is None:
                raise KeyError(f"unknown channel turn {channel_turn_id}")
            connection_id = str(turn_row["connection_id"])

            cursor = await connection.execute(
                "SELECT delivery_id FROM channel_deliveries WHERE delivery_id = ?",
                (str(delivery_id),),
            )
            existing_delivery = await cursor.fetchone()
            await cursor.close()
            if existing_delivery is not None:
                raise ValueError(f"delivery {delivery_id} already exists")

            await connection.execute(
                """
                INSERT INTO channel_deliveries(
                    delivery_id, channel_turn_id, connection_id, status,
                    attempt, created_at, updated_at, plan_version, cancel_requested_at
                ) VALUES (?, ?, ?, 'pending', 1, ?, ?, 1, NULL)
                """,
                (
                    str(delivery_id),
                    str(channel_turn_id),
                    connection_id,
                    created_at.isoformat(),
                    created_at.isoformat(),
                ),
            )

            for draft in parts:
                part_id = uuid4()
                provider_client_id = f"chatwaifu-{delivery_id.hex}-{draft.ordinal:03d}"
                await connection.execute(
                    """
                    INSERT INTO channel_delivery_parts(
                        part_id, delivery_id, ordinal, kind, payload_json, required,
                        status, delay_after_ms, not_before_at, attempt, lease_id,
                        lease_expires_at, provider_client_id, provider_message_id,
                        last_error_json, created_at, updated_at, delivered_at
                    ) VALUES (
                            ?, ?, ?, ?, ?, ?, 'pending', ?, ?, 0, NULL, NULL,
                            ?, NULL, NULL, ?, ?, NULL
                        )
                    """,
                    (
                        str(part_id),
                        str(delivery_id),
                        draft.ordinal,
                        draft.kind.value,
                        draft.payload.model_dump_json(),
                        1 if draft.required else 0,
                        draft.delay_after_ms,
                        draft.not_before_at.isoformat()
                        if draft.not_before_at is not None
                        else None,
                        provider_client_id,
                        created_at.isoformat(),
                        created_at.isoformat(),
                    ),
                )

            await connection.execute(
                """
                UPDATE channel_turns
                SET delivery_id = ?, updated_at = ?
                WHERE channel_turn_id = ?
                """,
                (str(delivery_id), created_at.isoformat(), str(channel_turn_id)),
            )

            if self._event_store is not None:
                ctx = await self._get_turn_context_tx(connection, delivery_id)
                if ctx is not None:
                    persisted = await self._event_store.append_in_transaction(
                        connection,
                        GenericCoreEvent.model_validate(
                            {
                                "event_id": uuid4(),
                                "event_type": "channel.delivery_plan_created",
                                "session_id": ctx.session_id,
                                "turn_id": ctx.turn_id,
                                "generation_id": ctx.generation_id,
                                "occurred_at": created_at,
                                "source": "runtime.external_channels",
                                "privacy": PrivacyLevel.PRIVATE,
                                "payload": {
                                    "connection_id": str(ctx.connection_id),
                                    "channel_turn_id": str(ctx.channel_turn_id),
                                    "delivery_id": str(delivery_id),
                                    "part_count": len(parts),
                                    "chat_type": ctx.chat_type.value,
                                    "conversation_key": ctx.conversation_key,
                                    "sender_key": ctx.sender_key,
                                },
                            }
                        ),
                    )
                    persisted_events.append(persisted)

            plan = await self._get_delivery_plan_tx(connection, delivery_id)
            if plan is None:
                raise RuntimeError("delivery plan disappeared after creation")
            return DeliveryTransitionResult(
                plan=plan,
                part=None,
                applied=True,
                persisted_events=tuple(persisted_events),
            )

    async def get_delivery_plan(self, delivery_id: UUID) -> ChannelDeliveryPlanRecord | None:
        async with self._database.transaction() as connection:
            return await self._get_delivery_plan_tx(connection, delivery_id)

    async def get_delivery_plan_by_turn(
        self, channel_turn_id: UUID
    ) -> ChannelDeliveryPlanRecord | None:
        async with self._database.transaction() as connection:
            cursor = await connection.execute(
                "SELECT delivery_id FROM channel_turns WHERE channel_turn_id = ?",
                (str(channel_turn_id),),
            )
            row = await cursor.fetchone()
            await cursor.close()
            if row is None or row["delivery_id"] is None:
                return None
            return await self._get_delivery_plan_tx(connection, UUID(str(row["delivery_id"])))

    async def list_delivery_parts(self, delivery_id: UUID) -> tuple[ChannelDeliveryPartRecord, ...]:
        async with self._database.transaction() as connection:
            cursor = await connection.execute(
                """
                    SELECT * FROM channel_delivery_parts
                    WHERE delivery_id = ? ORDER BY ordinal ASC
                    """,
                (str(delivery_id),),
            )
            rows = await cursor.fetchall()
            await cursor.close()
            return tuple(_delivery_part_record(row) for row in rows)

    async def _get_turn_context_tx(
        self, connection: aiosqlite.Connection, delivery_id: UUID
    ) -> _TurnDeliveryContext | None:
        cursor = await connection.execute(
            """
            SELECT
                t.session_id,
                t.turn_id,
                t.generation_id,
                t.connection_id,
                t.channel_turn_id,
                t.chat_type,
                t.conversation_key,
                t.sender_key
            FROM channel_deliveries AS d
            JOIN channel_turns AS t ON t.channel_turn_id = d.channel_turn_id
            WHERE d.delivery_id = ?
            """,
            (str(delivery_id),),
        )
        row = await cursor.fetchone()
        await cursor.close()
        if row is None:
            return None
        return _TurnDeliveryContext(
            session_id=UUID(str(row["session_id"])),
            turn_id=UUID(str(row["turn_id"])),
            generation_id=UUID(str(row["generation_id"])),
            connection_id=UUID(str(row["connection_id"])),
            channel_turn_id=UUID(str(row["channel_turn_id"])),
            chat_type=ChannelChatType(str(row["chat_type"])),
            conversation_key=str(row["conversation_key"]),
            sender_key=str(row["sender_key"]),
        )

    async def _get_delivery_plan_tx(
        self,
        connection: aiosqlite.Connection,
        delivery_id: UUID,
    ) -> ChannelDeliveryPlanRecord | None:
        cursor = await connection.execute(
            "SELECT * FROM channel_deliveries WHERE delivery_id = ?",
            (str(delivery_id),),
        )
        delivery_row = await cursor.fetchone()
        await cursor.close()
        if delivery_row is None:
            return None

        cursor = await connection.execute(
            """
                    SELECT * FROM channel_delivery_parts
                    WHERE delivery_id = ? ORDER BY ordinal ASC
                    """,
            (str(delivery_id),),
        )
        part_rows = await cursor.fetchall()
        await cursor.close()

        parts = tuple(_delivery_part_record(p) for p in part_rows)
        delivered_count = sum(1 for p in parts if p.status is ChannelDeliveryPartStatus.DELIVERED)
        delivery = _delivery_record(
            delivery_row,
            part_count=len(parts),
            delivered_part_count=delivered_count,
        )
        return ChannelDeliveryPlanRecord(delivery=delivery, parts=parts)

    async def _derive_delivery_plan_state_tx(
        self,
        connection: aiosqlite.Connection,
        delivery_id: UUID,
        updated_at: datetime,
    ) -> ChannelDeliveryPlanRecord:
        cursor = await connection.execute(
            "SELECT cancel_requested_at FROM channel_deliveries WHERE delivery_id = ?",
            (str(delivery_id),),
        )
        parent_row = await cursor.fetchone()
        await cursor.close()
        if parent_row is None:
            raise KeyError(f"unknown channel delivery {delivery_id}")
        cancel_requested = parent_row["cancel_requested_at"] is not None

        cursor = await connection.execute(
            """
            SELECT * FROM channel_delivery_parts
            WHERE delivery_id = ? ORDER BY ordinal ASC
            """,
            (str(delivery_id),),
        )
        all_part_rows = await cursor.fetchall()
        await cursor.close()

        parts = [_delivery_part_record(p) for p in all_part_rows]

        has_failed_required = any(
            p.status is ChannelDeliveryPartStatus.FAILED and p.required for p in parts
        )
        all_required_delivered = (
            all(p.status is ChannelDeliveryPartStatus.DELIVERED for p in parts if p.required)
            if any(p.required for p in parts)
            else (
                len(parts) > 0
                and all(p.status is ChannelDeliveryPartStatus.DELIVERED for p in parts)
            )
        )
        sending_parts = [p for p in parts if p.status is ChannelDeliveryPartStatus.SENDING]
        has_sending = len(sending_parts) > 0

        latest_delivered_at: datetime | None = None
        last_provider_msg_id: str | None = None
        last_failed_error: str | None = None

        for p in parts:
            if p.status is ChannelDeliveryPartStatus.DELIVERED:
                if p.delivered_at is not None and (
                    latest_delivered_at is None or p.delivered_at > latest_delivered_at
                ):
                    latest_delivered_at = p.delivered_at
                if p.provider_message_id is not None:
                    last_provider_msg_id = p.provider_message_id
            elif p.status is ChannelDeliveryPartStatus.FAILED and p.required:
                if p.last_error is not None:
                    last_failed_error = _error_json(p.last_error)

        new_parent_status: ChannelDeliveryStatus
        if has_failed_required:
            new_parent_status = ChannelDeliveryStatus.FAILED
        elif all_required_delivered:
            new_parent_status = ChannelDeliveryStatus.DELIVERED
        elif cancel_requested and not has_sending:
            new_parent_status = ChannelDeliveryStatus.CANCELLED
        elif has_sending:
            new_parent_status = ChannelDeliveryStatus.SENDING
        else:
            new_parent_status = ChannelDeliveryStatus.PENDING

        # Invariant: Terminal Parent不得有 Active Child
        if new_parent_status in (
            ChannelDeliveryStatus.FAILED,
            ChannelDeliveryStatus.DELIVERED,
            ChannelDeliveryStatus.CANCELLED,
        ):
            cancel_error = StructuredError(
                code="delivery_terminal_cascade_cancelled",
                message=f"Delivery plan reached terminal status {new_parent_status.value}",
                retryable=False,
                component="external_channels",
            )
            await connection.execute(
                """
                UPDATE channel_delivery_parts
                SET status = 'cancelled',
                    lease_id = NULL,
                    lease_expires_at = NULL,
                    last_error_json = COALESCE(last_error_json, ?),
                    updated_at = ?
                WHERE delivery_id = ? AND status IN ('pending', 'sending')
                """,
                (_error_json(cancel_error), updated_at.isoformat(), str(delivery_id)),
            )

        # Invariant: Clear Parent lease unless Parent is SENDING (mirror sending child lease)
        parent_lease_id: str | None = None
        parent_lease_expires_at: str | None = None
        if new_parent_status is ChannelDeliveryStatus.SENDING and sending_parts:
            active_sending = sending_parts[0]
            if active_sending.lease_id is not None:
                parent_lease_id = str(active_sending.lease_id)
            if active_sending.lease_expires_at is not None:
                parent_lease_expires_at = active_sending.lease_expires_at.isoformat()

        max_part_attempt = max(max((p.attempt for p in parts), default=1), 1)
        await connection.execute(
            """
            UPDATE channel_deliveries
            SET status = ?,
                attempt = ?,
                lease_id = ?,
                lease_expires_at = ?,
                provider_message_id = COALESCE(?, provider_message_id),
                last_error_json = COALESCE(?, last_error_json),
                updated_at = ?,
                delivered_at = ?
            WHERE delivery_id = ?
            """,
            (
                new_parent_status.value,
                max_part_attempt,
                parent_lease_id,
                parent_lease_expires_at,
                last_provider_msg_id,
                last_failed_error,
                updated_at.isoformat(),
                (
                    latest_delivered_at.isoformat()
                    if new_parent_status is ChannelDeliveryStatus.DELIVERED
                    and latest_delivered_at is not None
                    else None
                ),
                str(delivery_id),
            ),
        )

        plan = await self._get_delivery_plan_tx(connection, delivery_id)
        if plan is None:
            raise RuntimeError("delivery plan disappeared after state derivation")
        return plan

    async def claim_next_delivery_part(
        self,
        claim: ChannelDeliveryPartClaimRequest,
        *,
        claimed_at: datetime,
    ) -> DeliveryTransitionResult | None:
        lease_expires_at = claimed_at + timedelta(seconds=claim.lease_seconds)
        async with self._database.transaction() as connection:
            cursor = await connection.execute(
                "SELECT * FROM channel_deliveries WHERE delivery_id = ?",
                (str(claim.delivery_id),),
            )
            delivery_row = await cursor.fetchone()
            await cursor.close()
            if delivery_row is None:
                raise KeyError(f"unknown channel delivery {claim.delivery_id}")

            delivery_status = ChannelDeliveryStatus(str(delivery_row["status"]))
            cancel_requested = delivery_row["cancel_requested_at"] is not None

            if (
                delivery_status
                in {
                    ChannelDeliveryStatus.DELIVERED,
                    ChannelDeliveryStatus.FAILED,
                    ChannelDeliveryStatus.CANCELLED,
                }
                or cancel_requested
            ):
                return None

            cursor = await connection.execute(
                """
                SELECT * FROM channel_delivery_parts
                WHERE delivery_id = ? ORDER BY ordinal ASC
                """,
                (str(claim.delivery_id),),
            )
            part_rows = await cursor.fetchall()
            await cursor.close()

            # Mutual exclusion: only one active lease per delivery plan
            for prow in part_rows:
                pstatus = ChannelDeliveryPartStatus(str(prow["status"]))
                if pstatus is ChannelDeliveryPartStatus.SENDING:
                    pexpiry = _datetime(prow["lease_expires_at"])
                    please_id = (
                        UUID(str(prow["lease_id"])) if prow["lease_id"] is not None else None
                    )
                    if pexpiry is not None and pexpiry > claimed_at and please_id != claim.lease_id:
                        return None

            target_part: object | None = None
            for prow in part_rows:
                pstatus = ChannelDeliveryPartStatus(str(prow["status"]))
                if pstatus in {
                    ChannelDeliveryPartStatus.DELIVERED,
                    ChannelDeliveryPartStatus.SKIPPED,
                }:
                    continue
                if pstatus in {
                    ChannelDeliveryPartStatus.FAILED,
                    ChannelDeliveryPartStatus.CANCELLED,
                }:
                    return None
                target_part = prow
                break

            if target_part is None:
                return None

            target_part_id = UUID(str(target_part["part_id"]))  # type: ignore[index]
            if claim.part_id is not None and claim.part_id != target_part_id:
                return None

            not_before_at = _datetime(target_part["not_before_at"])  # type: ignore[index]
            if not_before_at is not None and not_before_at > claimed_at:
                return None

            target_status = ChannelDeliveryPartStatus(str(target_part["status"]))  # type: ignore[index]
            target_lease_id = (
                UUID(str(target_part["lease_id"])) if target_part["lease_id"] is not None else None  # type: ignore[index]
            )
            target_expiry = _datetime(target_part["lease_expires_at"])  # type: ignore[index]

            same_lease = target_lease_id == claim.lease_id
            if target_status is ChannelDeliveryPartStatus.SENDING and not same_lease:
                if target_expiry is not None and target_expiry > claimed_at:
                    return None

            attempt = int(target_part["attempt"])  # type: ignore[index]
            if target_status is ChannelDeliveryPartStatus.PENDING or not same_lease:
                attempt += 1

            cursor = await connection.execute(
                """
                UPDATE channel_delivery_parts
                SET status = 'sending',
                    attempt = ?,
                    lease_id = ?,
                    lease_expires_at = ?,
                    updated_at = ?
                WHERE part_id = ?
                  AND delivery_id = ?
                  AND status IN ('pending', 'sending')
                """,
                (
                    attempt,
                    str(claim.lease_id),
                    lease_expires_at.isoformat(),
                    claimed_at.isoformat(),
                    str(target_part_id),
                    str(claim.delivery_id),
                ),
            )
            if cursor.rowcount == 0:
                return None

            plan = await self._derive_delivery_plan_state_tx(
                connection, claim.delivery_id, claimed_at
            )
            updated_part = next(p for p in plan.parts if p.part_id == target_part_id)

            persisted_events: list[GenericCoreEvent] = []
            if self._event_store is not None:
                ctx = await self._get_turn_context_tx(connection, claim.delivery_id)
                if ctx is not None:
                    event = GenericCoreEvent.model_validate(
                        {
                            "event_id": uuid4(),
                            "event_type": "channel.delivery_part_claimed",
                            "session_id": ctx.session_id,
                            "turn_id": ctx.turn_id,
                            "generation_id": ctx.generation_id,
                            "occurred_at": claimed_at,
                            "source": "runtime.external_channels",
                            "privacy": PrivacyLevel.PRIVATE,
                            "payload": {
                                "connection_id": str(ctx.connection_id),
                                "channel_turn_id": str(ctx.channel_turn_id),
                                "delivery_id": str(claim.delivery_id),
                                "part_id": str(target_part_id),
                                "ordinal": updated_part.ordinal,
                                "attempt": updated_part.attempt,
                                "lease_id": str(claim.lease_id),
                                "provider_client_id": updated_part.provider_client_id,
                            },
                        }
                    )
                    persisted = await self._event_store.append_in_transaction(connection, event)
                    persisted_events.append(persisted)

            return DeliveryTransitionResult(
                plan=plan,
                part=updated_part,
                applied=True,
                persisted_events=tuple(persisted_events),
            )

    async def acknowledge_delivery_part(
        self,
        acknowledgement: ChannelDeliveryPartAcknowledgement,
        *,
        updated_at: datetime,
    ) -> DeliveryTransitionResult:
        async with self._database.transaction() as connection:
            cursor = await connection.execute(
                "SELECT * FROM channel_delivery_parts WHERE part_id = ? AND delivery_id = ?",
                (str(acknowledgement.part_id), str(acknowledgement.delivery_id)),
            )
            current_part = await cursor.fetchone()
            await cursor.close()
            if current_part is None:
                raise KeyError(
                    f"unknown channel delivery part {acknowledgement.part_id} "
                    f"for delivery {acknowledgement.delivery_id}"
                )

            current_status = ChannelDeliveryPartStatus(str(current_part["status"]))
            current_lease_id = (
                UUID(str(current_part["lease_id"]))
                if current_part["lease_id"] is not None
                else None
            )

            if current_status is ChannelDeliveryPartStatus.DELIVERED:
                if acknowledgement.status is not ChannelDeliveryPartStatus.DELIVERED:
                    raise ValueError("a delivered channel delivery part cannot be downgraded")
                plan = await self._get_delivery_plan_tx(connection, acknowledgement.delivery_id)
                if plan is None:
                    raise RuntimeError("delivery plan disappeared")
                part_record = next(p for p in plan.parts if p.part_id == acknowledgement.part_id)
                return DeliveryTransitionResult(
                    plan=plan,
                    part=part_record,
                    applied=False,
                    persisted_events=(),
                )

            if current_status is not ChannelDeliveryPartStatus.SENDING:
                raise ValueError("channel delivery part is not owned by an active sending lease")

            if current_lease_id != acknowledgement.lease_id:
                raise ValueError("delivery part acknowledgement lease_id mismatch")

            current_expiry = _datetime(current_part["lease_expires_at"])
            if current_expiry is not None and current_expiry < updated_at:
                raise ValueError("delivery part acknowledgement lease expired")

            delivered_at_val: str | None = None
            if acknowledgement.status is ChannelDeliveryPartStatus.DELIVERED:
                delivered_at_val = acknowledgement.acknowledged_at.isoformat()

            await connection.execute(
                """
                UPDATE channel_delivery_parts
                SET status = ?,
                    provider_message_id = COALESCE(?, provider_message_id),
                    last_error_json = ?,
                    updated_at = ?,
                    delivered_at = ?,
                    lease_id = NULL,
                    lease_expires_at = NULL
                WHERE part_id = ? AND delivery_id = ?
                """,
                (
                    acknowledgement.status.value,
                    acknowledgement.provider_message_id,
                    _error_json(acknowledgement.error),
                    updated_at.isoformat(),
                    delivered_at_val,
                    str(acknowledgement.part_id),
                    str(acknowledgement.delivery_id),
                ),
            )

            plan = await self._derive_delivery_plan_state_tx(
                connection, acknowledgement.delivery_id, updated_at
            )
            part_record = next(p for p in plan.parts if p.part_id == acknowledgement.part_id)

            persisted_events: list[GenericCoreEvent] = []
            if self._event_store is not None:
                ctx = await self._get_turn_context_tx(connection, acknowledgement.delivery_id)
                if ctx is not None:
                    persisted_events.append(
                        await self._event_store.append_in_transaction(
                            connection,
                            GenericCoreEvent.model_validate(
                                {
                                    "event_id": uuid4(),
                                    "event_type": "channel.delivery_part_acknowledged",
                                    "session_id": ctx.session_id,
                                    "turn_id": ctx.turn_id,
                                    "generation_id": ctx.generation_id,
                                    "occurred_at": updated_at,
                                    "source": "runtime.external_channels",
                                    "privacy": PrivacyLevel.PRIVATE,
                                    "payload": {
                                        "connection_id": str(ctx.connection_id),
                                        "channel_turn_id": str(ctx.channel_turn_id),
                                        "delivery_id": str(acknowledgement.delivery_id),
                                        "part_id": str(acknowledgement.part_id),
                                        "ordinal": part_record.ordinal,
                                        "status": part_record.status.value,
                                        "provider_message_id": part_record.provider_message_id,
                                    },
                                }
                            ),
                        )
                    )
                    if part_record.status is ChannelDeliveryPartStatus.DELIVERED:
                        persisted_events.append(
                            await self._event_store.append_in_transaction(
                                connection,
                                GenericCoreEvent.model_validate(
                                    {
                                        "event_id": uuid4(),
                                        "event_type": "channel.delivery_part_delivered",
                                        "session_id": ctx.session_id,
                                        "turn_id": ctx.turn_id,
                                        "generation_id": ctx.generation_id,
                                        "occurred_at": updated_at,
                                        "source": "runtime.external_channels",
                                        "privacy": PrivacyLevel.PRIVATE,
                                        "payload": {
                                            "connection_id": str(ctx.connection_id),
                                            "channel_turn_id": str(ctx.channel_turn_id),
                                            "delivery_id": str(acknowledgement.delivery_id),
                                            "part_id": str(acknowledgement.part_id),
                                            "ordinal": part_record.ordinal,
                                            "provider_message_id": part_record.provider_message_id,
                                        },
                                    }
                                ),
                            )
                        )
                    elif part_record.status is ChannelDeliveryPartStatus.FAILED:
                        persisted_events.append(
                            await self._event_store.append_in_transaction(
                                connection,
                                GenericCoreEvent.model_validate(
                                    {
                                        "event_id": uuid4(),
                                        "event_type": "channel.delivery_part_failed",
                                        "session_id": ctx.session_id,
                                        "turn_id": ctx.turn_id,
                                        "generation_id": ctx.generation_id,
                                        "occurred_at": updated_at,
                                        "source": "runtime.external_channels",
                                        "privacy": PrivacyLevel.PRIVATE,
                                        "payload": {
                                            "connection_id": str(ctx.connection_id),
                                            "channel_turn_id": str(ctx.channel_turn_id),
                                            "delivery_id": str(acknowledgement.delivery_id),
                                            "part_id": str(acknowledgement.part_id),
                                            "ordinal": part_record.ordinal,
                                            "error": (
                                                part_record.last_error.model_dump(mode="json")
                                                if part_record.last_error is not None
                                                else None
                                            ),
                                        },
                                    }
                                ),
                            )
                        )
                    if plan.status is ChannelDeliveryStatus.DELIVERED:
                        persisted_events.append(
                            await self._event_store.append_in_transaction(
                                connection,
                                GenericCoreEvent.model_validate(
                                    {
                                        "event_id": uuid4(),
                                        "event_type": "channel.delivery_plan_completed",
                                        "session_id": ctx.session_id,
                                        "turn_id": ctx.turn_id,
                                        "generation_id": ctx.generation_id,
                                        "occurred_at": updated_at,
                                        "source": "runtime.external_channels",
                                        "privacy": PrivacyLevel.PRIVATE,
                                        "payload": {
                                            "connection_id": str(ctx.connection_id),
                                            "channel_turn_id": str(ctx.channel_turn_id),
                                            "delivery_id": str(plan.delivery_id),
                                            "part_count": plan.part_count,
                                        },
                                    }
                                ),
                            )
                        )
                        persisted_events.append(
                            await self._event_store.append_in_transaction(
                                connection,
                                GenericCoreEvent.model_validate(
                                    {
                                        "event_id": uuid4(),
                                        "event_type": "channel.delivery_acknowledged",
                                        "session_id": ctx.session_id,
                                        "turn_id": ctx.turn_id,
                                        "generation_id": ctx.generation_id,
                                        "occurred_at": updated_at,
                                        "source": "runtime.external_channels",
                                        "privacy": PrivacyLevel.PRIVATE,
                                        "payload": {
                                            "connection_id": str(ctx.connection_id),
                                            "channel_turn_id": str(ctx.channel_turn_id),
                                            "delivery_id": str(plan.delivery_id),
                                            "delivery_status": plan.status.value,
                                            "chat_type": ctx.chat_type.value,
                                            "conversation_key": ctx.conversation_key,
                                            "sender_key": ctx.sender_key,
                                        },
                                    }
                                ),
                            )
                        )
                    elif plan.status is ChannelDeliveryStatus.CANCELLED:
                        persisted_events.append(
                            await self._event_store.append_in_transaction(
                                connection,
                                GenericCoreEvent.model_validate(
                                    {
                                        "event_id": uuid4(),
                                        "event_type": "channel.delivery_plan_cancelled",
                                        "session_id": ctx.session_id,
                                        "turn_id": ctx.turn_id,
                                        "generation_id": ctx.generation_id,
                                        "occurred_at": updated_at,
                                        "source": "runtime.external_channels",
                                        "privacy": PrivacyLevel.PRIVATE,
                                        "payload": {
                                            "connection_id": str(ctx.connection_id),
                                            "channel_turn_id": str(ctx.channel_turn_id),
                                            "delivery_id": str(plan.delivery_id),
                                        },
                                    }
                                ),
                            )
                        )

            return DeliveryTransitionResult(
                plan=plan,
                part=part_record,
                applied=True,
                persisted_events=tuple(persisted_events),
            )

    async def defer_delivery_part(
        self,
        defer_request: ChannelDeliveryPartDeferRequest,
        *,
        updated_at: datetime,
    ) -> DeliveryTransitionResult:
        async with self._database.transaction() as connection:
            cursor = await connection.execute(
                "SELECT * FROM channel_delivery_parts WHERE part_id = ? AND delivery_id = ?",
                (str(defer_request.part_id), str(defer_request.delivery_id)),
            )
            current_part = await cursor.fetchone()
            await cursor.close()
            if current_part is None:
                raise KeyError(
                    f"unknown channel delivery part {defer_request.part_id} "
                    f"for delivery {defer_request.delivery_id}"
                )
            current_status = ChannelDeliveryPartStatus(str(current_part["status"]))
            current_lease_id = (
                UUID(str(current_part["lease_id"]))
                if current_part["lease_id"] is not None
                else None
            )
            if current_status is not ChannelDeliveryPartStatus.SENDING:
                raise ValueError("channel delivery part is not owned by an active sending lease")
            if current_lease_id != defer_request.lease_id:
                raise ValueError("delivery part defer lease_id mismatch")

            await connection.execute(
                """
                UPDATE channel_delivery_parts
                SET status = 'pending',
                    lease_id = NULL,
                    lease_expires_at = NULL,
                    not_before_at = ?,
                    last_error_json = ?,
                    updated_at = ?
                WHERE part_id = ? AND delivery_id = ?
                """,
                (
                    defer_request.not_before_at.isoformat(),
                    _error_json(defer_request.error),
                    updated_at.isoformat(),
                    str(defer_request.part_id),
                    str(defer_request.delivery_id),
                ),
            )

            plan = await self._derive_delivery_plan_state_tx(
                connection, defer_request.delivery_id, updated_at
            )
            part_record = next(p for p in plan.parts if p.part_id == defer_request.part_id)
            return DeliveryTransitionResult(
                plan=plan,
                part=part_record,
                applied=True,
                persisted_events=(),
            )

    async def cancel_remaining_delivery_parts(
        self,
        delivery_id: UUID,
        cancel_request: ChannelDeliveryPartsCancelRequest,
    ) -> DeliveryTransitionResult:
        async with self._database.transaction() as connection:
            cursor = await connection.execute(
                "SELECT * FROM channel_deliveries WHERE delivery_id = ?",
                (str(delivery_id),),
            )
            parent = await cursor.fetchone()
            await cursor.close()
            if parent is None:
                raise KeyError(f"unknown channel delivery {delivery_id}")

            parent_status = ChannelDeliveryStatus(str(parent["status"]))
            if parent_status in {
                ChannelDeliveryStatus.DELIVERED,
                ChannelDeliveryStatus.FAILED,
                ChannelDeliveryStatus.CANCELLED,
            }:
                plan = await self._get_delivery_plan_tx(connection, delivery_id)
                if plan is None:
                    raise RuntimeError("delivery plan disappeared")
                return DeliveryTransitionResult(
                    plan=plan,
                    part=None,
                    applied=False,
                    persisted_events=(),
                )

            await connection.execute(
                """
                UPDATE channel_deliveries
                SET cancel_requested_at = COALESCE(cancel_requested_at, ?),
                    updated_at = ?
                WHERE delivery_id = ?
                """,
                (
                    cancel_request.requested_at.isoformat(),
                    cancel_request.requested_at.isoformat(),
                    str(delivery_id),
                ),
            )

            cancel_error = StructuredError(
                code="delivery_cancelled",
                message=cancel_request.reason,
                retryable=False,
                component="external_channels",
            )
            await connection.execute(
                """
                UPDATE channel_delivery_parts
                SET status = 'cancelled',
                    last_error_json = ?,
                    updated_at = ?
                WHERE delivery_id = ? AND status = 'pending'
                """,
                (
                    _error_json(cancel_error),
                    cancel_request.requested_at.isoformat(),
                    str(delivery_id),
                ),
            )

            plan = await self._derive_delivery_plan_state_tx(
                connection, delivery_id, cancel_request.requested_at
            )

            persisted_events: list[GenericCoreEvent] = []
            if self._event_store is not None:
                ctx = await self._get_turn_context_tx(connection, delivery_id)
                if ctx is not None:
                    persisted_events.append(
                        await self._event_store.append_in_transaction(
                            connection,
                            GenericCoreEvent.model_validate(
                                {
                                    "event_id": uuid4(),
                                    "event_type": "channel.delivery_plan_cancel_requested",
                                    "session_id": ctx.session_id,
                                    "turn_id": ctx.turn_id,
                                    "generation_id": ctx.generation_id,
                                    "occurred_at": cancel_request.requested_at,
                                    "source": "runtime.external_channels",
                                    "privacy": PrivacyLevel.PRIVATE,
                                    "payload": {
                                        "connection_id": str(ctx.connection_id),
                                        "channel_turn_id": str(ctx.channel_turn_id),
                                        "delivery_id": str(delivery_id),
                                        "reason": cancel_request.reason,
                                    },
                                }
                            ),
                        )
                    )
                    if plan.status is ChannelDeliveryStatus.CANCELLED:
                        persisted_events.append(
                            await self._event_store.append_in_transaction(
                                connection,
                                GenericCoreEvent.model_validate(
                                    {
                                        "event_id": uuid4(),
                                        "event_type": "channel.delivery_plan_cancelled",
                                        "session_id": ctx.session_id,
                                        "turn_id": ctx.turn_id,
                                        "generation_id": ctx.generation_id,
                                        "occurred_at": cancel_request.requested_at,
                                        "source": "runtime.external_channels",
                                        "privacy": PrivacyLevel.PRIVATE,
                                        "payload": {
                                            "connection_id": str(ctx.connection_id),
                                            "channel_turn_id": str(ctx.channel_turn_id),
                                            "delivery_id": str(delivery_id),
                                        },
                                    }
                                ),
                            )
                        )

            return DeliveryTransitionResult(
                plan=plan,
                part=None,
                applied=True,
                persisted_events=tuple(persisted_events),
            )

    async def list_active_delivery_plans_for_binding(
        self,
        binding_id: UUID,
    ) -> tuple[ChannelDeliveryPlanRecord, ...]:
        async with self._database.transaction() as connection:
            cursor = await connection.execute(
                """
                SELECT d.delivery_id
                FROM channel_deliveries AS d
                JOIN channel_turns AS t ON t.channel_turn_id = d.channel_turn_id
                WHERE t.binding_id = ?
                  AND d.status IN ('pending', 'sending')
                ORDER BY d.created_at ASC
                """,
                (str(binding_id),),
            )
            rows = await cursor.fetchall()
            await cursor.close()
            plans: list[ChannelDeliveryPlanRecord] = []
            for row in rows:
                plan = await self._get_delivery_plan_tx(connection, UUID(str(row["delivery_id"])))
                if plan is not None:
                    plans.append(plan)
            return tuple(plans)

    async def find_active_delivery_plan_for_binding(
        self,
        binding_id: UUID,
    ) -> ChannelDeliveryPlanRecord | None:
        plans = await self.list_active_delivery_plans_for_binding(binding_id)
        return plans[-1] if plans else None

    async def list_nonterminal_delivery_plans(
        self,
        connection_id: UUID | None = None,
        *,
        limit: int = 50,
    ) -> tuple[ChannelDeliveryPlanRecord, ...]:
        async with self._database.transaction() as connection:
            if connection_id is not None:
                cursor = await connection.execute(
                    """
                    SELECT d.delivery_id
                    FROM channel_deliveries AS d
                    JOIN channel_turns AS t ON t.channel_turn_id = d.channel_turn_id
                    WHERE d.status IN ('pending', 'sending')
                      AND t.connection_id = ?
                    ORDER BY d.created_at ASC
                    LIMIT ?
                    """,
                    (str(connection_id), limit),
                )
            else:
                cursor = await connection.execute(
                    """
                    SELECT d.delivery_id
                    FROM channel_deliveries AS d
                    WHERE d.status IN ('pending', 'sending')
                    ORDER BY d.created_at ASC
                    LIMIT ?
                    """,
                    (limit,),
                )
            rows = await cursor.fetchall()
            await cursor.close()
            plans: list[ChannelDeliveryPlanRecord] = []
            for r in rows:
                plan = await self._get_delivery_plan_tx(connection, UUID(str(r["delivery_id"])))
                if plan is not None:
                    plans.append(plan)
            return tuple(plans)

    async def next_delivery_wakeup_at(
        self,
        connection_id: UUID | None = None,
    ) -> datetime | None:
        async with self._database.transaction() as connection:
            if connection_id is not None:
                cursor = await connection.execute(
                    """
                    SELECT MIN(next_time) AS next_wakeup FROM (
                        SELECT MIN(p.lease_expires_at) AS next_time
                        FROM channel_delivery_parts AS p
                        JOIN channel_deliveries AS d ON d.delivery_id = p.delivery_id
                        JOIN channel_turns AS t ON t.channel_turn_id = d.channel_turn_id
                        WHERE p.status = 'sending' AND p.lease_expires_at IS NOT NULL
                          AND t.connection_id = ?
                        UNION ALL
                        SELECT MIN(p.not_before_at) AS next_time
                        FROM channel_delivery_parts AS p
                        JOIN channel_deliveries AS d ON d.delivery_id = p.delivery_id
                        JOIN channel_turns AS t ON t.channel_turn_id = d.channel_turn_id
                        WHERE p.status = 'pending' AND p.not_before_at IS NOT NULL
                          AND t.connection_id = ?
                    )
                    """,
                    (str(connection_id), str(connection_id)),
                )
            else:
                cursor = await connection.execute(
                    """
                    SELECT MIN(next_time) AS next_wakeup FROM (
                        SELECT MIN(p.lease_expires_at) AS next_time
                        FROM channel_delivery_parts AS p
                        WHERE p.status = 'sending' AND p.lease_expires_at IS NOT NULL
                        UNION ALL
                        SELECT MIN(p.not_before_at) AS next_time
                        FROM channel_delivery_parts AS p
                        WHERE p.status = 'pending' AND p.not_before_at IS NOT NULL
                    )
                    """
                )
            row = await cursor.fetchone()
            await cursor.close()
            if row is None or row["next_wakeup"] is None:
                return None
            return _datetime(row["next_wakeup"])

    async def recover_expired_delivery_part_leases(
        self,
        *,
        as_of: datetime,
    ) -> int:
        async with self._database.transaction() as connection:
            cursor = await connection.execute(
                """
                SELECT p.part_id, p.delivery_id, d.cancel_requested_at
                FROM channel_delivery_parts AS p
                JOIN channel_deliveries AS d ON d.delivery_id = p.delivery_id
                WHERE p.status = 'sending'
                  AND (p.lease_expires_at IS NULL OR p.lease_expires_at <= ?)
                """,
                (as_of.isoformat(),),
            )
            expired_rows = tuple(await cursor.fetchall())
            await cursor.close()

            if not expired_rows:
                return 0

            affected_deliveries: set[UUID] = set()
            for row in expired_rows:
                part_id = str(row["part_id"])
                delivery_id = UUID(str(row["delivery_id"]))
                cancel_requested = row["cancel_requested_at"] is not None
                affected_deliveries.add(delivery_id)

                new_status = "cancelled" if cancel_requested else "pending"
                await connection.execute(
                    """
                    UPDATE channel_delivery_parts
                    SET status = ?,
                        lease_id = NULL,
                        lease_expires_at = NULL,
                        updated_at = ?
                    WHERE part_id = ?
                    """,
                    (new_status, as_of.isoformat(), part_id),
                )

            for delivery_id in affected_deliveries:
                await self._derive_delivery_plan_state_tx(connection, delivery_id, as_of)

            return len(expired_rows)

    async def set_turn_terminal(
        self,
        channel_turn_id: UUID,
        *,
        status: ChannelTurnStatus,
        error: StructuredError | None,
        completed_at: datetime,
    ) -> ChannelTurnRecord:
        if status not in {
            ChannelTurnStatus.CANCELLED,
            ChannelTurnStatus.FAILED,
            ChannelTurnStatus.TIMED_OUT,
        }:
            raise ValueError("turn terminal status is invalid")
        await self._update_turn_status(
            channel_turn_id,
            status=status,
            error=error,
            updated_at=completed_at,
            completed_at=completed_at,
        )
        return await self._required_turn(channel_turn_id)

    async def set_turn_cancelling(
        self, channel_turn_id: UUID, *, updated_at: datetime
    ) -> ChannelTurnRecord:
        await self._update_turn_status(
            channel_turn_id,
            status=ChannelTurnStatus.CANCELLING,
            error=None,
            updated_at=updated_at,
            allowed_from=(ChannelTurnStatus.ACCEPTED, ChannelTurnStatus.PROCESSING),
        )
        return await self._required_turn(channel_turn_id)

    async def acknowledge_delivery(
        self,
        acknowledgement: ChannelDeliveryAcknowledgement,
        *,
        updated_at: datetime,
    ) -> ChannelDeliveryRecord:
        async with self._database.transaction() as connection:
            cursor = await connection.execute(
                "SELECT channel_turn_id, "
                "(SELECT COUNT(*) FROM channel_delivery_parts WHERE delivery_id = ?) AS part_count "
                "FROM channel_deliveries WHERE delivery_id = ?",
                (str(acknowledgement.delivery_id), str(acknowledgement.delivery_id)),
            )
            row = await cursor.fetchone()
            await cursor.close()
            if row is None:
                raise KeyError(f"unknown channel delivery {acknowledgement.delivery_id}")
            if UUID(str(row["channel_turn_id"])) != acknowledgement.channel_turn_id:
                raise ValueError("delivery acknowledgement channel_turn_id mismatch")
            part_count = int(row["part_count"])
            if part_count > 1:
                raise ValueError(
                    "legacy whole-delivery operations are not supported "
                    "for multipart delivery plans"
                )

            cursor = await connection.execute(
                "SELECT part_id FROM channel_delivery_parts WHERE delivery_id = ? AND ordinal = 0",
                (str(acknowledgement.delivery_id),),
            )
            part_row = await cursor.fetchone()
            await cursor.close()
            if part_row is None:
                raise KeyError(
                    f"unknown channel delivery part for delivery {acknowledgement.delivery_id}"
                )
            part_id = UUID(str(part_row["part_id"]))

        part_status = (
            ChannelDeliveryPartStatus.DELIVERED
            if acknowledgement.status is ChannelDeliveryStatus.DELIVERED
            else ChannelDeliveryPartStatus.FAILED
        )
        part_ack = ChannelDeliveryPartAcknowledgement(
            delivery_id=acknowledgement.delivery_id,
            part_id=part_id,
            lease_id=acknowledgement.lease_id,
            status=part_status,
            provider_message_id=acknowledgement.provider_message_id,
            error=acknowledgement.error,
            acknowledged_at=acknowledgement.acknowledged_at,
        )
        result = await self.acknowledge_delivery_part(part_ack, updated_at=updated_at)
        return result.plan.delivery

    async def claim_delivery(
        self,
        claim: ChannelDeliveryClaimRequest,
        *,
        claimed_at: datetime,
    ) -> ChannelDeliveryRecord | None:
        async with self._database.transaction() as connection:
            cursor = await connection.execute(
                "SELECT channel_turn_id, "
                "(SELECT COUNT(*) FROM channel_delivery_parts WHERE delivery_id = ?) AS part_count "
                "FROM channel_deliveries WHERE delivery_id = ?",
                (str(claim.delivery_id), str(claim.delivery_id)),
            )
            row = await cursor.fetchone()
            await cursor.close()
            if row is None:
                raise KeyError(f"unknown channel delivery {claim.delivery_id}")
            if UUID(str(row["channel_turn_id"])) != claim.channel_turn_id:
                raise ValueError("delivery claim channel_turn_id mismatch")
            part_count = int(row["part_count"])
            if part_count > 1:
                raise ValueError(
                    "legacy whole-delivery operations are not supported "
                    "for multipart delivery plans"
                )

        part_claim = ChannelDeliveryPartClaimRequest(
            delivery_id=claim.delivery_id,
            part_id=None,
            lease_id=claim.lease_id,
            lease_seconds=claim.lease_seconds,
        )
        result = await self.claim_next_delivery_part(part_claim, claimed_at=claimed_at)
        if result is None or result.part is None:
            return None
        return result.plan.delivery

    async def reconcile_inflight(
        self,
        *,
        status: ChannelTurnStatus,
        error: StructuredError,
        updated_at: datetime,
    ) -> int:
        if status not in {ChannelTurnStatus.FAILED, ChannelTurnStatus.TIMED_OUT}:
            raise ValueError("reconciliation status must be terminal")
        changed = await self._database.execute(
            """
            UPDATE channel_turns SET status = ?, error_json = ?, revision = revision + 1,
                updated_at = ?, completed_at = ?
            WHERE status IN ('accepted', 'processing', 'cancelling')
            """,
            (
                status.value,
                _error_json(error),
                updated_at.isoformat(),
                updated_at.isoformat(),
            ),
        )
        return max(0, changed)

    async def _required_turn(self, channel_turn_id: UUID) -> ChannelTurnRecord:
        turn = await self.get_turn(channel_turn_id)
        if turn is None:
            raise KeyError(f"unknown channel turn {channel_turn_id}")
        return turn

    async def _update_turn_status(
        self,
        channel_turn_id: UUID,
        *,
        status: ChannelTurnStatus,
        error: StructuredError | None,
        updated_at: datetime,
        completed_at: datetime | None = None,
        allowed_from: tuple[ChannelTurnStatus, ...] | None = None,
    ) -> None:
        where = "channel_turn_id = ?"
        parameters: list[object] = [
            status.value,
            _error_json(error),
            updated_at.isoformat(),
            completed_at.isoformat() if completed_at is not None else None,
            str(channel_turn_id),
        ]
        if allowed_from:
            placeholders = ",".join("?" for _item in allowed_from)
            where += f" AND status IN ({placeholders})"
            parameters.extend(item.value for item in allowed_from)
        changed = await self._database.execute(
            f"""
            UPDATE channel_turns SET status = ?, error_json = ?, revision = revision + 1,
                updated_at = ?, completed_at = COALESCE(?, completed_at)
            WHERE {where}
            """,
            parameters,
        )
        if changed == 0 and await self.get_turn(channel_turn_id) is None:
            raise KeyError(f"unknown channel turn {channel_turn_id}")


_TURN_SELECT = """
    SELECT t.*, d.status AS delivery_status
    FROM channel_turns AS t
    LEFT JOIN channel_deliveries AS d ON d.delivery_id = t.delivery_id
"""


def _connection_record(row: object) -> ChannelConnectionRecord:
    item = row  # aiosqlite.Row; kept structural to avoid adapter leakage in the port
    raw_allowed: object = json.loads(
        str(item["allowed_sender_keys_json"])  # type: ignore[index]
    )
    if not isinstance(raw_allowed, list):
        raise ValueError("persisted allowed_sender_keys_json is invalid")
    allowed_objects = cast(list[object], raw_allowed)
    if not all(isinstance(value, str) for value in allowed_objects):
        raise ValueError("persisted allowed_sender_keys_json is invalid")
    allowed = cast(list[str], allowed_objects)
    configuration = ChannelConnectionConfiguration(
        connection_id=UUID(str(item["connection_id"])),  # type: ignore[index]
        provider_id=str(item["provider_id"]),  # type: ignore[index]
        name=str(item["name"]),  # type: ignore[index]
        character_id=str(item["character_id"]),  # type: ignore[index]
        principal_scope=str(item["principal_scope"]),  # type: ignore[index]
        account_key=(
            str(item["account_key"]) if item["account_key"] is not None else None  # type: ignore[index]
        ),
        allowed_sender_keys=allowed,
        enabled=bool(item["enabled"]),  # type: ignore[index]
        timeout_seconds=float(item["timeout_seconds"]),  # type: ignore[index]
    )
    return ChannelConnectionRecord(
        configuration=configuration,
        status=ChannelConnectionStatus(str(item["status"])),  # type: ignore[index]
        access_token_hash=str(item["access_token_hash"]),  # type: ignore[index]
        last_error=_error_from_json(item["last_error_json"]),  # type: ignore[index]
        last_seen_at=_datetime(item["last_seen_at"]),  # type: ignore[index]
        revision=int(item["revision"]),  # type: ignore[index]
        created_at=_required_datetime(item["created_at"]),  # type: ignore[index]
        updated_at=_required_datetime(item["updated_at"]),  # type: ignore[index]
        deleted_at=_datetime(item["deleted_at"]),  # type: ignore[index]
    )


def _binding_record(row: object) -> ChannelBindingRecord:
    item = row
    return ChannelBindingRecord(
        binding_id=UUID(str(item["binding_id"])),  # type: ignore[index]
        connection_id=UUID(str(item["connection_id"])),  # type: ignore[index]
        conversation_key=str(item["conversation_key"]),  # type: ignore[index]
        sender_key=str(item["sender_key"]),  # type: ignore[index]
        session_id=UUID(str(item["session_id"])),  # type: ignore[index]
        created_at=_required_datetime(item["created_at"]),  # type: ignore[index]
        updated_at=_required_datetime(item["updated_at"]),  # type: ignore[index]
    )


def _turn_record(row: object) -> ChannelTurnRecord:
    item = row
    return ChannelTurnRecord(
        channel_turn_id=UUID(str(item["channel_turn_id"])),  # type: ignore[index]
        connection_id=UUID(str(item["connection_id"])),  # type: ignore[index]
        binding_id=UUID(str(item["binding_id"])),  # type: ignore[index]
        external_message_id=str(item["external_message_id"]),  # type: ignore[index]
        content_sha256=str(item["content_sha256"]),  # type: ignore[index]
        account_key=(
            str(item["account_key"]) if item["account_key"] is not None else None  # type: ignore[index]
        ),
        conversation_key=str(item["conversation_key"]),  # type: ignore[index]
        chat_type=ChannelChatType(str(item["chat_type"])),  # type: ignore[index]
        conversation_label=(
            str(item["conversation_label"])  # type: ignore[index]
            if item["conversation_label"] is not None  # type: ignore[index]
            else None
        ),
        sender_key=str(item["sender_key"]),  # type: ignore[index]
        sender_display_name=(
            str(item["sender_display_name"])  # type: ignore[index]
            if item["sender_display_name"] is not None  # type: ignore[index]
            else None
        ),
        principal_scope=str(item["principal_scope"]),  # type: ignore[index]
        session_id=UUID(str(item["session_id"])),  # type: ignore[index]
        turn_id=UUID(str(item["turn_id"])),  # type: ignore[index]
        generation_id=UUID(str(item["generation_id"])),  # type: ignore[index]
        status=ChannelTurnStatus(str(item["status"])),  # type: ignore[index]
        reply_text=(
            str(item["reply_text"]) if item["reply_text"] is not None else None  # type: ignore[index]
        ),
        error=_error_from_json(item["error_json"]),  # type: ignore[index]
        delivery_id=(
            UUID(str(item["delivery_id"])) if item["delivery_id"] is not None else None  # type: ignore[index]
        ),
        delivery_status=(
            ChannelDeliveryStatus(str(item["delivery_status"]))  # type: ignore[index]
            if item["delivery_status"] is not None  # type: ignore[index]
            else None
        ),
        revision=int(item["revision"]),  # type: ignore[index]
        accepted_at=_required_datetime(item["accepted_at"]),  # type: ignore[index]
        created_at=_required_datetime(item["created_at"]),  # type: ignore[index]
        updated_at=_required_datetime(item["updated_at"]),  # type: ignore[index]
        completed_at=_datetime(item["completed_at"]),  # type: ignore[index]
    )


def _delivery_part_record(row: object) -> ChannelDeliveryPartRecord:
    item = cast(aiosqlite.Row, row)
    payload_raw = json.loads(str(item["payload_json"]))  # type: ignore[index]
    payload = _PART_PAYLOAD_ADAPTER.validate_python(payload_raw)
    return ChannelDeliveryPartRecord(
        part_id=UUID(str(item["part_id"])),  # type: ignore[index]
        delivery_id=UUID(str(item["delivery_id"])),  # type: ignore[index]
        ordinal=int(item["ordinal"]),  # type: ignore[index]
        kind=ChannelDeliveryPartKind(str(item["kind"])),  # type: ignore[index]
        payload=payload,
        required=bool(item["required"]),  # type: ignore[index]
        status=ChannelDeliveryPartStatus(str(item["status"])),  # type: ignore[index]
        delay_after_ms=int(item["delay_after_ms"]),  # type: ignore[index]
        not_before_at=_datetime(item["not_before_at"]),  # type: ignore[index]
        attempt=int(item["attempt"]),  # type: ignore[index]
        lease_id=UUID(str(item["lease_id"])) if item["lease_id"] is not None else None,  # type: ignore[index]
        lease_expires_at=_datetime(item["lease_expires_at"]),  # type: ignore[index]
        provider_client_id=str(item["provider_client_id"]),  # type: ignore[index]
        provider_message_id=str(item["provider_message_id"])
        if item["provider_message_id"] is not None
        else None,  # type: ignore[index]
        last_error=_error_from_json(item["last_error_json"]),  # type: ignore[index]
        created_at=_required_datetime(item["created_at"]),  # type: ignore[index]
        updated_at=_required_datetime(item["updated_at"]),  # type: ignore[index]
        delivered_at=_datetime(item["delivered_at"]),  # type: ignore[index]
    )


def _delivery_record(
    row: object,
    *,
    part_count: int | None = None,
    delivered_part_count: int | None = None,
) -> ChannelDeliveryRecord:
    item = cast(aiosqlite.Row, row)
    plan_version = 1
    cancel_requested_at = None
    try:
        if "plan_version" in item.keys() and item["plan_version"] is not None:  # type: ignore[attr-defined]
            plan_version = int(item["plan_version"])  # type: ignore[index]
        if "cancel_requested_at" in item.keys():  # type: ignore[attr-defined]
            cancel_requested_at = _datetime(item["cancel_requested_at"])  # type: ignore[index]
    except (AttributeError, KeyError):
        pass

    resolved_part_count = part_count if part_count is not None else 1
    resolved_delivered_part_count = (
        delivered_part_count
        if delivered_part_count is not None
        else (1 if str(item["status"]) == "delivered" else 0)  # type: ignore[index]
    )

    return ChannelDeliveryRecord(
        delivery_id=UUID(str(item["delivery_id"])),  # type: ignore[index]
        channel_turn_id=UUID(str(item["channel_turn_id"])),  # type: ignore[index]
        connection_id=UUID(str(item["connection_id"])),  # type: ignore[index]
        status=ChannelDeliveryStatus(str(item["status"])),  # type: ignore[index]
        attempt=int(item["attempt"]),  # type: ignore[index]
        lease_id=(
            UUID(str(item["lease_id"])) if item["lease_id"] is not None else None  # type: ignore[index]
        ),
        lease_expires_at=_datetime(item["lease_expires_at"]),  # type: ignore[index]
        provider_message_id=(
            str(item["provider_message_id"])  # type: ignore[index]
            if item["provider_message_id"] is not None  # type: ignore[index]
            else None
        ),
        last_error=_error_from_json(item["last_error_json"]),  # type: ignore[index]
        created_at=_required_datetime(item["created_at"]),  # type: ignore[index]
        updated_at=_required_datetime(item["updated_at"]),  # type: ignore[index]
        delivered_at=_datetime(item["delivered_at"]),  # type: ignore[index]
        plan_version=plan_version,
        part_count=resolved_part_count,
        delivered_part_count=resolved_delivered_part_count,
        cancel_requested_at=cancel_requested_at,
    )


def _error_json(error: StructuredError | None) -> str | None:
    return error.model_dump_json() if error is not None else None


def _error_from_json(value: object) -> StructuredError | None:
    if value is None:
        return None
    return StructuredError.model_validate_json(str(value))


def _datetime(value: object) -> datetime | None:
    return datetime.fromisoformat(str(value)) if value is not None else None


def _required_datetime(value: object) -> datetime:
    parsed = _datetime(value)
    if parsed is None:
        raise ValueError("persisted channel timestamp is missing")
    return parsed
