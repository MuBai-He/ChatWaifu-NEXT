# pyright: reportPrivateUsage=false
"Atomic fixed-group identity, admission and delivery fences, without external I/O."

from __future__ import annotations

import base64
import json
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import cast
from uuid import UUID, uuid4

import aiosqlite
from chatwaifu_protocol.channel_groups import ChannelGroupDeliveryTarget, ChannelGroupPauseReason
from chatwaifu_protocol.channels import ChannelDeliveryPartDraft, ChannelImageDeliveryPartPayload
from chatwaifu_protocol.events import GenericCoreEvent, PrivacyLevel

from chatwaifu_runtime.external_channels.group_models import (
    ChannelGroupAdmission,
    ChannelGroupAdmissionResult,
    ChannelGroupAudienceObservation,
    ChannelGroupAuthorization,
    ChannelGroupPlanResult,
    ChannelGroupRouteLineage,
    ChannelGroupRouteMember,
    ChannelGroupRouteRecord,
    ChannelGroupTransition,
    ChannelParticipantLinkRecord,
    audience_fingerprint,
    aware,
    qq_id,
    revision,
    strict_bool,
)
from chatwaifu_runtime.external_channels.group_ports import ChannelGroupRepository
from chatwaifu_runtime.external_channels.models import ChannelBindingRecord
from chatwaifu_runtime.external_channels.presentation import group_text_parts_match_reply
from chatwaifu_runtime.external_channels.service import (
    ChannelBusyError,
    ChannelConflictError,
    ChannelNotFoundError,
    ChannelPolicyError,
)
from chatwaifu_runtime.persistence.database import Database
from chatwaifu_runtime.persistence.event_store import EventStore
from chatwaifu_runtime.persistence.sqlite_external_channels import (
    _TURN_SELECT,
    SQLiteExternalChannelRepository,
    _binding_record,
    _single_part_draft,
    _turn_record,
    _validate_delivery_part_drafts,
)


async def _one(
    connection: aiosqlite.Connection, sql: str, args: tuple[object, ...] = ()
) -> aiosqlite.Row | None:
    cursor = await connection.execute(sql, args)
    try:
        return await cursor.fetchone()
    finally:
        await cursor.close()


async def _rows(
    connection: aiosqlite.Connection, sql: str, args: tuple[object, ...] = ()
) -> tuple[aiosqlite.Row, ...]:
    cursor = await connection.execute(sql, args)
    try:
        return tuple(await cursor.fetchall())
    finally:
        await cursor.close()


def _stamp(value: datetime) -> datetime:
    return aware(value).astimezone(UTC)


def _strings(raw: object) -> tuple[str, ...]:
    value: object = json.loads(str(raw))
    if not isinstance(value, list):
        raise ValueError("persisted identity list invalid")
    values = cast(list[object], value)
    if not all(isinstance(item, str) for item in values):
        raise ValueError("persisted identity list invalid")
    return tuple(cast(list[str], value))


def _link(row: aiosqlite.Row) -> ChannelParticipantLinkRecord:
    return ChannelParticipantLinkRecord(
        link_id=UUID(row["link_id"]),
        provider_id=row["provider_id"],
        account_key=row["account_key"],
        sender_key=row["sender_key"],
        participant_id=row["participant_id"],
        enabled=bool(row["enabled"]),
        revision=int(row["revision"]),
        created_at=datetime.fromisoformat(row["created_at"]),
        updated_at=datetime.fromisoformat(row["updated_at"]),
    )


def _next_cursor(
    connection_id: UUID, rows: tuple[aiosqlite.Row, ...], limit: int, identity: str
) -> str | None:
    if len(rows) <= limit:
        return None
    row = rows[limit - 1]
    return base64.urlsafe_b64encode(
        json.dumps(
            [str(connection_id), row["created_at"], row[identity]], separators=(",", ":")
        ).encode()
    ).decode()


def _page_boundary(connection_id: UUID, limit: int, cursor: str | None) -> tuple[str, str] | None:
    if type(limit) is not int or not 1 <= limit <= 50:
        raise ValueError("page limit must be an integer in 1..50")
    if cursor is None:
        return None
    if type(cursor) is not str or not 1 <= len(cursor) <= 256:
        raise ValueError("bounded cursor required")
    try:
        raw: object = json.loads(base64.b64decode(cursor, altchars=b"-_", validate=True))
        if not isinstance(raw, list):
            raise ValueError("cursor must be a list")
        values = cast(list[object], raw)
        if len(values) != 3 or any(type(value) is not str for value in values):
            raise ValueError("cursor shape invalid")
        owner, stamp, identity = cast(tuple[str, str, str], tuple(values))
        if owner != str(connection_id):
            raise ValueError("cursor belongs to another connection")
        aware(datetime.fromisoformat(stamp))
        UUID(identity)
        return stamp, identity
    except (ValueError, TypeError, UnicodeDecodeError) as error:
        raise ValueError("invalid group cursor") from error


class SQLiteChannelGroupRepository(ChannelGroupRepository):
    def __init__(self, database: Database, events: EventStore | None = None) -> None:
        self._database = database
        self._events = events
        self._deliveries = SQLiteExternalChannelRepository(database, events)

    async def get_link(self, link_id: UUID) -> ChannelParticipantLinkRecord | None:
        row = await self._database.fetchone(
            "SELECT * FROM channel_participant_links WHERE link_id=?", (str(link_id),)
        )
        return _link(row) if row is not None else None

    async def list_links(
        self, connection_id: UUID, *, limit: int = 25, cursor: str | None = None
    ) -> tuple[tuple[ChannelParticipantLinkRecord, ...], str | None]:
        boundary = _page_boundary(connection_id, limit, cursor)
        async with self._database.transaction() as db:
            conn = await _one(
                db,
                "SELECT account_key FROM channel_connections WHERE connection_id=?",
                (str(connection_id),),
            )
            if conn is None:
                raise ChannelNotFoundError("group connection unavailable")
            sql = (
                "SELECT * FROM channel_participant_links "
                "WHERE provider_id='qq_napcat' AND account_key=?"
            )
            args: tuple[object, ...] = (conn["account_key"],)
            if boundary is not None:
                sql += " AND (created_at,link_id)<(?,?)"
                args += boundary
            rows = await _rows(
                db, sql + " ORDER BY created_at DESC,link_id DESC LIMIT ?", (*args, limit + 1)
            )
            next_cursor = _next_cursor(connection_id, rows, limit, "link_id")
            return tuple(_link(row) for row in rows[:limit]), next_cursor

    async def list_routes(
        self, connection_id: UUID, *, limit: int = 25, cursor: str | None = None
    ) -> tuple[tuple[ChannelGroupRouteRecord, ...], str | None]:
        boundary = _page_boundary(connection_id, limit, cursor)
        async with self._database.transaction() as db:
            if (
                await _one(
                    db,
                    "SELECT 1 FROM channel_connections WHERE connection_id=?",
                    (str(connection_id),),
                )
                is None
            ):
                raise ChannelNotFoundError("group connection unavailable")
            sql = "SELECT route_id,created_at FROM channel_group_routes WHERE connection_id=?"
            args: tuple[object, ...] = (str(connection_id),)
            if boundary is not None:
                sql += " AND (created_at,route_id)<(?,?)"
                args += boundary
            rows = await _rows(
                db, sql + " ORDER BY created_at DESC,route_id DESC LIMIT ?", (*args, limit + 1)
            )
            items = tuple([await self._route_tx(db, UUID(row["route_id"])) for row in rows[:limit]])
            return items, _next_cursor(connection_id, rows, limit, "route_id")

    async def _connection_tx(
        self, db: aiosqlite.Connection, connection_id: UUID, account_key: str
    ) -> aiosqlite.Row:
        row = await _one(
            db, "SELECT * FROM channel_connections WHERE connection_id=?", (str(connection_id),)
        )
        if row is None or row["deleted_at"] is not None:
            raise ChannelNotFoundError("group connection unavailable")
        if row["provider_id"] != "qq_napcat" or row["account_key"] != account_key:
            raise ChannelPolicyError("group account does not match the connection")
        if not row["enabled"] or row["status"] != "ready":
            raise ChannelPolicyError("group connection must be enabled and ready")
        return row

    async def create_observation(self, observation: ChannelGroupAudienceObservation) -> None:
        async with self._database.transaction() as db:
            conn = await self._connection_tx(db, observation.connection_id, observation.account_key)
            if conn["revision"] != observation.connection_revision:
                raise ChannelConflictError("connection changed during observation")
            await db.execute(
                "INSERT INTO channel_group_audience_observations VALUES(?,?,?,?,?,?,?,?)",
                (
                    str(observation.observation_id),
                    str(observation.connection_id),
                    observation.connection_revision,
                    observation.account_key,
                    observation.group_id,
                    json.dumps(sorted(observation.member_ids)),
                    observation.observed_at.isoformat(),
                    observation.expires_at.isoformat(),
                ),
            )

    async def _observation_tx(
        self, db: aiosqlite.Connection, observation_id: UUID
    ) -> ChannelGroupAudienceObservation:
        row = await _one(
            db,
            "SELECT * FROM channel_group_audience_observations WHERE observation_id=?",
            (str(observation_id),),
        )
        if row is None:
            raise ChannelNotFoundError("group audience observation unavailable")
        return ChannelGroupAudienceObservation(
            observation_id=UUID(row["observation_id"]),
            connection_id=UUID(row["connection_id"]),
            connection_revision=int(row["connection_revision"]),
            account_key=row["account_key"],
            group_id=row["group_id"],
            member_ids=_strings(row["member_ids_json"]),
            observed_at=datetime.fromisoformat(row["observed_at"]),
            expires_at=datetime.fromisoformat(row["expires_at"]),
        )

    async def get_observation(self, observation_id: UUID) -> ChannelGroupAudienceObservation | None:
        async with self._database.transaction() as db:
            try:
                return await self._observation_tx(db, observation_id)
            except ChannelNotFoundError:
                return None

    async def _fresh_observation_tx(
        self, db: aiosqlite.Connection, observation_id: UUID, as_of: datetime
    ) -> ChannelGroupAudienceObservation:
        observation = await self._observation_tx(db, observation_id)
        conn = await self._connection_tx(db, observation.connection_id, observation.account_key)
        if conn["revision"] != observation.connection_revision:
            raise ChannelConflictError("connection changed since audience observation")
        if not observation.observed_at <= as_of < observation.expires_at:
            raise ChannelPolicyError("fresh audience observation required")
        return observation

    async def create_link(
        self, observation_id: UUID, sender_key: str, participant_id: str, *, created_at: datetime
    ) -> ChannelParticipantLinkRecord:
        qq_id(sender_key)
        stamp = _stamp(created_at)
        async with self._database.transaction() as db:
            observation = await self._fresh_observation_tx(db, observation_id, stamp)
            if sender_key not in observation.member_ids:
                raise ChannelPolicyError("sender is not in the observed audience")
            if (
                await _one(
                    db, "SELECT 1 FROM participants WHERE participant_id=?", (participant_id,)
                )
                is None
            ):
                raise ChannelPolicyError("registered participant required")
            existing = await _one(
                db,
                (
                    "SELECT * FROM channel_participant_links WHERE "
                    "provider_id='qq_napcat' AND account_key=? AND sender_key=?"
                ),
                (observation.account_key, sender_key),
            )
            if existing is not None:
                if existing["participant_id"] != participant_id:
                    raise ChannelConflictError("participant link identity cannot change")
                return _link(existing)
            link_id = uuid4()
            await db.execute(
                "INSERT INTO channel_participant_links VALUES(?,'qq_napcat',?,?,?,1,1,?,?)",
                (
                    str(link_id),
                    observation.account_key,
                    sender_key,
                    participant_id,
                    stamp.isoformat(),
                    stamp.isoformat(),
                ),
            )
            row = await _one(
                db, "SELECT * FROM channel_participant_links WHERE link_id=?", (str(link_id),)
            )
            assert row is not None
            return _link(row)

    async def _route_tx(self, db: aiosqlite.Connection, route_id: UUID) -> ChannelGroupRouteRecord:
        row = await _one(
            db, "SELECT * FROM channel_group_routes WHERE route_id=?", (str(route_id),)
        )
        if row is None:
            raise ChannelNotFoundError("group route unavailable")
        members = await _rows(
            db,
            (
                "SELECT * FROM channel_group_route_members WHERE route_id=? "
                "AND route_revision=? ORDER BY sender_key"
            ),
            (str(route_id), row["revision"]),
        )
        return ChannelGroupRouteRecord(
            route_id=UUID(row["route_id"]),
            connection_id=UUID(row["connection_id"]),
            account_key=row["account_key"],
            group_id=row["group_id"],
            character_id=row["character_id"],
            scene_id=row["scene_id"],
            display_name=row["display_name"],
            revision=int(row["revision"]),
            enabled=bool(row["enabled"]),
            pause_reason=ChannelGroupPauseReason(row["pause_reason"])
            if row["pause_reason"]
            else None,
            observation_id=UUID(row["observation_id"]),
            members=tuple(
                ChannelGroupRouteMember(
                    UUID(m["link_id"]), m["sender_key"], m["participant_id"], bool(m["can_speak"])
                )
                for m in members
            ),
            created_at=datetime.fromisoformat(row["created_at"]),
            updated_at=datetime.fromisoformat(row["updated_at"]),
            deleted_at=datetime.fromisoformat(row["deleted_at"]) if row["deleted_at"] else None,
        )

    async def get_route(self, route_id: UUID) -> ChannelGroupRouteRecord | None:
        async with self._database.transaction() as db:
            try:
                return await self._route_tx(db, route_id)
            except ChannelNotFoundError:
                return None

    async def find_route(
        self, connection_id: UUID, group_id: str
    ) -> ChannelGroupRouteRecord | None:
        qq_id(group_id)
        async with self._database.transaction() as db:
            row = await _one(
                db,
                ("SELECT route_id FROM channel_group_routes WHERE connection_id=? AND group_id=?"),
                (str(connection_id), group_id),
            )
            return await self._route_tx(db, UUID(row["route_id"])) if row else None

    async def is_group_scene(self, scene_id: str) -> bool:
        row = await self._database.fetchone(
            "SELECT 1 FROM channel_group_route_versions WHERE scene_id=? LIMIT 1",
            (scene_id,),
        )
        return row is not None

    async def _validate_members_tx(
        self,
        db: aiosqlite.Connection,
        route: ChannelGroupRouteRecord,
        observation: ChannelGroupAudienceObservation,
        *,
        new_scene: bool,
    ) -> None:
        if (route.connection_id, route.account_key, route.group_id) != (
            observation.connection_id,
            observation.account_key,
            observation.group_id,
        ):
            raise ChannelPolicyError("observation belongs to another group")
        if {m.sender_key for m in route.members} != set(observation.member_ids):
            raise ChannelPolicyError("every observed audience member must be linked")
        for member in route.members:
            link = await _one(
                db,
                "SELECT * FROM channel_participant_links WHERE link_id=?",
                (str(member.link_id),),
            )
            if (
                link is None
                or not link["enabled"]
                or (link["account_key"], link["sender_key"], link["participant_id"])
                != (route.account_key, member.sender_key, member.participant_id)
            ):
                raise ChannelPolicyError("enabled immutable audience link required")
        scene = await _one(
            db, "SELECT * FROM conversation_scenes WHERE scene_id=?", (route.scene_id,)
        )
        if scene is None or set(_strings(scene["participant_ids_json"])) != {
            m.participant_id for m in route.members
        }:
            raise ChannelPolicyError("scene must match the complete mapped audience")
        if new_scene:
            used = await _one(
                db,
                (
                    "SELECT 1 FROM channel_group_route_versions WHERE scene_id=? "
                    "UNION ALL SELECT 1 FROM sessions WHERE scene_id=? LIMIT 1"
                ),
                (route.scene_id, route.scene_id),
            )
            if used or datetime.fromisoformat(scene["created_at"]) < observation.observed_at:
                raise ChannelPolicyError("newly created unused scene required")

    async def _version_tx(self, db: aiosqlite.Connection, route: ChannelGroupRouteRecord) -> None:
        await db.execute(
            "INSERT INTO channel_group_route_versions VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                str(route.route_id),
                route.revision,
                str(route.connection_id),
                route.account_key,
                route.group_id,
                route.character_id,
                route.scene_id,
                str(route.observation_id),
                route.audience_fingerprint,
                int(route.enabled),
                route.pause_reason.value if route.pause_reason else None,
                route.updated_at.isoformat(),
            ),
        )
        for member in route.members:
            await db.execute(
                "INSERT INTO channel_group_route_members VALUES(?,?,?,?,?,?,?)",
                (
                    str(route.route_id),
                    route.revision,
                    route.account_key,
                    str(member.link_id),
                    member.sender_key,
                    member.participant_id,
                    int(member.can_speak),
                ),
            )

    async def create_route(self, route: ChannelGroupRouteRecord) -> ChannelGroupRouteRecord:
        if route.enabled or route.revision != 1 or route.deleted_at is not None:
            raise ChannelPolicyError("new routes must be revision 1 and disabled")
        stamp = _stamp(route.created_at)
        async with self._database.transaction() as db:
            conn = await self._connection_tx(db, route.connection_id, route.account_key)
            if conn["character_id"] != route.character_id:
                raise ChannelPolicyError("route character must match connection")
            if await _one(
                db,
                "SELECT 1 FROM channel_group_routes WHERE connection_id=? AND group_id=?",
                (str(route.connection_id), route.group_id),
            ):
                raise ChannelConflictError(
                    "group route identity already exists, including deleted routes"
                )
            observation = await self._fresh_observation_tx(db, route.observation_id, stamp)
            await self._validate_members_tx(db, route, observation, new_scene=True)
            await db.execute(
                "INSERT INTO channel_group_routes VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    str(route.route_id),
                    str(route.connection_id),
                    route.account_key,
                    route.group_id,
                    route.character_id,
                    route.scene_id,
                    route.display_name,
                    route.revision,
                    0,
                    route.pause_reason.value if route.pause_reason else None,
                    str(route.observation_id),
                    route.audience_fingerprint,
                    stamp.isoformat(),
                    _stamp(route.updated_at).isoformat(),
                    None,
                ),
            )
            await self._version_tx(db, route)
            await db.execute(
                "INSERT INTO channel_group_route_heads VALUES(?,NULL,NULL,NULL,?)",
                (str(route.route_id), stamp.isoformat()),
            )
            return route

    async def _cancel_tx(
        self, db: aiosqlite.Connection, route_id: UUID, stamp: datetime
    ) -> ChannelGroupTransition:
        turns = await _rows(
            db,
            (
                "SELECT * FROM channel_turns WHERE group_route_id=? AND "
                "(status IN ('accepted','processing','cancelling') OR "
                "channel_turn_id=(SELECT active_channel_turn_id FROM "
                "channel_group_route_heads WHERE route_id=?))"
            ),
            (str(route_id), str(route_id)),
        )
        head = await _one(
            db,
            ("SELECT active_channel_turn_id FROM channel_group_route_heads WHERE route_id=?"),
            (str(route_id),),
        )
        active = head["active_channel_turn_id"] if head else None
        await db.execute(
            (
                "UPDATE channel_turns SET status=CASE WHEN channel_turn_id=? "
                "THEN 'cancelling' ELSE 'cancelled' END, "
                "revision=revision+1,updated_at=?,completed_at=CASE WHEN "
                "channel_turn_id=? THEN completed_at ELSE "
                "COALESCE(completed_at,?) END WHERE group_route_id=? AND "
                "status IN ('accepted','processing','cancelling')"
            ),
            (active, stamp.isoformat(), active, stamp.isoformat(), str(route_id)),
        )
        plans = await _rows(
            db,
            (
                "SELECT delivery_id FROM channel_deliveries WHERE "
                "group_route_id=? AND status IN ('pending','sending')"
            ),
            (str(route_id),),
        )
        for plan in plans:
            delivery_id = UUID(plan["delivery_id"])
            await db.execute(
                (
                    "UPDATE channel_deliveries SET "
                    "cancel_requested_at=COALESCE(cancel_requested_at,?),updated_"
                    "at=? WHERE delivery_id=?"
                ),
                (stamp.isoformat(), stamp.isoformat(), str(delivery_id)),
            )
            await db.execute(
                (
                    "UPDATE channel_delivery_parts SET "
                    "status='cancelled',updated_at=?,lease_id=NULL,lease_expires_"
                    "at=NULL WHERE delivery_id=? AND status IN "
                    "('pending','sending')"
                ),
                (stamp.isoformat(), str(delivery_id)),
            )
            await self._deliveries._derive_delivery_plan_state_tx(db, delivery_id, stamp)
        await db.execute(
            (
                "UPDATE channel_group_route_heads SET "
                "latest_channel_turn_id=NULL,pending_channel_turn_id=NULL,upd"
                "ated_at=? WHERE route_id=?"
            ),
            (stamp.isoformat(), str(route_id)),
        )
        events: tuple[GenericCoreEvent, ...] = ()
        for turn in turns:
            if turn["channel_turn_id"] != active and turn["status"] in {
                "accepted",
                "processing",
                "cancelling",
            }:
                events += await self._event_tx(
                    db,
                    turn,
                    "channel.turn_cancelled",
                    stamp,
                    dict(
                        connection_id=turn["connection_id"],
                        channel_turn_id=turn["channel_turn_id"],
                        group_route_id=str(route_id),
                        reason="group_work_revoked",
                    ),
                )
        return ChannelGroupTransition(
            displaced_turn_ids=tuple(UUID(t["channel_turn_id"]) for t in turns),
            affected_session_ids=tuple(sorted({UUID(t["session_id"]) for t in turns}, key=str)),
            persisted_events=events,
        )

    async def update_route(
        self,
        route_id: UUID,
        *,
        expected_revision: int,
        enabled: bool,
        observation_id: UUID | None,
        members: tuple[ChannelGroupRouteMember, ...],
        scene_id: str,
        updated_at: datetime,
    ) -> ChannelGroupTransition:
        revision(expected_revision)
        strict_bool(enabled)
        audience_fingerprint(members)
        stamp = _stamp(updated_at)
        async with self._database.transaction() as db:
            old = await self._route_tx(db, route_id)
            if old.revision != expected_revision:
                raise ChannelConflictError("group route revision changed")
            if old.deleted_at is not None:
                raise ChannelPolicyError("deleted route cannot be reused")
            if enabled and observation_id is None:
                raise ChannelPolicyError("explicit audience revalidation required")
            new = replace(
                old,
                revision=old.revision + 1,
                enabled=enabled,
                scene_id=scene_id,
                members=members,
                observation_id=observation_id or old.observation_id,
                updated_at=stamp,
                pause_reason=None if enabled else ChannelGroupPauseReason.OPERATOR_DISABLED,
            )
            changed_audience = old.audience_fingerprint != new.audience_fingerprint
            if changed_audience and scene_id == old.scene_id:
                raise ChannelPolicyError("changed audience requires a new scene")
            if not changed_audience and scene_id != old.scene_id:
                raise ChannelPolicyError("unchanged audience retains its scene")
            if observation_id is not None:
                observation = await self._fresh_observation_tx(db, observation_id, stamp)
                await self._validate_members_tx(db, new, observation, new_scene=changed_audience)
            elif changed_audience:
                raise ChannelPolicyError("changed audience requires an observation")
            if enabled:
                conn = await self._connection_tx(db, new.connection_id, new.account_key)
                if conn["character_id"] != new.character_id:
                    raise ChannelPolicyError("route character changed")
            transition = await self._cancel_tx(db, route_id, stamp)
            await self._store_updated_route_tx(db, new)
            return replace(transition, route=new)

    async def _store_updated_route_tx(
        self, db: aiosqlite.Connection, route: ChannelGroupRouteRecord
    ) -> None:
        await db.execute(
            (
                "UPDATE channel_group_routes SET "
                "scene_id=?,revision=?,enabled=?,pause_reason=?,observation_i"
                "d=?,audience_fingerprint=?,updated_at=?,deleted_at=? WHERE "
                "route_id=?"
            ),
            (
                route.scene_id,
                route.revision,
                int(route.enabled),
                route.pause_reason.value if route.pause_reason else None,
                str(route.observation_id),
                route.audience_fingerprint,
                route.updated_at.isoformat(),
                route.deleted_at.isoformat() if route.deleted_at else None,
                str(route.route_id),
            ),
        )
        await self._version_tx(db, route)

    async def _pause_tx(
        self,
        db: aiosqlite.Connection,
        route: ChannelGroupRouteRecord,
        reason: ChannelGroupPauseReason,
        stamp: datetime,
    ) -> ChannelGroupTransition:
        transition = await self._cancel_tx(db, route.route_id, stamp)
        if not route.enabled and route.pause_reason == reason:
            return replace(transition, route=route)
        new = replace(
            route,
            revision=route.revision + 1,
            enabled=False,
            pause_reason=reason,
            updated_at=stamp,
            deleted_at=stamp
            if reason is ChannelGroupPauseReason.ROUTE_DELETED
            else route.deleted_at,
        )
        await self._store_updated_route_tx(db, new)
        return replace(transition, route=new)

    async def pause_routes(
        self,
        *,
        reason: ChannelGroupPauseReason,
        updated_at: datetime,
        connection_id: UUID | None = None,
        group_id: str | None = None,
        scene_id: str | None = None,
        link_id: UUID | None = None,
    ) -> tuple[ChannelGroupTransition, ...]:
        stamp = _stamp(updated_at)
        raw_reason = cast(object, reason)
        if not isinstance(raw_reason, ChannelGroupPauseReason) or all(
            x is None for x in (connection_id, group_id, scene_id, link_id)
        ):
            raise ValueError("typed reason and bounded selector required")
        if group_id is not None and connection_id is None:
            raise ValueError("group selector requires its connection")
        async with self._database.transaction() as db:
            return await self._pause_matching_tx(
                db, reason, stamp, connection_id, group_id, scene_id, link_id
            )

    async def _pause_matching_tx(
        self,
        db: aiosqlite.Connection,
        reason: ChannelGroupPauseReason,
        stamp: datetime,
        connection_id: UUID | None,
        group_id: str | None,
        scene_id: str | None,
        link_id: UUID | None,
    ) -> tuple[ChannelGroupTransition, ...]:
        predicates: list[str] = []
        args: list[object] = []
        for column, value in (
            ("connection_id", connection_id),
            ("group_id", group_id),
            ("scene_id", scene_id),
        ):
            if value is not None:
                predicates.append("r." + column + "=?")
                args.append(str(value))
        if link_id is not None:
            predicates.append(
                "EXISTS(SELECT 1 FROM channel_group_route_members m WHERE "
                "m.route_id=r.route_id AND m.route_revision=r.revision AND "
                "m.link_id=?)"
            )
            args.append(str(link_id))
        rows = await _rows(
            db,
            "SELECT r.route_id FROM channel_group_routes r WHERE " + " AND ".join(predicates),
            tuple(args),
        )
        result: list[ChannelGroupTransition] = []
        for row in rows:
            result.append(
                await self._pause_tx(
                    db, await self._route_tx(db, UUID(row["route_id"])), reason, stamp
                )
            )
        return tuple(result)

    async def update_link(
        self, link_id: UUID, *, enabled: bool, expected_revision: int, updated_at: datetime
    ) -> ChannelGroupTransition:
        strict_bool(enabled)
        revision(expected_revision)
        stamp = _stamp(updated_at)
        async with self._database.transaction() as db:
            row = await _one(
                db, "SELECT * FROM channel_participant_links WHERE link_id=?", (str(link_id),)
            )
            if row is None:
                raise ChannelNotFoundError("participant link unavailable")
            if row["revision"] != expected_revision:
                raise ChannelConflictError("participant link revision changed")
            await db.execute(
                (
                    "UPDATE channel_participant_links SET "
                    "enabled=?,revision=revision+1,updated_at=? WHERE link_id=?"
                ),
                (int(enabled), stamp.isoformat(), str(link_id)),
            )
            transitions = await self._pause_matching_tx(
                db, ChannelGroupPauseReason.LINK_REVOKED, stamp, None, None, None, link_id
            )
            updated = await _one(
                db, "SELECT * FROM channel_participant_links WHERE link_id=?", (str(link_id),)
            )
            assert updated is not None
            return ChannelGroupTransition(
                link=_link(updated),
                displaced_turn_ids=tuple(t for x in transitions for t in x.displaced_turn_ids),
                affected_session_ids=tuple(
                    sorted({s for x in transitions for s in x.affected_session_ids}, key=str)
                ),
                persisted_events=tuple(e for x in transitions for e in x.persisted_events),
            )

    async def find_group_binding(
        self, route_id: UUID, scene_id: str, sender_key: str
    ) -> ChannelBindingRecord | None:
        qq_id(sender_key)
        row = await self._database.fetchone(
            (
                "SELECT * FROM channel_bindings WHERE chat_type='group' AND "
                "group_route_id=? AND scene_id=? AND sender_key=?"
            ),
            (str(route_id), scene_id, sender_key),
        )
        return _binding_record(row) if row else None

    async def _session_tx(
        self,
        db: aiosqlite.Connection,
        session_id: UUID,
        route: ChannelGroupRouteRecord,
        member: ChannelGroupRouteMember,
    ) -> None:
        session = await _one(db, "SELECT * FROM sessions WHERE session_id=?", (str(session_id),))
        if session is None or (
            session["state"] != "ready"
            or session["character_id"] != route.character_id
            or session["participant_id"] != member.participant_id
            or session["scene_id"] != route.scene_id
            or session["scene_kind"] != "shared"
            or session["user_scope"] != f"scene:{route.scene_id}"
            or session["state_scope"] != f"scene_member:{route.scene_id}:{member.participant_id}"
            or set(_strings(session["audience_json"])) != {m.participant_id for m in route.members}
        ):
            raise ChannelPolicyError("persisted group session identity does not match route")

    async def _authorization_tx(
        self, db: aiosqlite.Connection, lineage: ChannelGroupRouteLineage
    ) -> ChannelGroupAuthorization:
        try:
            route = await self._route_tx(db, lineage.route_id)
            conn = await self._connection_tx(db, route.connection_id, route.account_key)
        except (ChannelPolicyError, ChannelNotFoundError):
            return ChannelGroupAuthorization(False, "connection_unavailable")
        if (
            not route.enabled
            or route.deleted_at
            or route.pause_reason
            or route.revision != lineage.route_revision
        ):
            return ChannelGroupAuthorization(False, "route_revoked")
        if (
            route.scene_id != lineage.scene_id
            or route.audience_fingerprint != lineage.audience_fingerprint
            or conn["character_id"] != route.character_id
        ):
            return ChannelGroupAuthorization(False, "route_identity_changed")
        turn = await _one(
            db,
            "SELECT * FROM channel_turns WHERE channel_turn_id=?",
            (str(lineage.channel_turn_id),),
        )
        head = await _one(
            db, "SELECT * FROM channel_group_route_heads WHERE route_id=?", (str(route.route_id),)
        )
        if (
            turn is None
            or head is None
            or head["latest_channel_turn_id"] != str(lineage.channel_turn_id)
        ):
            return ChannelGroupAuthorization(False, "superseded")
        if (
            turn["binding_id"] != str(lineage.binding_id)
            or turn["group_lineage_version"] != 1
            or turn["group_route_id"] != str(route.route_id)
            or turn["group_route_revision"] != route.revision
            or turn["connection_id"] != str(route.connection_id)
            or turn["account_key"] != route.account_key
            or turn["conversation_key"] != f"group:{route.group_id}"
            or turn["chat_type"] != "group"
            or turn["principal_scope"] != f"scene:{route.scene_id}"
            or turn["status"] not in {"accepted", "processing", "completed", "failed"}
        ):
            return ChannelGroupAuthorization(False, "turn_revoked")
        binding = await _one(
            db, "SELECT * FROM channel_bindings WHERE binding_id=?", (str(lineage.binding_id),)
        )
        member = next(
            (m for m in route.members if m.sender_key == turn["sender_key"] and m.can_speak), None
        )
        if (
            binding is None
            or member is None
            or binding["chat_type"] != "group"
            or (
                binding["group_route_id"] != str(route.route_id)
                or binding["scene_id"] != route.scene_id
                or binding["participant_id"] != member.participant_id
                or binding["participant_link_id"] != str(member.link_id)
                or binding["session_id"] != turn["session_id"]
                or binding["sender_key"] != member.sender_key
                or binding["conversation_key"] != turn["conversation_key"]
                or binding["connection_id"] != turn["connection_id"]
            )
        ):
            return ChannelGroupAuthorization(False, "binding_revoked")
        for audience_member in route.members:
            link = await _one(
                db,
                "SELECT * FROM channel_participant_links WHERE link_id=?",
                (str(audience_member.link_id),),
            )
            if (
                link is None
                or not link["enabled"]
                or (link["account_key"], link["sender_key"], link["participant_id"])
                != (route.account_key, audience_member.sender_key, audience_member.participant_id)
            ):
                return ChannelGroupAuthorization(False, "audience_link_revoked")
        try:
            await self._session_tx(db, UUID(turn["session_id"]), route, member)
        except ChannelPolicyError:
            return ChannelGroupAuthorization(False, "session_revoked")
        return ChannelGroupAuthorization(True, "eligible")

    async def authorize_group_turn(
        self, lineage: ChannelGroupRouteLineage
    ) -> ChannelGroupAuthorization:
        async with self._database.transaction() as db:
            return await self._authorization_tx(db, lineage)

    async def _admission_result_tx(
        self,
        db: aiosqlite.Connection,
        turn_id: UUID,
        *,
        duplicate: bool,
        displaced: tuple[UUID, ...] = (),
        dispatch: bool = False,
    ) -> ChannelGroupAdmissionResult:
        row = await _one(db, _TURN_SELECT + " WHERE t.channel_turn_id=?", (str(turn_id),))
        assert row is not None
        binding = await _one(
            db, "SELECT * FROM channel_bindings WHERE binding_id=?", (row["binding_id"],)
        )
        version = await _one(
            db,
            "SELECT * FROM channel_group_route_versions WHERE route_id=? AND revision=?",
            (row["group_route_id"], row["group_route_revision"]),
        )
        assert binding is not None and version is not None
        return ChannelGroupAdmissionResult(
            turn=_turn_record(row),
            binding=_binding_record(binding),
            lineage=ChannelGroupRouteLineage(
                UUID(row["group_route_id"]),
                int(row["group_route_revision"]),
                turn_id,
                UUID(row["binding_id"]),
                version["scene_id"],
                version["audience_fingerprint"],
            ),
            duplicate=duplicate,
            dispatch_now=dispatch,
            displaced_turn_ids=displaced,
        )

    async def get_group_turn(self, channel_turn_id: UUID) -> ChannelGroupAdmissionResult | None:
        async with self._database.transaction() as db:
            row = await _one(
                db,
                "SELECT 1 FROM channel_turns WHERE channel_turn_id=? AND group_lineage_version=1",
                (str(channel_turn_id),),
            )
            if row is None:
                return None
            return await self._admission_result_tx(db, channel_turn_id, duplicate=True)

    async def find_group_turn(
        self, connection_id: UUID, group_id: str, external_message_id: str
    ) -> ChannelGroupAdmissionResult | None:
        group_id = qq_id(group_id)
        async with self._database.transaction() as db:
            row = await _one(
                db,
                "SELECT channel_turn_id FROM channel_turns WHERE connection_id=? "
                "AND chat_type='group' AND conversation_key=? AND external_message_id=? "
                "AND group_lineage_version=1",
                (str(connection_id), f"group:{group_id}", external_message_id),
            )
            if row is None:
                return None
            return await self._admission_result_tx(db, UUID(row["channel_turn_id"]), duplicate=True)

    async def list_group_turns(
        self, route_id: UUID, *, limit: int = 25, cursor: str | None = None
    ) -> tuple[tuple[ChannelGroupAdmissionResult, ...], str | None]:
        boundary = _page_boundary(route_id, limit, cursor)
        async with self._database.transaction() as db:
            await self._route_tx(db, route_id)
            sql = (
                "SELECT channel_turn_id,created_at FROM channel_turns "
                "WHERE group_route_id=? AND group_lineage_version=1"
            )
            args: tuple[object, ...] = (str(route_id),)
            if boundary is not None:
                sql += " AND (created_at,channel_turn_id)<(?,?)"
                args += boundary
            rows = await _rows(
                db,
                sql + " ORDER BY created_at DESC,channel_turn_id DESC LIMIT ?",
                (*args, limit + 1),
            )
            items = tuple(
                [
                    await self._admission_result_tx(
                        db, UUID(row["channel_turn_id"]), duplicate=True
                    )
                    for row in rows[:limit]
                ]
            )
            return items, _next_cursor(route_id, rows, limit, "channel_turn_id")

    async def admit_group_turn(
        self, admission: ChannelGroupAdmission
    ) -> ChannelGroupAdmissionResult:
        stamp = _stamp(admission.admitted_at)
        message = admission.message
        if message.received_at > stamp + timedelta(minutes=5):
            raise ChannelPolicyError("group event timestamp is unexpectedly future")
        async with self._database.transaction() as db:
            route = await self._route_tx(db, admission.route_id)
            conn = await self._connection_tx(db, message.connection_id, message.account_key)
            if (route.connection_id, route.account_key, route.group_id) != (
                message.connection_id,
                message.account_key,
                message.group_id,
            ):
                raise ChannelPolicyError("group route does not match message")
            if not route.enabled or route.deleted_at or route.pause_reason:
                raise ChannelPolicyError("group route disabled or paused")
            if route.revision != admission.expected_route_revision:
                raise ChannelConflictError("group route revision changed")
            if conn["character_id"] != route.character_id:
                raise ChannelPolicyError("group route character changed")
            member = next(
                (m for m in route.members if m.sender_key == message.sender_key and m.can_speak),
                None,
            )
            if member is None:
                raise ChannelPolicyError("group speaker is not granted")
            for audience_member in route.members:
                link = await _one(
                    db,
                    "SELECT * FROM channel_participant_links WHERE link_id=?",
                    (str(audience_member.link_id),),
                )
                if link is None or not link["enabled"]:
                    raise ChannelPolicyError("group audience link revoked")
            old = await _one(
                db,
                (
                    "SELECT * FROM channel_turns WHERE connection_id=? AND "
                    "chat_type='group' AND conversation_key=? AND "
                    "external_message_id=?"
                ),
                (
                    str(message.connection_id),
                    f"group:{message.group_id}",
                    message.external_message_id,
                ),
            )
            if old:
                if (
                    old["group_lineage_version"] != 1
                    or old["content_sha256"] != message.content_sha256
                ):
                    raise ChannelConflictError("raw group ID content conflict")
                return await self._admission_result_tx(
                    db, UUID(old["channel_turn_id"]), duplicate=True
                )
            await self._session_tx(db, admission.session_id, route, member)
            binding = await _one(
                db,
                (
                    "SELECT * FROM channel_bindings WHERE chat_type='group' AND "
                    "group_route_id=? AND scene_id=? AND sender_key=?"
                ),
                (str(route.route_id), route.scene_id, message.sender_key),
            )
            if binding is None:
                binding_id = uuid4()
                await db.execute(
                    (
                        "INSERT INTO "
                        "channel_bindings(binding_id,connection_id,conversation_key,s"
                        "ender_key,session_id,created_at,updated_at,chat_type,group_r"
                        "oute_id,scene_id,participant_link_id,participant_id) "
                        "VALUES(?,?,?,?,?,?,?,'group',?,?,?,?)"
                    ),
                    (
                        str(binding_id),
                        str(message.connection_id),
                        f"group:{message.group_id}",
                        message.sender_key,
                        str(admission.session_id),
                        stamp.isoformat(),
                        stamp.isoformat(),
                        str(route.route_id),
                        route.scene_id,
                        str(member.link_id),
                        member.participant_id,
                    ),
                )
            else:
                binding_id = UUID(binding["binding_id"])
                if binding["session_id"] != str(admission.session_id):
                    raise ChannelConflictError("group member binding already owns another session")
            head = await _one(
                db,
                "SELECT * FROM channel_group_route_heads WHERE route_id=?",
                (str(route.route_id),),
            )
            assert head is not None
            active = head["active_channel_turn_id"]
            if active is None:
                count = await _one(
                    db,
                    (
                        "SELECT count(*) AS n FROM channel_group_route_heads WHERE "
                        "active_channel_turn_id IS NOT NULL"
                    ),
                )
                if count and count["n"] >= 32:
                    raise ChannelBusyError("group work capacity reached")
            transition = await self._cancel_tx(db, route.route_id, stamp)
            await db.execute(
                (
                    "INSERT INTO "
                    "channel_turns(channel_turn_id,connection_id,binding_id,exter"
                    "nal_message_id,content_sha256,account_key,conversation_key,c"
                    "hat_type,sender_key,principal_scope,session_id,turn_id,gener"
                    "ation_id,status,revision,accepted_at,created_at,updated_at,i"
                    "nput_kind,group_lineage_version,group_route_id,group_route_r"
                    "evision) "
                    "VALUES(?,?,?,?,?,?,?,'group',?,?,?,?,?,'accepted',0,?,?,?,?,1,?,?)"
                ),
                (
                    str(admission.channel_turn_id),
                    str(message.connection_id),
                    str(binding_id),
                    message.external_message_id,
                    message.content_sha256,
                    message.account_key,
                    f"group:{message.group_id}",
                    message.sender_key,
                    f"scene:{route.scene_id}",
                    str(admission.session_id),
                    str(admission.turn_id),
                    str(admission.generation_id),
                    stamp.isoformat(),
                    stamp.isoformat(),
                    stamp.isoformat(),
                    "image" if message.image_fingerprint is not None else "text",
                    str(route.route_id),
                    route.revision,
                ),
            )
            await db.execute(
                (
                    "UPDATE channel_group_route_heads SET "
                    "latest_channel_turn_id=?,active_channel_turn_id=?,pending_ch"
                    "annel_turn_id=?,updated_at=? WHERE route_id=?"
                ),
                (
                    str(admission.channel_turn_id),
                    active or str(admission.channel_turn_id),
                    str(admission.channel_turn_id) if active else None,
                    stamp.isoformat(),
                    str(route.route_id),
                ),
            )
            result = await self._admission_result_tx(
                db,
                admission.channel_turn_id,
                duplicate=False,
                displaced=transition.displaced_turn_ids,
                dispatch=active is None,
            )
            return replace(result, persisted_events=transition.persisted_events)

    async def begin_group_turn(
        self, lineage: ChannelGroupRouteLineage, *, updated_at: datetime
    ) -> bool:
        stamp = _stamp(updated_at)
        async with self._database.transaction() as db:
            if not (await self._authorization_tx(db, lineage)).allowed:
                return False
            head = await _one(
                db,
                ("SELECT active_channel_turn_id FROM channel_group_route_heads WHERE route_id=?"),
                (str(lineage.route_id),),
            )
            if head is None or head["active_channel_turn_id"] != str(lineage.channel_turn_id):
                return False
            row = await _one(
                db,
                "SELECT status FROM channel_turns WHERE channel_turn_id=?",
                (str(lineage.channel_turn_id),),
            )
            if row is None or row["status"] != "accepted":
                return False
            await db.execute(
                (
                    "UPDATE channel_turns SET "
                    "status='processing',revision=revision+1,updated_at=? WHERE "
                    "channel_turn_id=?"
                ),
                (stamp.isoformat(), str(lineage.channel_turn_id)),
            )
            return True

    async def release_group_active(
        self, route_id: UUID, channel_turn_id: UUID, *, updated_at: datetime
    ) -> UUID | None:
        stamp = _stamp(updated_at)
        async with self._database.transaction() as db:
            head = await _one(
                db, "SELECT * FROM channel_group_route_heads WHERE route_id=?", (str(route_id),)
            )
            if head is None or head["active_channel_turn_id"] != str(channel_turn_id):
                return None
            await db.execute(
                (
                    "UPDATE channel_turns SET "
                    "status='cancelled',revision=revision+1,updated_at=?,complete"
                    "d_at=COALESCE(completed_at,?) WHERE channel_turn_id=? AND "
                    "status='cancelling'"
                ),
                (stamp.isoformat(), stamp.isoformat(), str(channel_turn_id)),
            )
            next_id = head["pending_channel_turn_id"]
            if next_id is not None:
                pending = await self._admission_result_tx(db, UUID(next_id), duplicate=False)
                if not (await self._authorization_tx(db, pending.lineage)).allowed:
                    next_id = None
            await db.execute(
                (
                    "UPDATE channel_group_route_heads SET "
                    "active_channel_turn_id=?,pending_channel_turn_id=NULL,update"
                    "d_at=? WHERE route_id=?"
                ),
                (next_id, stamp.isoformat(), str(route_id)),
            )
            return UUID(next_id) if next_id else None

    async def _event_tx(
        self,
        db: aiosqlite.Connection,
        turn: aiosqlite.Row,
        event_type: str,
        stamp: datetime,
        payload: dict[str, str | int | None],
    ) -> tuple[GenericCoreEvent, ...]:
        if self._events is None:
            return ()
        event = GenericCoreEvent.model_validate(
            dict(
                event_id=uuid4(),
                event_type=event_type,
                session_id=UUID(turn["session_id"]),
                turn_id=UUID(turn["turn_id"]),
                generation_id=UUID(turn["generation_id"]),
                occurred_at=stamp,
                source="runtime.external_channels",
                privacy=PrivacyLevel.PRIVATE,
                payload=payload,
            )
        )
        return (await self._events.append_in_transaction(db, event),)

    async def create_group_plan(
        self,
        lineage: ChannelGroupRouteLineage,
        *,
        reply_text: str,
        delivery_id: UUID,
        completed_at: datetime,
        parts: tuple[ChannelDeliveryPartDraft, ...] | None = None,
    ) -> ChannelGroupPlanResult:
        if type(reply_text) is not str or not reply_text.strip() or len(reply_text) > 20000:
            raise ValueError("group reply must be nonempty and bounded")
        draft_parts = parts if parts is not None else _single_part_draft(reply_text)
        _validate_delivery_part_drafts(draft_parts)
        if not group_text_parts_match_reply(draft_parts, reply_text, allow_sticker=True) or any(
            part.not_before_at is not None for part in draft_parts
        ):
            raise ValueError(
                "group parts require ordered complete text and receipt-based scheduling"
            )
        stamp = _stamp(completed_at)
        async with self._database.transaction() as db:
            auth = await self._authorization_tx(db, lineage)
            if not auth.allowed:
                raise ChannelPolicyError(auth.reason)
            route = await self._route_tx(db, lineage.route_id)
            turn = await _one(
                db,
                "SELECT * FROM channel_turns WHERE channel_turn_id=?",
                (str(lineage.channel_turn_id),),
            )
            assert turn is not None
            if isinstance(draft_parts[-1].payload, ChannelImageDeliveryPartPayload):
                image = draft_parts[-1].payload
                asset = await _one(
                    db,
                    "SELECT 1 FROM learned_stickers WHERE principal_scope=? AND character_id=? "
                    "AND sticker_id=? AND sha256=? AND mime_type=?",
                    (
                        f"scene:{route.scene_id}",
                        route.character_id,
                        image.sticker_id,
                        image.sha256,
                        image.mime_type,
                    ),
                )
                if asset is None:
                    raise ChannelPolicyError("Group image must belong to the current scene")
            existing = await _one(
                db,
                (
                    "SELECT delivery_id,group_target_json FROM "
                    "channel_deliveries WHERE channel_turn_id=?"
                ),
                (str(lineage.channel_turn_id),),
            )
            if existing:
                if turn["reply_text"] != reply_text:
                    raise ChannelConflictError("group plan reply differs from its fixed generation")
                return ChannelGroupPlanResult(
                    UUID(existing["delivery_id"]),
                    ChannelGroupDeliveryTarget.model_validate_json(existing["group_target_json"]),
                )
            if turn["status"] != "processing":
                raise ChannelPolicyError("group plan requires a processing turn")
            generation = await _one(
                db, "SELECT * FROM generations WHERE generation_id=?", (turn["generation_id"],)
            )
            if (
                generation is None
                or generation["session_id"] != turn["session_id"]
                or generation["turn_id"] != turn["turn_id"]
                or generation["state"] != "completed"
                or generation["invalidated_at"] is not None
                or generation["output_text"] != reply_text
            ):
                raise ChannelPolicyError("group reply requires the matching completed generation")
            target = ChannelGroupDeliveryTarget(
                connection_id=route.connection_id,
                account_key=route.account_key,
                group_id=route.group_id,
                route_id=route.route_id,
                route_revision=route.revision,
                channel_turn_id=lineage.channel_turn_id,
                scene_id=route.scene_id,
                audience_fingerprint=route.audience_fingerprint,
            )
            await db.execute(
                (
                    "INSERT INTO "
                    "channel_deliveries(delivery_id,channel_turn_id,connection_id"
                    ",binding_id,status,attempt,created_at,updated_at,group_targe"
                    "t_json,group_route_id,group_route_revision) "
                    "VALUES(?,?,?,?,'pending',1,?,?,?,?,?)"
                ),
                (
                    str(delivery_id),
                    str(lineage.channel_turn_id),
                    str(route.connection_id),
                    str(lineage.binding_id),
                    stamp.isoformat(),
                    stamp.isoformat(),
                    target.model_dump_json(),
                    str(route.route_id),
                    route.revision,
                ),
            )
            for part in draft_parts:
                await db.execute(
                    (
                        "INSERT INTO "
                        "channel_delivery_parts(part_id,delivery_id,ordinal,kind,payl"
                        "oad_json,required,status,delay_after_ms,attempt,provider_cli"
                        "ent_id,created_at,updated_at) "
                        "VALUES(?,?,?,?,?,?,'pending',?,0,?,?,?)"
                    ),
                    (
                        str(uuid4()),
                        str(delivery_id),
                        part.ordinal,
                        part.kind.value,
                        part.payload.model_dump_json(),
                        int(part.required),
                        part.delay_after_ms,
                        f"chatwaifu-{delivery_id.hex}-{part.ordinal:03d}",
                        stamp.isoformat(),
                        stamp.isoformat(),
                    ),
                )
            await db.execute(
                (
                    "UPDATE channel_turns SET "
                    "status='completed',reply_text=?,delivery_id=?,completed_at=?"
                    ",updated_at=?,revision=revision+1 WHERE channel_turn_id=?"
                ),
                (
                    reply_text,
                    str(delivery_id),
                    stamp.isoformat(),
                    stamp.isoformat(),
                    str(lineage.channel_turn_id),
                ),
            )
            events = await self._event_tx(
                db,
                turn,
                "channel.delivery_plan_created",
                stamp,
                dict(
                    connection_id=str(route.connection_id),
                    channel_turn_id=str(lineage.channel_turn_id),
                    delivery_id=str(delivery_id),
                    part_count=len(draft_parts),
                    chat_type="group",
                    conversation_key=f"group:{route.group_id}",
                    sender_key=turn["sender_key"],
                ),
            )
            return ChannelGroupPlanResult(delivery_id, target, events)

    async def cancel_group_turn(
        self, channel_turn_id: UUID, *, expected_revision: int, reason: str, updated_at: datetime
    ) -> ChannelGroupTransition:
        revision(expected_revision, 0)
        if type(reason) is not str or not reason.strip() or len(reason) > 128:
            raise ValueError("bounded cancellation reason required")
        stamp = _stamp(updated_at)
        async with self._database.transaction() as db:
            turn = await _one(
                db,
                ("SELECT * FROM channel_turns WHERE channel_turn_id=? AND group_lineage_version=1"),
                (str(channel_turn_id),),
            )
            if turn is None:
                raise ChannelNotFoundError("group turn unavailable")
            if turn["revision"] != expected_revision:
                raise ChannelConflictError("group turn revision changed")
            head = await _one(
                db,
                "SELECT * FROM channel_group_route_heads WHERE route_id=?",
                (turn["group_route_id"],),
            )
            assert head is not None
            # Targeted cancellation never cancels a newer member's latest input.
            if head["latest_channel_turn_id"] != str(channel_turn_id):
                return ChannelGroupTransition(
                    route=await self._route_tx(db, UUID(turn["group_route_id"]))
                )
            transition = await self._cancel_tx(db, UUID(turn["group_route_id"]), stamp)
            return replace(transition, route=await self._route_tx(db, UUID(turn["group_route_id"])))
