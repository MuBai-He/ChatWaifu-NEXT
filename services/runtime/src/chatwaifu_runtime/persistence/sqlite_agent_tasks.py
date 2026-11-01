"""CAS task storage and write-ahead operation journal; separate from redacted audit."""

import json
from typing import cast
from uuid import UUID

from chatwaifu_protocol.agent import AgentEvent, AgentTask, AgentTaskPage
from chatwaifu_protocol.base import JsonObject

from chatwaifu_runtime.agent.action_claims import ActionAlreadyClaimed
from chatwaifu_runtime.persistence.database import Database


class SQLiteAgentTaskRepository:
    def __init__(self, database: Database) -> None:
        self._database = database

    async def create(self, task: AgentTask) -> None:
        async with self._database.transaction() as connection:
            await connection.execute(
                "INSERT INTO agent_tasks(task_id,scope,revision,state,payload_json) "
                "VALUES(?,?,?,?,?)",
                (
                    str(task.task_id),
                    task.scope,
                    task.revision,
                    task.state.value,
                    task.model_dump_json(),
                ),
            )

    async def get(self, task_id: UUID) -> AgentTask | None:
        row = await self._database.fetchone(
            "SELECT payload_json FROM agent_tasks WHERE task_id=?", (str(task_id),)
        )
        return AgentTask.model_validate_json(row["payload_json"]) if row is not None else None

    async def save(self, task: AgentTask, expected_revision: int) -> bool:
        if task.revision != expected_revision + 1:
            raise ValueError("task revision must advance exactly once")
        async with self._database.transaction() as connection:
            result = await connection.execute(
                "UPDATE agent_tasks SET revision=?,state=?,payload_json=? "
                "WHERE task_id=? AND revision=?",
                (
                    task.revision,
                    task.state.value,
                    task.model_dump_json(),
                    str(task.task_id),
                    expected_revision,
                ),
            )
            return result.rowcount == 1

    async def enqueue_event(self, event: AgentEvent) -> bool:
        async with self._database.transaction() as connection:
            await connection.execute(
                "DELETE FROM agent_events WHERE julianday("
                "json_extract(payload_json,'$.expires_at'))<julianday('now')",
            )
            count = await (
                await connection.execute("SELECT COUNT(*) FROM agent_events WHERE settled=0")
            ).fetchone()
            if count is not None and count[0] >= 256:
                raise ValueError("agent event capacity reached")
            total = await (await connection.execute("SELECT COUNT(*) FROM agent_events")).fetchone()
            if total is not None and total[0] >= 2000:
                raise ValueError("agent event replay ledger capacity reached")
            result = await connection.execute(
                "INSERT OR IGNORE INTO agent_events(event_id,task_id,payload_json) VALUES(?,?,?)",
                (
                    str(event.event_id),
                    str(event.task_id) if event.task_id else None,
                    event.model_dump_json(),
                ),
            )
            if result.rowcount == 0:
                old = await (
                    await connection.execute(
                        "SELECT payload_json FROM agent_events WHERE event_id=?",
                        (str(event.event_id),),
                    )
                ).fetchone()
                if old is not None and old["payload_json"] != event.model_dump_json():
                    raise ValueError("event identity has conflicting content")
            return result.rowcount == 1

    async def settle_event(self, event_id: UUID) -> None:
        await self._database.execute(
            "UPDATE agent_events SET settled=1 WHERE event_id=?", (str(event_id),)
        )

    async def pending_events(self) -> list[AgentEvent]:
        rows = await self._database.fetchall(
            "SELECT payload_json FROM agent_events WHERE settled=0 ORDER BY rowid LIMIT 256"
        )
        return [AgentEvent.model_validate_json(row["payload_json"]) for row in rows]

    async def page(self, scope: str | None, cursor: str | None = None) -> AgentTaskPage:
        if cursor is not None:
            UUID(cursor)
        rows = await self._database.fetchall(
            "SELECT payload_json FROM agent_tasks WHERE (? IS NULL OR scope=?) "
            "AND task_id>? ORDER BY task_id LIMIT 51",
            (scope, scope, cursor or ""),
        )
        items = [AgentTask.model_validate_json(row["payload_json"]) for row in rows[:50]]
        return AgentTaskPage(
            items=items, next_cursor=str(items[-1].task_id) if len(rows) > 50 else None
        )

    async def begin_step(self, task_id: UUID, key: str, record: JsonObject) -> bool:
        async with self._database.transaction() as connection:
            action = record.get("action_id")
            if (
                isinstance(action, str)
                and record.get("skill_id") == "agenda.manage"
                and record.get("side_effect") != "read"
            ):
                claims = list(
                    await connection.execute_fetchall(
                        "SELECT task_id,step_key FROM harness_action_claims WHERE action_id=?",
                        (action,),
                    )
                )
                if claims and (claims[0][0] != str(task_id) or claims[0][1] != key):
                    prior = list(
                        await connection.execute_fetchall(
                            (
                                "SELECT payload_json FROM agent_task_steps WHERE task_id=? AN"
                                "D step_key=?"
                            ),
                            (claims[0][0], claims[0][1]),
                        )
                    )
                    payload: JsonObject = json.loads(prior[0][0]) if prior else {}
                    if (
                        payload.get("state") == "settled"
                        and payload.get("ok") is False
                        and payload.get("effect_possible") is False
                    ):
                        await connection.execute(
                            (
                                "UPDATE harness_action_claims SET task_id=?,step_key=? WHERE "
                                "action_id=?"
                            ),
                            (str(task_id), key, action),
                        )
                    else:
                        raise ActionAlreadyClaimed(UUID(claims[0][0]))
                else:
                    await connection.execute(
                        "INSERT OR IGNORE INTO harness_action_claims VALUES(?,?,?)",
                        (action, str(task_id), key),
                    )

            metadata = record.get("verification")
            root = metadata.get("logical_work_id") if isinstance(metadata, dict) else None
            if isinstance(root, str) and record.get("side_effect") != "read":
                previous = list(
                    await connection.execute_fetchall(
                        "SELECT task_id,step_key FROM harness_work_claims WHERE work_item_id=?",
                        (root,),
                    )
                )
                if previous and (previous[0][0] != str(task_id) or previous[0][1] != key):
                    settled = list(
                        await connection.execute_fetchall(
                            (
                                "SELECT payload_json FROM agent_t"
                                "ask_steps WHERE task_id=? AND st"
                                "ep_key=?"
                            ),
                            (previous[0][0], previous[0][1]),
                        )
                    )
                    facts: JsonObject = json.loads(settled[0][0]) if settled else {}
                    if facts.get("state") != "settled":
                        raise ActionAlreadyClaimed(UUID(previous[0][0]))
                await connection.execute(
                    (
                        "INSERT INTO harness_work_claims "
                        "VALUES(?,?,?) ON CONFLICT(work_i"
                        "tem_id) DO UPDATE SET task_id=ex"
                        "cluded.task_id,step_key=excluded"
                        ".step_key"
                    ),
                    (root, str(task_id), key),
                )

            result = await connection.execute(
                "INSERT OR IGNORE INTO agent_task_steps(task_id,step_key,payload_json) "
                "VALUES(?,?,?)",
                (str(task_id), key, json.dumps(record, ensure_ascii=False, allow_nan=False)),
            )
            return result.rowcount == 1

    async def finish_step(self, task_id: UUID, key: str, record: JsonObject) -> None:
        async with self._database.transaction() as connection:
            rows = await connection.execute_fetchall(
                "SELECT payload_json FROM agent_tasks WHERE task_id=?", (str(task_id),)
            )
            current = list(rows)
            if current and json.loads(current[0][0]).get("blocked_reason") == "source_forgotten":
                record = {k: v for k, v in record.items() if k != "snapshot"}
                record["redacted"] = True
            await connection.execute(
                "UPDATE agent_task_steps SET payload_json=? WHERE task_id=? AND step_key=?",
                (json.dumps(record, ensure_ascii=False, allow_nan=False), str(task_id), key),
            )

    async def steps(self, task_id: UUID) -> list[JsonObject]:
        rows = await self._database.fetchall(
            "SELECT payload_json FROM agent_task_steps WHERE task_id=? ORDER BY sequence",
            (str(task_id),),
        )
        return [cast(JsonObject, json.loads(row["payload_json"])) for row in rows]
