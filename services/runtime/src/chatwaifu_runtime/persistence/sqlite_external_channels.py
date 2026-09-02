"""SQLite adapter for the External Channel Gateway repository port."""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from typing import cast
from uuid import UUID

import aiosqlite
from chatwaifu_protocol.channels import (
    ChannelChatType,
    ChannelConnectionConfiguration,
    ChannelConnectionStatus,
    ChannelDeliveryAcknowledgement,
    ChannelDeliveryClaimRequest,
    ChannelDeliveryStatus,
    ChannelTurnStatus,
)
from chatwaifu_protocol.errors import StructuredError

from chatwaifu_runtime.external_channels.models import (
    ChannelBindingRecord,
    ChannelConnectionRecord,
    ChannelDeliveryRecord,
    ChannelTurnRecord,
)
from chatwaifu_runtime.external_channels.ports import ExternalChannelRepository
from chatwaifu_runtime.persistence.database import Database


class SQLiteExternalChannelRepository(ExternalChannelRepository):
    def __init__(self, database: Database) -> None:
        self._database = database

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

    async def list_inflight_turns(self) -> tuple[ChannelTurnRecord, ...]:
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
    ) -> ChannelTurnRecord:
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
                        attempt, created_at, updated_at
                    )
                    SELECT ?, channel_turn_id, connection_id, 'pending', 1, ?, ?
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
        return await self._required_turn(channel_turn_id)

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
                "SELECT * FROM channel_deliveries WHERE delivery_id = ?",
                (str(acknowledgement.delivery_id),),
            )
            current = await cursor.fetchone()
            await cursor.close()
            if current is None:
                raise KeyError(f"unknown channel delivery {acknowledgement.delivery_id}")
            if UUID(str(current["channel_turn_id"])) != acknowledgement.channel_turn_id:
                raise ValueError("delivery acknowledgement channel_turn_id mismatch")
            current_lease_id = (
                UUID(str(current["lease_id"])) if current["lease_id"] is not None else None
            )
            if current_lease_id != acknowledgement.lease_id:
                raise ValueError("delivery acknowledgement lease_id mismatch")
            current_status = ChannelDeliveryStatus(str(current["status"]))
            if current_status is ChannelDeliveryStatus.DELIVERED:
                if acknowledgement.status is not ChannelDeliveryStatus.DELIVERED:
                    raise ValueError("a delivered channel reply cannot be downgraded")
                return _delivery_record(current)
            if current_status is not ChannelDeliveryStatus.SENDING:
                raise ValueError("channel delivery is not owned by an active sending lease")
            await connection.execute(
                """
                UPDATE channel_deliveries SET status = ?, provider_message_id = ?,
                    last_error_json = ?, updated_at = ?, delivered_at = ?
                WHERE delivery_id = ?
                """,
                (
                    acknowledgement.status.value,
                    acknowledgement.provider_message_id,
                    _error_json(acknowledgement.error),
                    updated_at.isoformat(),
                    (
                        acknowledgement.acknowledged_at.isoformat()
                        if acknowledgement.status is ChannelDeliveryStatus.DELIVERED
                        else None
                    ),
                    str(acknowledgement.delivery_id),
                ),
            )
            cursor = await connection.execute(
                "SELECT * FROM channel_deliveries WHERE delivery_id = ?",
                (str(acknowledgement.delivery_id),),
            )
            updated = await cursor.fetchone()
            await cursor.close()
        if updated is None:
            raise RuntimeError("channel delivery disappeared after acknowledgement")
        return _delivery_record(updated)

    async def claim_delivery(
        self,
        claim: ChannelDeliveryClaimRequest,
        *,
        claimed_at: datetime,
    ) -> ChannelDeliveryRecord | None:
        lease_expires_at = claimed_at + timedelta(seconds=claim.lease_seconds)
        async with self._database.transaction() as connection:
            cursor = await connection.execute(
                "SELECT * FROM channel_deliveries WHERE delivery_id = ?",
                (str(claim.delivery_id),),
            )
            current = await cursor.fetchone()
            await cursor.close()
            if current is None:
                raise KeyError(f"unknown channel delivery {claim.delivery_id}")
            if UUID(str(current["channel_turn_id"])) != claim.channel_turn_id:
                raise ValueError("delivery claim channel_turn_id mismatch")

            status = ChannelDeliveryStatus(str(current["status"]))
            if status is ChannelDeliveryStatus.DELIVERED:
                return _delivery_record(current)
            if status is ChannelDeliveryStatus.CANCELLED:
                raise ValueError("a cancelled channel delivery cannot be claimed")

            current_lease_id = (
                UUID(str(current["lease_id"])) if current["lease_id"] is not None else None
            )
            current_expiry = _datetime(current["lease_expires_at"])
            same_lease = current_lease_id == claim.lease_id
            if (
                status is ChannelDeliveryStatus.SENDING
                and not same_lease
                and current_expiry is not None
                and current_expiry > claimed_at
            ):
                return None

            attempt = int(current["attempt"])
            if status is ChannelDeliveryStatus.FAILED or (
                status is ChannelDeliveryStatus.SENDING and not same_lease
            ):
                attempt += 1

            await connection.execute(
                """
                UPDATE channel_deliveries
                SET status = 'sending', attempt = ?, lease_id = ?, lease_expires_at = ?,
                    provider_message_id = NULL, last_error_json = NULL, updated_at = ?
                WHERE delivery_id = ?
                """,
                (
                    attempt,
                    str(claim.lease_id),
                    lease_expires_at.isoformat(),
                    claimed_at.isoformat(),
                    str(claim.delivery_id),
                ),
            )
            cursor = await connection.execute(
                "SELECT * FROM channel_deliveries WHERE delivery_id = ?",
                (str(claim.delivery_id),),
            )
            updated = await cursor.fetchone()
            await cursor.close()
        if updated is None:
            raise RuntimeError("channel delivery disappeared after claim")
        return _delivery_record(updated)

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


def _delivery_record(row: object) -> ChannelDeliveryRecord:
    item = row
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
