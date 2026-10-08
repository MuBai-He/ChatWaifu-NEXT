"""CAS policies and durable rolling participation counters."""

import json
from datetime import datetime, timedelta
from uuid import UUID

from chatwaifu_protocol.agent import DecisionRecord, GroupAutonomyPolicy

from chatwaifu_runtime.persistence.database import Database


class SQLiteBehaviorRepository:
    def __init__(self, database: Database) -> None:
        self.database = database

    async def get(self, route_id: UUID) -> GroupAutonomyPolicy | None:
        row = await self.database.fetchone(
            "SELECT policy_json FROM agent_group_policies WHERE route_id=?", (str(route_id),)
        )
        return GroupAutonomyPolicy.model_validate_json(row["policy_json"]) if row else None

    async def save(self, policy: GroupAutonomyPolicy, expected_revision: int) -> bool:
        if policy.revision != expected_revision + 1:
            raise ValueError("policy revision must advance once")
        async with self.database.transaction() as connection:
            if expected_revision == 0:
                result = await connection.execute(
                    "INSERT OR IGNORE INTO agent_group_policies(route_id,revision,policy_json) "
                    "VALUES(?,?,?)",
                    (str(policy.route_id), policy.revision, policy.model_dump_json()),
                )
            else:
                result = await connection.execute(
                    "UPDATE agent_group_policies SET revision=?,policy_json=? "
                    "WHERE route_id=? AND revision=?",
                    (
                        policy.revision,
                        policy.model_dump_json(),
                        str(policy.route_id),
                        expected_revision,
                    ),
                )
            return result.rowcount == 1

    async def reserve(self, policy: GroupAutonomyPolicy, now: datetime, *, speech: bool) -> bool:
        kind = "speech" if speech else "observation"
        limit = policy.messages_per_hour if speech else policy.observations_per_hour
        async with self.database.transaction() as connection:
            current = await (
                await connection.execute(
                    "SELECT policy_json FROM agent_group_policies WHERE route_id=?",
                    (str(policy.route_id),),
                )
            ).fetchone()
            if current is None or current["policy_json"] != policy.model_dump_json():
                return False
            await connection.execute(
                "DELETE FROM agent_behavior_reservations WHERE occurred_at<?",
                ((now - timedelta(hours=1)).isoformat(),),
            )
            row = await (
                await connection.execute(
                    "SELECT COUNT(*) AS count,MAX(occurred_at) AS last "
                    "FROM agent_behavior_reservations WHERE route_id=? AND kind=?",
                    (str(policy.route_id), kind),
                )
            ).fetchone()
            assert row is not None
            if row["count"] >= limit:
                return False
            interval = (
                policy.message_interval_seconds if speech else policy.decision_interval_seconds
            )
            if (
                row["last"]
                and (now - datetime.fromisoformat(row["last"])).total_seconds() < interval
            ):
                return False
            await connection.execute(
                "INSERT INTO agent_behavior_reservations(route_id,kind,occurred_at) VALUES(?,?,?)",
                (str(policy.route_id), kind, now.isoformat()),
            )
            return True

    async def next_observation(self, policy: GroupAutonomyPolicy, now: datetime) -> datetime:
        rows = await self.database.fetchall(
            "SELECT occurred_at FROM agent_behavior_reservations "
            "WHERE route_id=? AND kind='observation' AND occurred_at>=? ORDER BY occurred_at",
            (str(policy.route_id), (now - timedelta(hours=1)).isoformat()),
        )
        times = [datetime.fromisoformat(row["occurred_at"]) for row in rows]
        due = (
            max(now, times[-1] + timedelta(seconds=policy.decision_interval_seconds))
            if times
            else now
        )
        if len(times) >= policy.observations_per_hour:
            due = max(due, times[0] + timedelta(hours=1))
        return due

    async def quiet_until(self, route_id: UUID) -> datetime | None:
        row = await self.database.fetchone(
            "SELECT until_at FROM agent_group_quiet WHERE route_id=?", (str(route_id),)
        )
        return datetime.fromisoformat(row["until_at"]) if row else None

    async def set_quiet(self, route_id: UUID, until: datetime) -> None:
        await self.database.execute(
            "INSERT INTO agent_group_quiet(route_id,until_at) VALUES(?,?) "
            "ON CONFLICT(route_id) DO UPDATE SET until_at=excluded.until_at",
            (str(route_id), until.isoformat()),
        )

    async def mark_turn(self, turn_id: UUID, route_id: UUID, policy_revision: int) -> bool:
        async with self.database.transaction() as connection:
            row = await (
                await connection.execute(
                    "SELECT policy_json FROM agent_group_policies WHERE route_id=?",
                    (str(route_id),),
                )
            ).fetchone()
            if row is None:
                return False
            policy = GroupAutonomyPolicy.model_validate_json(row["policy_json"])
            if policy.mode != "member" or policy.revision != policy_revision:
                return False
            await connection.execute(
                "INSERT INTO agent_autonomous_turns(turn_id,route_id,policy_revision) "
                "VALUES(?,?,?)",
                (str(turn_id), str(route_id), policy_revision),
            )
            return True

    async def authorize_turn(self, turn_id: UUID) -> bool:
        row = await self.database.fetchone(
            "SELECT t.policy_revision,p.policy_json FROM agent_autonomous_turns t "
            "LEFT JOIN agent_group_policies p ON p.route_id=t.route_id WHERE t.turn_id=?",
            (str(turn_id),),
        )
        if row is None:
            return True  # Explicit requests retain their own admission grant.
        if row["policy_json"] is None:
            return False
        policy = GroupAutonomyPolicy.model_validate_json(row["policy_json"])
        return policy.mode == "member" and policy.revision == row["policy_revision"]

    async def record(
        self, policy: GroupAutonomyPolicy, decision: DecisionRecord, now: datetime
    ) -> None:
        async with self.database.transaction() as connection:
            await connection.execute(
                "INSERT INTO agent_decisions(route_id,policy_revision,occurred_at,decision_json) "
                "VALUES(?,?,?,?)",
                (
                    str(policy.route_id),
                    policy.revision,
                    now.isoformat(),
                    json.dumps(decision.model_dump(mode="json"), ensure_ascii=False),
                ),
            )
            await connection.execute(
                "DELETE FROM agent_decisions WHERE sequence NOT IN "
                "(SELECT sequence FROM agent_decisions ORDER BY sequence DESC LIMIT 2000)"
            )

    async def recent(self, route_id: UUID) -> list[DecisionRecord]:
        rows = await self.database.fetchall(
            "SELECT decision_json FROM agent_decisions WHERE route_id=? "
            "ORDER BY sequence DESC LIMIT 30",
            (str(route_id),),
        )
        return [DecisionRecord.model_validate_json(row["decision_json"]) for row in rows]
