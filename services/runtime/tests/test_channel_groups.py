"""Real SQLite group fences; no provider, model, or server operations."""

from __future__ import annotations

import asyncio
import json
import sqlite3
from collections.abc import AsyncIterator
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import cast
from uuid import UUID, uuid4

import pytest
from chatwaifu_protocol.channel_groups import ChannelGroupPauseReason
from chatwaifu_protocol.channels import (
    ChannelChatType,
    ChannelConnectionConfiguration,
    ChannelConnectionStatus,
    ChannelDeliveryPartClaimRequest,
)
from chatwaifu_runtime.config.settings import StorageConfig
from chatwaifu_runtime.eventing.hub import EventHub
from chatwaifu_runtime.external_channels.group_models import (
    ChannelGroupAdmission,
    ChannelGroupAudienceObservation,
    ChannelGroupInboundDescriptor,
    ChannelGroupRouteMember,
    ChannelGroupRouteRecord,
    audience_fingerprint,
)
from chatwaifu_runtime.external_channels.service import (
    ChannelBusyError,
    ChannelConflictError,
    ChannelPolicyError,
)
from chatwaifu_runtime.persistence.database import Database
from chatwaifu_runtime.persistence.event_store import EventStore
from chatwaifu_runtime.persistence.migrations import MIGRATIONS
from chatwaifu_runtime.persistence.sqlite_channel_groups import SQLiteChannelGroupRepository
from chatwaifu_runtime.persistence.sqlite_external_channels import SQLiteExternalChannelRepository
from chatwaifu_runtime.sessions.service import SessionService

NOW = datetime.now(UTC)


@dataclass
class Group:
    database: Database
    repository: SQLiteChannelGroupRepository
    connection_id: UUID
    route: ChannelGroupRouteRecord
    sessions: dict[str, UUID]

    def admission(
        self, sender: str, raw_id: str, *, text: str = "fixture"
    ) -> ChannelGroupAdmission:
        return ChannelGroupAdmission(
            message=ChannelGroupInboundDescriptor(
                self.connection_id, "900", self.route.group_id, sender, raw_id, text, NOW
            ),
            route_id=self.route.route_id,
            expected_route_revision=self.route.revision,
            session_id=self.sessions[sender],
            channel_turn_id=uuid4(),
            turn_id=uuid4(),
            generation_id=uuid4(),
            admitted_at=NOW,
        )


async def _open(path: Path, *, through: int = 40) -> Database:
    db = Database(
        path,
        StorageConfig(database_path=path),
        migrations=tuple(m for m in MIGRATIONS if m[0] <= through),
    )
    await db.open()
    return db


async def _group(db: Database, *, group_id: str = "500", existing: Group | None = None) -> Group:
    events = EventStore(db)
    deliveries = SQLiteExternalChannelRepository(db, events)
    repository = SQLiteChannelGroupRepository(db, events)
    sessions = SessionService(db, events, EventHub())
    if existing is None:
        connection_id = uuid4()
        await deliveries.create_connection(
            ChannelConnectionConfiguration(
                connection_id=connection_id,
                provider_id="qq_napcat",
                name="fixture",
                character_id="default",
                principal_scope="local",
                account_key="900",
                allowed_sender_keys=["999"],
                enabled=True,
            ),
            access_token_hash="fixture",
            created_at=NOW,
        )
        await deliveries.touch_connection(
            connection_id, status=ChannelConnectionStatus.READY, seen_at=NOW
        )
        participants = [
            (await sessions.create_participant(name)).participant_id for name in ("A", "B")
        ]
    else:
        connection_id = existing.connection_id
        participants = [m.participant_id for m in existing.route.members]
    observation = ChannelGroupAudienceObservation(
        uuid4(), connection_id, 1, "900", group_id, ("111", "222"), NOW, NOW + timedelta(seconds=60)
    )
    await repository.create_observation(observation)
    links = [
        await repository.create_link(
            observation.observation_id, sender, participant, created_at=NOW
        )
        for sender, participant in zip(("111", "222"), participants, strict=True)
    ]
    scene = await sessions.create_scene("new fixture scene", participants)
    members = tuple(
        ChannelGroupRouteMember(link.link_id, link.sender_key, link.participant_id, True)
        for link in links
    )
    route = ChannelGroupRouteRecord(
        uuid4(),
        connection_id,
        "900",
        group_id,
        "default",
        scene.scene_id,
        "fixture group",
        1,
        False,
        None,
        observation.observation_id,
        members,
        NOW,
        NOW,
    )
    await repository.create_route(route)
    updated = await repository.update_route(
        route.route_id,
        expected_revision=1,
        enabled=True,
        observation_id=observation.observation_id,
        members=members,
        scene_id=scene.scene_id,
        updated_at=NOW,
    )
    assert updated.route is not None
    member_sessions = {
        link.sender_key: (
            await sessions.create_session(
                "default", participant_id=link.participant_id, scene_id=scene.scene_id
            )
        ).session_id
        for link in links
    }
    return Group(db, repository, connection_id, updated.route, member_sessions)


@pytest.fixture
async def group(tmp_path: Path) -> AsyncIterator[Group]:
    db = await _open(tmp_path / "groups.db")
    try:
        yield await _group(db)
    finally:
        await db.close()


async def _generation(group: Group, admission: ChannelGroupAdmission, text: str = "reply") -> None:
    await group.database.execute(
        "INSERT INTO turns(turn_id,session_id,role,created_at) VALUES(?,?,'user',?)",
        (str(admission.turn_id), str(admission.session_id), NOW.isoformat()),
    )
    await group.database.execute(
        "INSERT INTO generations(generation_id,session_id,turn_id,state,backend_kind,"
        "output_text,completed_at) VALUES(?,?,?,'completed','fixture',?,?)",
        (
            str(admission.generation_id),
            str(admission.session_id),
            str(admission.turn_id),
            text,
            NOW.isoformat(),
        ),
    )


async def test_members_have_distinct_binding_session_and_stable_raw_dedup(group: Group) -> None:
    first = group.admission("111", "-1")
    a = await group.repository.admit_group_turn(first)
    assert a.dispatch_now and not a.duplicate
    assert (
        a.turn.external_message_id == "-1"
        and a.turn.principal_scope == f"scene:{group.route.scene_id}"
    )
    duplicate = await group.repository.admit_group_turn(replace(first, channel_turn_id=uuid4()))
    assert duplicate.duplicate and not duplicate.dispatch_now
    b = await group.repository.admit_group_turn(group.admission("222", "2"))
    assert b.binding.binding_id != a.binding.binding_id and b.turn.session_id != a.turn.session_id
    assert not b.dispatch_now
    assert (await group.repository.authorize_group_turn(a.lineage)).reason == "superseded"
    duplicate = await group.repository.admit_group_turn(first)
    assert duplicate.duplicate
    assert (await group.repository.authorize_group_turn(b.lineage)).allowed
    with pytest.raises(ChannelConflictError):
        await group.repository.admit_group_turn(
            replace(first, message=replace(first.message, text="changed"))
        )


async def test_cross_group_same_raw_id_is_not_duplicate(group: Group) -> None:
    other = await _group(group.database, group_id="501", existing=group)
    a = await group.repository.admit_group_turn(group.admission("111", "77"))
    b = await other.repository.admit_group_turn(other.admission("111", "77"))
    assert a.turn.channel_turn_id != b.turn.channel_turn_id
    assert a.dispatch_now and b.dispatch_now and not b.duplicate


async def test_route_latest_pending_replaces_cross_member_and_release_is_cas(group: Group) -> None:
    a = await group.repository.admit_group_turn(group.admission("111", "1"))
    assert await group.repository.begin_group_turn(a.lineage, updated_at=NOW)
    b = await group.repository.admit_group_turn(group.admission("222", "2"))
    c = await group.repository.admit_group_turn(group.admission("111", "3"))
    assert b.turn.channel_turn_id in c.displaced_turn_ids
    assert not await group.repository.begin_group_turn(c.lineage, updated_at=NOW)
    assert (
        await group.repository.release_group_active(
            group.route.route_id, b.turn.channel_turn_id, updated_at=NOW
        )
        is None
    )
    assert (
        await group.repository.release_group_active(
            group.route.route_id, a.turn.channel_turn_id, updated_at=NOW
        )
        == c.turn.channel_turn_id
    )
    assert await group.repository.begin_group_turn(c.lineage, updated_at=NOW)
    old = await group.database.fetchone(
        "SELECT status FROM channel_turns WHERE channel_turn_id=?", (str(b.turn.channel_turn_id),)
    )
    assert old is not None and old["status"] == "cancelled"


async def test_concurrent_admission_serializes_and_only_one_pending(group: Group) -> None:
    admissions = [group.admission("111" if i % 2 else "222", str(i)) for i in range(1, 12)]
    results = await asyncio.gather(*(group.repository.admit_group_turn(a) for a in admissions))
    assert sum(r.dispatch_now for r in results) == 1
    heads = await group.database.fetchone(
        "SELECT * FROM channel_group_route_heads WHERE route_id=?", (str(group.route.route_id),)
    )
    assert heads is not None and heads["active_channel_turn_id"] != heads["pending_channel_turn_id"]
    assert heads["latest_channel_turn_id"] == heads["pending_channel_turn_id"]
    pending = await group.database.fetchall(
        "SELECT status FROM channel_turns WHERE group_route_id=? AND status='accepted'",
        (str(group.route.route_id),),
    )
    assert len(pending) == 1


@pytest.mark.parametrize("reason", list(ChannelGroupPauseReason))
async def test_all_revocations_cover_durable_unregistered_admission(
    group: Group, reason: ChannelGroupPauseReason
) -> None:
    admitted = await group.repository.admit_group_turn(group.admission("111", "1"))
    transitions = await group.repository.pause_routes(
        reason=reason, updated_at=NOW, connection_id=group.connection_id
    )
    assert admitted.turn.channel_turn_id in transitions[0].displaced_turn_ids
    assert not (await group.repository.authorize_group_turn(admitted.lineage)).allowed
    assert not await group.repository.begin_group_turn(admitted.lineage, updated_at=NOW)
    assert (
        await group.repository.release_group_active(
            group.route.route_id, admitted.turn.channel_turn_id, updated_at=NOW
        )
        is None
    )
    route = await group.repository.get_route(group.route.route_id)
    assert route is not None and not route.enabled


async def test_link_revocation_pauses_every_referencing_route(group: Group) -> None:
    other = await _group(group.database, group_id="501", existing=group)
    a = await group.repository.admit_group_turn(group.admission("111", "1"))
    b = await other.repository.admit_group_turn(other.admission("222", "1"))
    transition = await group.repository.update_link(
        group.route.members[0].link_id, enabled=False, expected_revision=1, updated_at=NOW
    )
    assert set(transition.displaced_turn_ids) == {a.turn.channel_turn_id, b.turn.channel_turn_id}
    assert not (await group.repository.authorize_group_turn(a.lineage)).allowed
    assert not (await other.repository.authorize_group_turn(b.lineage)).allowed
    with pytest.raises(ChannelConflictError):
        await group.repository.update_link(
            group.route.members[0].link_id, enabled=True, expected_revision=1, updated_at=NOW
        )


async def test_session_identity_cannot_be_supplied_or_reused_across_member(group: Group) -> None:
    with pytest.raises(ChannelPolicyError, match="persisted"):
        await group.repository.admit_group_turn(
            replace(group.admission("111", "1"), session_id=group.sessions["222"])
        )
    await group.database.execute(
        "UPDATE sessions SET state='closed' WHERE session_id=?", (str(group.sessions["111"]),)
    )
    with pytest.raises(ChannelPolicyError):
        await group.repository.admit_group_turn(group.admission("111", "1"))


async def test_plan_is_fixed_single_text_and_requires_canonical_generation(group: Group) -> None:
    admission = group.admission("111", "1")
    a = await group.repository.admit_group_turn(admission)
    assert await group.repository.begin_group_turn(a.lineage, updated_at=NOW)
    await _generation(group, admission)
    with pytest.raises(ChannelPolicyError):
        await group.repository.create_group_plan(
            a.lineage, reply_text="altered", delivery_id=uuid4(), completed_at=NOW
        )
    plan = await group.repository.create_group_plan(
        a.lineage, reply_text="reply", delivery_id=uuid4(), completed_at=NOW
    )
    assert (
        plan.target.group_id == group.route.group_id
        and plan.target.route_revision == group.route.revision
    )
    parts = await group.database.fetchall(
        "SELECT * FROM channel_delivery_parts WHERE delivery_id=?", (str(plan.delivery_id),)
    )
    assert len(parts) == 1 and parts[0]["kind"] == "text" and parts[0]["ordinal"] == 0
    assert plan.persisted_events[0].payload["chat_type"] == "group"
    with pytest.raises(sqlite3.IntegrityError, match="immutable"):
        await group.database.execute(
            "UPDATE channel_deliveries SET group_target_json='{}' WHERE delivery_id=?",
            (str(plan.delivery_id),),
        )
    with pytest.raises(sqlite3.IntegrityError, match="one immediate text"):
        await group.database.execute(
            "INSERT INTO channel_delivery_parts(part_id,delivery_id,ordinal,kind,payload_json,"
            "required,status,delay_after_ms,attempt,provider_client_id,created_at,updated_at) "
            "VALUES(?,?,1,'audio','{}',1,'pending',0,0,?,?,?)",
            (str(uuid4()), str(plan.delivery_id), str(uuid4()), NOW.isoformat(), NOW.isoformat()),
        )
    with pytest.raises(sqlite3.IntegrityError, match="content is immutable"):
        await group.database.execute(
            "UPDATE channel_delivery_parts SET payload_json='{}' WHERE delivery_id=?",
            (str(plan.delivery_id),),
        )
    repeated = await group.repository.create_group_plan(
        a.lineage, reply_text="reply", delivery_id=uuid4(), completed_at=NOW
    )
    assert repeated.delivery_id == plan.delivery_id and repeated.target == plan.target
    with pytest.raises(ChannelConflictError, match="fixed generation"):
        await group.repository.create_group_plan(
            a.lineage, reply_text="altered", delivery_id=uuid4(), completed_at=NOW
        )
    deliveries = SQLiteExternalChannelRepository(group.database, EventStore(group.database))
    readback = await deliveries.get_delivery_plan(plan.delivery_id)
    assert readback is not None and readback.group_target == plan.target
    assert a.binding.chat_type.value == "group" and a.binding.group_route_id == group.route.route_id
    assert (
        a.binding.scene_id == group.route.scene_id
        and a.binding.link_id == group.route.members[0].link_id
    )
    assert a.turn.group_lineage_version == 1 and a.turn.group_route_id == group.route.route_id
    assert a.turn.group_route_revision == group.route.revision
    assert (
        await group.repository.find_group_binding(group.route.route_id, group.route.scene_id, "111")
        == a.binding
    )
    with pytest.raises(sqlite3.IntegrityError, match="content is immutable"):
        await group.database.execute(
            "UPDATE channel_delivery_parts SET not_before_at=? WHERE delivery_id=?",
            (NOW.isoformat(), str(plan.delivery_id)),
        )


async def test_plan_loses_to_route_change_and_known_receipt_survives_revoke(group: Group) -> None:
    admission = group.admission("111", "1")
    a = await group.repository.admit_group_turn(admission)
    await group.repository.begin_group_turn(a.lineage, updated_at=NOW)
    await _generation(group, admission)
    plan = await group.repository.create_group_plan(
        a.lineage, reply_text="reply", delivery_id=uuid4(), completed_at=NOW
    )
    deliveries = SQLiteExternalChannelRepository(group.database, EventStore(group.database))
    claimed = await deliveries.claim_next_delivery_part(
        ChannelDeliveryPartClaimRequest(delivery_id=plan.delivery_id, lease_id=uuid4()),
        claimed_at=NOW,
    )
    assert claimed is not None and claimed.part is not None
    await group.repository.pause_routes(
        reason=ChannelGroupPauseReason.CONNECTION_DELETED,
        updated_at=NOW,
        connection_id=group.connection_id,
    )
    with pytest.raises(ChannelPolicyError):
        await group.repository.create_group_plan(
            a.lineage, reply_text="reply", delivery_id=uuid4(), completed_at=NOW
        )
    result = await deliveries.reconcile_known_delivery_part_receipt(
        group.connection_id, claimed.part.provider_client_id, "-77", observed_at=NOW
    )
    assert result.part is not None and result.part.provider_message_id == "-77"
    assert result.plan.status.value == "delivered"
    with pytest.raises(ValueError, match="conflicts"):
        await deliveries.reconcile_known_delivery_part_receipt(
            group.connection_id, claimed.part.provider_client_id, "-78", observed_at=NOW
        )


async def test_expired_observation_does_not_expire_enabled_route(group: Group) -> None:
    a = await group.repository.admit_group_turn(group.admission("111", "1"))
    assert (await group.repository.authorize_group_turn(a.lineage)).allowed
    with pytest.raises(ChannelPolicyError, match="fresh"):
        await group.repository.update_route(
            group.route.route_id,
            expected_revision=group.route.revision,
            enabled=True,
            observation_id=group.route.observation_id,
            members=group.route.members,
            scene_id=group.route.scene_id,
            updated_at=NOW + timedelta(minutes=2),
        )


async def test_route_versions_and_link_identity_immutable_and_cas(group: Group) -> None:
    with pytest.raises(sqlite3.IntegrityError, match="immutable"):
        await group.database.execute(
            "UPDATE channel_group_route_versions SET enabled=1 WHERE route_id=?",
            (str(group.route.route_id),),
        )
    with pytest.raises(sqlite3.IntegrityError, match="immutable"):
        await group.database.execute(
            "DELETE FROM channel_group_route_versions WHERE route_id=?",
            (str(group.route.route_id),),
        )
    with pytest.raises(sqlite3.IntegrityError, match="immutable"):
        await group.database.execute(
            "UPDATE channel_participant_links SET participant_id='local' WHERE link_id=?",
            (str(group.route.members[0].link_id),),
        )
    with pytest.raises(ChannelConflictError):
        await group.repository.update_route(
            group.route.route_id,
            expected_revision=1,
            enabled=False,
            observation_id=None,
            members=group.route.members,
            scene_id=group.route.scene_id,
            updated_at=NOW,
        )


@pytest.mark.parametrize("bad", [True, 1.0, "1"])
async def test_repository_strict_revision_inputs(group: Group, bad: object) -> None:
    with pytest.raises(ValueError, match="integer"):
        await group.repository.update_route(
            group.route.route_id,
            expected_revision=cast(int, bad),
            enabled=False,
            observation_id=None,
            members=group.route.members,
            scene_id=group.route.scene_id,
            updated_at=NOW,
        )


def test_fingerprint_includes_full_mapping_but_not_speaking_grants() -> None:
    members = (
        ChannelGroupRouteMember(uuid4(), "111", "a", True),
        ChannelGroupRouteMember(uuid4(), "222", "b", False),
    )
    assert audience_fingerprint(members) == audience_fingerprint(tuple(reversed(members)))
    assert audience_fingerprint(members) == audience_fingerprint(
        (replace(members[0], can_speak=False), members[1])
    )
    assert audience_fingerprint(members) != audience_fingerprint(
        (replace(members[0], participant_id="c"), members[1])
    )


async def test_head_foreign_keys_reject_cross_route_turn(group: Group) -> None:
    other = await _group(group.database, group_id="501", existing=group)
    a = await group.repository.admit_group_turn(group.admission("111", "1"))
    for field in ("latest_channel_turn_id", "active_channel_turn_id", "pending_channel_turn_id"):
        with pytest.raises(sqlite3.IntegrityError, match="FOREIGN KEY"):
            await group.database.execute(
                f"UPDATE channel_group_route_heads SET {field}=? WHERE route_id=?",
                (str(a.turn.channel_turn_id), str(other.route.route_id)),
            )


async def test_global32_capacity_has_no_partial_binding_or_turn_and_keeps_one_pending(
    group: Group,
) -> None:
    groups = [group] + [
        await _group(group.database, group_id=str(501 + i), existing=group) for i in range(32)
    ]
    for current in groups[:32]:
        assert (
            await current.repository.admit_group_turn(current.admission("111", "1"))
        ).dispatch_now
    rejected = groups[32].admission("111", "1")
    before = await group.database.fetchall("SELECT * FROM channel_bindings ORDER BY binding_id")
    with pytest.raises(ChannelBusyError):
        await groups[32].repository.admit_group_turn(rejected)
    assert (
        await group.database.fetchall("SELECT * FROM channel_bindings ORDER BY binding_id")
        == before
    )
    assert await group.repository.get_group_turn(rejected.channel_turn_id) is None
    replacement = await group.repository.admit_group_turn(group.admission("222", "2"))
    assert not replacement.dispatch_now
    assert (
        await group.database.fetchone(
            "SELECT count(*) FROM channel_group_route_heads "
            "WHERE active_channel_turn_id IS NOT NULL"
        )
    )[0] == 32  # type: ignore[index]


async def test_changed_audience_needs_new_unused_scene_and_preserves_old_binding(
    group: Group,
) -> None:
    first = await group.repository.admit_group_turn(group.admission("111", "1"))
    sessions = SessionService(group.database, EventStore(group.database), EventHub())
    participant = await sessions.create_participant("C")
    observation = ChannelGroupAudienceObservation(
        uuid4(),
        group.connection_id,
        1,
        "900",
        "500",
        ("111", "333"),
        NOW,
        NOW + timedelta(seconds=60),
    )
    await group.repository.create_observation(observation)
    link = await group.repository.create_link(
        observation.observation_id, "333", participant.participant_id, created_at=NOW
    )
    members = (
        group.route.members[0],
        ChannelGroupRouteMember(link.link_id, "333", link.participant_id, True),
    )
    with pytest.raises(ChannelPolicyError, match="new scene"):
        await group.repository.update_route(
            group.route.route_id,
            expected_revision=group.route.revision,
            enabled=True,
            observation_id=observation.observation_id,
            members=members,
            scene_id=group.route.scene_id,
            updated_at=NOW,
        )
    new_scene = await sessions.create_scene("new audience", [m.participant_id for m in members])
    transition = await group.repository.update_route(
        group.route.route_id,
        expected_revision=group.route.revision,
        enabled=True,
        observation_id=observation.observation_id,
        members=members,
        scene_id=new_scene.scene_id,
        updated_at=NOW,
    )
    assert first.turn.channel_turn_id in transition.displaced_turn_ids
    assert not (await group.repository.authorize_group_turn(first.lineage)).allowed
    retained = await group.repository.get_group_turn(first.turn.channel_turn_id)
    assert retained is not None and retained.lineage.scene_id == group.route.scene_id
    assert (
        await group.repository.find_group_binding(group.route.route_id, group.route.scene_id, "111")
        == first.binding
    )
    assert transition.route is not None and transition.route.scene_id == new_scene.scene_id
    assert await group.repository.is_group_scene(group.route.scene_id)
    assert await group.repository.is_group_scene(new_scene.scene_id)


async def test_deleted_route_cannot_be_reused_and_history_pages_are_read_only(group: Group) -> None:
    admitted = [
        await group.repository.admit_group_turn(group.admission("111", str(i))) for i in range(1, 4)
    ]
    await group.repository.pause_routes(
        reason=ChannelGroupPauseReason.ROUTE_DELETED,
        updated_at=NOW,
        connection_id=group.connection_id,
    )
    before = await group.database.fetchall("SELECT * FROM channel_turns ORDER BY channel_turn_id")
    page, cursor = await group.repository.list_group_turns(group.route.route_id, limit=1)
    assert len(page) == 1 and cursor is not None and page[0].duplicate and not page[0].dispatch_now
    seen = {page[0].turn.channel_turn_id}
    while cursor is not None:
        page, cursor = await group.repository.list_group_turns(
            group.route.route_id, limit=1, cursor=cursor
        )
        seen.update(p.turn.channel_turn_id for p in page)
    assert seen == {r.turn.channel_turn_id for r in admitted}
    assert (
        await group.database.fetchall("SELECT * FROM channel_turns ORDER BY channel_turn_id")
        == before
    )
    found = await group.repository.find_group_turn(group.connection_id, "500", "1")
    assert found is not None and found.turn.channel_turn_id == admitted[0].turn.channel_turn_id
    assert await group.repository.get_group_turn(uuid4()) is None
    routes, route_cursor = await group.repository.list_routes(group.connection_id, limit=1)
    assert routes[0].deleted_at is not None and route_cursor is None
    links, link_cursor = await group.repository.list_links(group.connection_id, limit=1)
    assert len(links) == 1 and link_cursor is not None
    next_links, next_cursor = await group.repository.list_links(
        group.connection_id, limit=1, cursor=link_cursor
    )
    assert (
        len(next_links) == 1 and next_cursor is None and next_links[0].link_id != links[0].link_id
    )
    with pytest.raises(ValueError, match="cursor"):
        await group.repository.list_links(uuid4(), cursor=link_cursor)
    with pytest.raises(ValueError, match="cursor"):
        await group.repository.list_group_turns(uuid4(), cursor=link_cursor)
    with pytest.raises(ChannelPolicyError, match="deleted"):
        await group.repository.update_route(
            group.route.route_id,
            expected_revision=group.route.revision + 1,
            enabled=True,
            observation_id=group.route.observation_id,
            members=group.route.members,
            scene_id=group.route.scene_id,
            updated_at=NOW,
        )
    with pytest.raises(ChannelConflictError, match="group"):
        await group.repository.create_route(
            replace(group.route, route_id=uuid4(), enabled=False, revision=1)
        )


@pytest.mark.parametrize("limit", [True, 0, 51, 1.0, "1"])
async def test_history_limits_are_strict_and_bounded(group: Group, limit: object) -> None:
    with pytest.raises(ValueError, match="limit"):
        await group.repository.list_group_turns(group.route.route_id, limit=limit)  # type: ignore[arg-type]


async def test_new_scene_cannot_reuse_existing_sessions_and_mapping_is_not_replaceable(
    group: Group,
) -> None:
    with pytest.raises(ChannelConflictError, match="identity"):
        await group.repository.create_link(
            group.route.observation_id, "111", group.route.members[1].participant_id, created_at=NOW
        )
    observation = ChannelGroupAudienceObservation(
        uuid4(),
        group.connection_id,
        1,
        "900",
        "502",
        ("111", "222"),
        NOW,
        NOW + timedelta(seconds=60),
    )
    await group.repository.create_observation(observation)
    with pytest.raises(ChannelPolicyError, match="unused"):
        await group.repository.create_route(
            replace(
                group.route,
                route_id=uuid4(),
                group_id="502",
                enabled=False,
                revision=1,
                observation_id=observation.observation_id,
            )
        )


async def test_create_is_disabled_and_route_cas_serializes_revoke(group: Group) -> None:
    initial = await group.database.fetchone(
        "SELECT enabled FROM channel_group_route_versions WHERE route_id=? AND revision=1",
        (str(group.route.route_id),),
    )
    assert initial is not None and initial["enabled"] == 0
    admitted = await group.repository.admit_group_turn(group.admission("111", "1"))

    async def disable() -> object:
        try:
            return await group.repository.update_route(
                group.route.route_id,
                expected_revision=group.route.revision,
                enabled=False,
                observation_id=None,
                members=group.route.members,
                scene_id=group.route.scene_id,
                updated_at=NOW,
            )
        except ChannelConflictError as error:
            return error

    results = await asyncio.gather(disable(), disable())
    assert sum(isinstance(result, ChannelConflictError) for result in results) == 1
    assert not (await group.repository.authorize_group_turn(admitted.lineage)).allowed
    assert not await group.repository.begin_group_turn(admitted.lineage, updated_at=NOW)


async def test_completed_plan_is_displaced_and_pending_cancel_events_are_returned(
    group: Group,
) -> None:
    admission = group.admission("111", "1")
    first = await group.repository.admit_group_turn(admission)
    await group.repository.begin_group_turn(first.lineage, updated_at=NOW)
    await _generation(group, admission)
    plan = await group.repository.create_group_plan(
        first.lineage, reply_text="reply", delivery_id=uuid4(), completed_at=NOW
    )
    second = await group.repository.admit_group_turn(group.admission("222", "2"))
    assert first.turn.channel_turn_id in second.displaced_turn_ids and not second.dispatch_now
    third = await group.repository.admit_group_turn(group.admission("111", "3"))
    assert second.turn.channel_turn_id in third.displaced_turn_ids
    assert len(third.persisted_events) == 1
    event = third.persisted_events[0]
    assert (
        event.event_type == "channel.turn_cancelled" and event.session_id == second.turn.session_id
    )
    assert (
        await group.database.fetchone(
            "SELECT status FROM channel_deliveries WHERE delivery_id=?", (str(plan.delivery_id),)
        )
    )[0] == "cancelled"  # type: ignore[index]
    completed = await group.repository.get_group_turn(first.turn.channel_turn_id)
    assert completed is not None and completed.turn.status.value == "completed"
    assert (
        await group.repository.release_group_active(
            group.route.route_id, first.turn.channel_turn_id, updated_at=NOW
        )
        == third.turn.channel_turn_id
    )


async def test_targeted_cancel_of_old_turn_never_cancels_new_latest_and_cas_is_strict(
    group: Group,
) -> None:
    first = await group.repository.admit_group_turn(group.admission("111", "1"))
    second = await group.repository.admit_group_turn(group.admission("222", "2"))
    current_first = await group.repository.get_group_turn(first.turn.channel_turn_id)
    assert current_first is not None
    old_cancel = await group.repository.cancel_group_turn(
        first.turn.channel_turn_id,
        expected_revision=current_first.turn.revision,
        reason="operator_cancelled",
        updated_at=NOW,
    )
    assert old_cancel.displaced_turn_ids == ()
    assert (await group.repository.authorize_group_turn(second.lineage)).allowed
    with pytest.raises(ChannelConflictError):
        await group.repository.cancel_group_turn(
            second.turn.channel_turn_id,
            expected_revision=100,
            reason="operator_cancelled",
            updated_at=NOW,
        )
    canceled = await group.repository.cancel_group_turn(
        second.turn.channel_turn_id,
        expected_revision=second.turn.revision,
        reason="operator_cancelled",
        updated_at=NOW,
    )
    assert second.turn.channel_turn_id in canceled.displaced_turn_ids
    assert not (await group.repository.authorize_group_turn(second.lineage)).allowed


async def test_scene_ownership_precedes_any_binding_and_survives_soft_delete(group: Group) -> None:
    assert await group.database.fetchall("SELECT * FROM channel_bindings") == []
    assert await group.repository.is_group_scene(group.route.scene_id)
    sessions = SessionService(group.database, EventStore(group.database), EventHub())
    native_scene = await sessions.create_scene(
        "native", [m.participant_id for m in group.route.members]
    )
    assert not await group.repository.is_group_scene(native_scene.scene_id)
    await group.repository.pause_routes(
        reason=ChannelGroupPauseReason.ROUTE_DELETED,
        connection_id=group.connection_id,
        updated_at=NOW,
    )
    assert await group.repository.is_group_scene(group.route.scene_id)
    assert await group.database.fetchall("SELECT * FROM channel_bindings") == []


async def test_two_sqlite_handles_serialize_duplicate_and_cas(group: Group, tmp_path: Path) -> None:
    other_database = await _open(tmp_path / "groups.db")
    try:
        other = SQLiteChannelGroupRepository(other_database, EventStore(other_database))
        admission = group.admission("111", "1")
        redelivery = replace(
            admission,
            channel_turn_id=uuid4(),
            message=replace(admission.message, received_at=NOW + timedelta(seconds=1)),
        )
        results = await asyncio.gather(
            group.repository.admit_group_turn(admission), other.admit_group_turn(redelivery)
        )
        assert sum(result.duplicate for result in results) == 1
        assert sum(result.dispatch_now for result in results) == 1
        assert results[0].turn.channel_turn_id == results[1].turn.channel_turn_id
        assert len(await group.database.fetchall("SELECT * FROM channel_turns")) == 1

        async def disable(repository: SQLiteChannelGroupRepository) -> object:
            try:
                return await repository.update_route(
                    group.route.route_id,
                    expected_revision=group.route.revision,
                    enabled=False,
                    observation_id=None,
                    members=group.route.members,
                    scene_id=group.route.scene_id,
                    updated_at=NOW,
                )
            except ChannelConflictError as error:
                return error

        transitions = await asyncio.gather(disable(group.repository), disable(other))
        assert sum(isinstance(result, ChannelConflictError) for result in transitions) == 1
        assert not (await other.authorize_group_turn(results[0].lineage)).allowed
        assert await other_database.fetchall("PRAGMA foreign_key_check") == []
    finally:
        await other_database.close()


async def test_two_sqlite_handles_plan_and_revoke_never_leave_sendable_work(
    group: Group, tmp_path: Path
) -> None:
    other_database = await _open(tmp_path / "groups.db")
    try:
        other = SQLiteChannelGroupRepository(other_database, EventStore(other_database))
        admission = group.admission("111", "1")
        first = await group.repository.admit_group_turn(admission)
        await group.repository.begin_group_turn(first.lineage, updated_at=NOW)
        await _generation(group, admission)

        async def plan() -> object:
            try:
                return await group.repository.create_group_plan(
                    first.lineage, reply_text="reply", delivery_id=uuid4(), completed_at=NOW
                )
            except ChannelPolicyError as error:
                return error

        _, paused = await asyncio.gather(
            plan(),
            other.pause_routes(
                reason=ChannelGroupPauseReason.MEMBERSHIP_CHANGED,
                connection_id=group.connection_id,
                updated_at=NOW,
            ),
        )
        assert paused[0].route is not None and not paused[0].route.enabled
        assert not (await other.authorize_group_turn(first.lineage)).allowed
        assert (
            await other_database.fetchall(
                "SELECT * FROM channel_delivery_parts WHERE status IN ('pending','sending')"
            )
            == []
        )
        assert await other_database.fetchall("PRAGMA foreign_key_check") == []
    finally:
        await other_database.close()


async def test_legacy_private_lookup_never_reads_group_binding_or_raw_id(group: Group) -> None:
    admitted = await group.repository.admit_group_turn(group.admission("111", "1"))
    deliveries = SQLiteExternalChannelRepository(group.database, EventStore(group.database))
    assert await deliveries.find_binding(group.connection_id, "group:500") is None
    assert await deliveries.find_turn_by_external_message(group.connection_id, "1") is None
    sessions = SessionService(group.database, EventStore(group.database), EventHub())
    private_session = await sessions.create_session("default")
    private_binding = await deliveries.create_binding(
        binding_id=uuid4(),
        connection_id=group.connection_id,
        conversation_key="group:500",
        sender_key="999",
        session_id=private_session.session_id,
        created_at=NOW,
    )
    assert private_binding.chat_type is ChannelChatType.DIRECT
    private_turn = await deliveries.create_turn(
        replace(
            admitted.turn,
            channel_turn_id=uuid4(),
            binding_id=private_binding.binding_id,
            sender_key="999",
            chat_type=ChannelChatType.DIRECT,
            principal_scope="local",
            session_id=private_session.session_id,
            turn_id=uuid4(),
            generation_id=uuid4(),
            group_lineage_version=0,
            group_route_id=None,
            group_route_revision=None,
        )
    )
    assert await deliveries.find_binding(group.connection_id, "group:500") == private_binding
    assert await deliveries.find_turn_by_external_message(group.connection_id, "1") == private_turn
    group_result = await group.repository.find_group_turn(group.connection_id, "500", "1")
    assert (
        group_result is not None
        and group_result.turn.channel_turn_id == admitted.turn.channel_turn_id
    )
    assert group_result.binding.binding_id != private_binding.binding_id


async def test_group_target_and_unknown_send_fence_survive_reopen_then_known_receipt(
    group: Group, tmp_path: Path
) -> None:
    admission = group.admission("111", "1")
    accepted = await group.repository.admit_group_turn(admission)
    await group.repository.begin_group_turn(accepted.lineage, updated_at=NOW)
    await _generation(group, admission)
    plan = await group.repository.create_group_plan(
        accepted.lineage, reply_text="reply", delivery_id=uuid4(), completed_at=NOW
    )
    deliveries = SQLiteExternalChannelRepository(group.database, EventStore(group.database))
    claimed = await deliveries.claim_next_delivery_part(
        ChannelDeliveryPartClaimRequest(delivery_id=plan.delivery_id, lease_id=uuid4()),
        claimed_at=NOW,
    )
    assert claimed is not None and claimed.part is not None
    client_id = claimed.part.provider_client_id
    journal = json.dumps({client_id: "-77", "unknown-client": None})
    await deliveries.set_adapter_cursor(group.connection_id, cursor=journal, updated_at=NOW)
    await group.repository.pause_routes(
        reason=ChannelGroupPauseReason.RECONNECT, connection_id=group.connection_id, updated_at=NOW
    )
    await group.database.close()
    reopened = await _open(tmp_path / "groups.db")
    try:
        group_repository = SQLiteChannelGroupRepository(reopened, EventStore(reopened))
        deliveries = SQLiteExternalChannelRepository(reopened, EventStore(reopened))
        readback = await deliveries.get_delivery_plan(plan.delivery_id)
        assert readback is not None and readback.group_target == plan.target
        assert readback.parts[0].attempt == 1 and readback.parts[0].status.value == "cancelled"
        assert not (await group_repository.authorize_group_turn(accepted.lineage)).allowed
        assert await deliveries.get_adapter_cursor(group.connection_id) == journal
        assert await deliveries.retained_send_journal_keys(
            group.connection_id, [client_id, "unknown-client"]
        ) == frozenset({client_id, "unknown-client"})
        result = await deliveries.reconcile_known_delivery_part_receipt(
            group.connection_id, client_id, "-77", observed_at=NOW
        )
        assert result.part is not None and result.part.attempt == 1
        assert result.plan.group_target == plan.target and result.plan.status.value == "delivered"
        assert await deliveries.retained_send_journal_keys(
            group.connection_id, [client_id, "unknown-client"]
        ) == frozenset({"unknown-client"})
        assert await reopened.fetchall("PRAGMA foreign_key_check") == []
    finally:
        await reopened.close()


async def test_bulk_registration_rolls_back_all_participants_and_links_on_failure(
    group: Group,
) -> None:
    observation = ChannelGroupAudienceObservation(
        uuid4(),
        group.connection_id,
        1,
        "900",
        "501",
        ("333", "444"),
        NOW,
        NOW + timedelta(seconds=60),
    )
    await group.repository.create_observation(observation)
    before = await group.database.fetchall("SELECT * FROM participants")
    async with group.database.transaction() as db:
        await db.execute(
            "CREATE TRIGGER fixture_reject_registration BEFORE INSERT ON channel_participant_links "
            "WHEN NEW.sender_key='444' BEGIN SELECT RAISE(ABORT,'fixture failure'); END"
        )
    with pytest.raises(sqlite3.IntegrityError):
        await group.repository.register_audience(observation.observation_id, {}, created_at=NOW)
    assert await group.database.fetchall("SELECT * FROM participants") == before
    rows = await group.database.fetchall(
        "SELECT * FROM channel_participant_links WHERE sender_key IN ('333','444')"
    )
    assert not rows


async def test_concurrent_bulk_confirmation_reuses_each_identity(group: Group) -> None:
    observation = ChannelGroupAudienceObservation(
        uuid4(),
        group.connection_id,
        1,
        "900",
        "501",
        ("333", "444"),
        NOW,
        NOW + timedelta(seconds=60),
    )
    await group.repository.create_observation(observation)
    first, second = await asyncio.gather(
        group.repository.register_audience(
            observation.observation_id, {"333": "same", "444": "same"}, created_at=NOW
        ),
        group.repository.register_audience(observation.observation_id, {}, created_at=NOW),
    )
    assert sorted((first[2], second[2])) == [0, 2]
    assert first[0] == second[0]
    assert len({link.participant_id for link in first[0]}) == 2
