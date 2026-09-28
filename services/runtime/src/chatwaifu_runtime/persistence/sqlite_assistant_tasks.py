"""Transactional scheduling, idempotent operations, and revocable device credentials."""

import hashlib
import hmac
import json
import secrets
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

import aiosqlite

from chatwaifu_runtime.persistence.database import Database
from chatwaifu_runtime.personal_assistant.repository import AssistantAccessError
from chatwaifu_runtime.personal_assistant.tasks import AppleOperation, TaskInput, next_due


class SQLiteTaskRepository:
    def __init__(self, database: Database):
        self.db = database

    async def _device(self, c: aiosqlite.Connection, device_id: str, secret: str | None = None):
        async with c.execute(
            "SELECT * FROM assistant_devices WHERE device_id=? AND revoked=0", (device_id,)
        ) as cursor:
            row = await cursor.fetchone()
        if row is None or (
            secret is not None
            and not hmac.compare_digest(
                row["secret_hash"], hashlib.sha256(secret.encode()).hexdigest()
            )
        ):
            raise AssistantAccessError("device_not_authorized")
        return row

    async def pair(self, name: str) -> dict[str, Any]:
        device_id, secret = str(uuid4()), secrets.token_urlsafe(32)
        await self.db.execute(
            "INSERT INTO assistant_devices(device_id,name,secret_hash) VALUES(?,?,?)",
            (device_id, name, hashlib.sha256(secret.encode()).hexdigest()),
        )
        return {"device_id": device_id, "secret": secret}

    async def devices(self) -> list[dict[str, Any]]:
        rows = await self.db.fetchall(
            "SELECT device_id,name,last_seen,sources_json FROM assistant_devices WHERE "
            "revoked=0 ORDER BY name"
        )
        return [
            {"device_id": r[0], "name": r[1], "last_seen": r[2], "sources": json.loads(r[3])}
            for r in rows
        ]

    async def revoke(self, device_id: str) -> None:
        async with self.db.transaction() as c:
            await c.execute(
                "DELETE FROM assistant_write_destinations WHERE provider='apple' AND device_id=?",
                (device_id,),
            )
            await c.execute(
                "UPDATE assistant_devices SET revoked=1,secret_hash='',sources_json='[]' "
                "WHERE device_id=?",
                (device_id,),
            )
            await c.execute(
                "UPDATE assistant_tasks SET state='cancelled' WHERE device_id=?", (device_id,)
            )
            await c.execute(
                "UPDATE assistant_deliveries SET state='cancelled' WHERE device_id=? AND "
                "state IN ('pending','presented')",
                (device_id,),
            )
            await c.execute(
                "UPDATE assistant_operations SET result_json='{}',state=CASE WHEN "
                "state='leased' THEN"
                "'uncertain' ELSE 'cancelled' END WHERE device_id=? AND state IN "
                "('queued','leased')",
                (device_id,),
            )

            await c.execute(
                "UPDATE assistant_operations SET result_json='{}' WHERE device_id=?", (device_id,)
            )

    async def create_task(self, task: TaskInput, now: float) -> dict[str, Any]:
        payload = task.model_dump(mode="json")
        async with self.db.transaction() as c:
            await self._device(c, str(task.device_id))
            async with c.execute(
                "SELECT payload_json FROM assistant_tasks WHERE task_id=?", (str(task.request_id),)
            ) as cursor:
                old = await cursor.fetchone()
            if old:
                if json.loads(old[0]) != payload:
                    raise ValueError("request_id_conflict")
                return payload
            async with c.execute(
                "SELECT COUNT(*) FROM assistant_tasks WHERE state IN ('active','paused')"
            ) as cursor:
                count = await cursor.fetchone()
            if count and count[0] >= 200:
                raise ValueError("active_task_limit_reached")
            if not now < task.due_at.timestamp() <= now + 366 * 86400:
                raise ValueError("due_time_must_be_future_within_one_year")
            await c.execute(
                "INSERT INTO assistant_tasks(task_id,device_id,payload_json,due) VALUES(?,?,?,?)",
                (
                    str(task.request_id),
                    str(task.device_id),
                    json.dumps(payload),
                    task.due_at.timestamp(),
                ),
            )
        return payload

    async def tasks(self) -> list[dict[str, Any]]:
        rows = await self.db.fetchall(
            "SELECT payload_json,state,due,revision FROM assistant_tasks "
            "ORDER BY CASE state WHEN 'active' THEN 0 WHEN 'paused' THEN 1 ELSE 2 END,due LIMIT 200"
        )
        return [
            {**json.loads(r[0]), "state": r[1], "next_due": r[2], "revision": r[3]} for r in rows
        ]

    async def revise_task(self, task: TaskInput, expected_revision: int, now: float) -> None:
        if not now < task.due_at.timestamp() <= now + 366 * 86400:
            raise ValueError("due_time_must_be_future_within_one_year")
        async with self.db.transaction() as c:
            await self._device(c, str(task.device_id))
            async with c.execute(
                "SELECT state,due,payload_json,revision FROM assistant_tasks WHERE task_id=?",
                (str(task.request_id),),
            ) as cursor:
                old = await cursor.fetchone()
            if not old or old[0] not in ("active", "paused"):
                raise ValueError("only_active_or_paused_tasks_can_be_edited")
            payload = task.model_dump(mode="json")
            if json.loads(old[2]) == payload:
                return
            if old[3] != expected_revision:
                raise ValueError("task_changed_refresh_before_editing")
            await c.execute(
                "UPDATE assistant_tasks SET "
                "device_id=?,payload_json=?,due=?,revision=revision+1 WHERE task_id=?",
                (
                    str(task.device_id),
                    json.dumps(payload),
                    task.due_at.timestamp(),
                    str(task.request_id),
                ),
            )
            await c.execute(
                "UPDATE assistant_deliveries SET state='cancelled' WHERE task_id=? AND "
                "state IN ('pending','presented')",
                (str(task.request_id),),
            )

    async def change_task(self, task_id: str, action: str, now: float) -> None:
        async with self.db.transaction() as c:
            async with c.execute(
                "SELECT * FROM assistant_tasks WHERE task_id=?", (task_id,)
            ) as cursor:
                row = await cursor.fetchone()
            if row is None:
                raise ValueError("task_not_found")
            if action not in ("pause", "resume", "cancel"):
                raise ValueError("invalid_task_action")
            if action == "pause" and row["state"] != "active":
                raise ValueError("only_active_tasks_can_pause")
            if action == "resume":
                if row["state"] != "paused":
                    raise ValueError("only_paused_tasks_can_resume")
                await self._device(c, row["device_id"])
                task = TaskInput.model_validate_json(row["payload_json"])
                due = row["due"]
                if due <= now:
                    following = next_due(
                        task.due_at, task.timezone, task.repeat, datetime.fromtimestamp(now, UTC)
                    )
                    if following is None:
                        raise ValueError("expired_one_time_task_requires_new_time")
                    due = following.timestamp()
                await c.execute(
                    "UPDATE assistant_tasks SET "
                    "state='active',due=?,revision=revision+1 WHERE task_id=?",
                    (due, task_id),
                )
            else:
                await c.execute(
                    "UPDATE assistant_tasks SET state=?,revision=revision+1 WHERE task_id=?",
                    ("paused" if action == "pause" else "cancelled", task_id),
                )
                await c.execute(
                    "UPDATE assistant_deliveries SET state='cancelled' WHERE task_id=? "
                    "AND state IN ('pending','presented')",
                    (task_id,),
                )

    async def tick(self, now: float) -> None:
        async with self.db.transaction() as c:
            async with c.execute(
                "SELECT * FROM assistant_tasks WHERE state='active' AND due<=? ORDER BY "
                "due LIMIT 200",
                (now,),
            ) as cursor:
                rows = await cursor.fetchall()
            for row in rows:
                task = TaskInput.model_validate_json(row["payload_json"])
                expires = row["due"] + (120 if task.kind == "alarm" else 3600)
                await c.execute(
                    "INSERT OR IGNORE INTO "
                    "assistant_deliveries(delivery_id,task_id,device_id,due,expires,state) "
                    "VALUES(?,?,?,?,?,?)",
                    (
                        str(uuid4()),
                        row["task_id"],
                        row["device_id"],
                        row["due"],
                        expires,
                        "pending" if expires > now else "missed",
                    ),
                )
                following = next_due(
                    task.due_at, task.timezone, task.repeat, datetime.fromtimestamp(now, UTC)
                )
                await c.execute(
                    "UPDATE assistant_tasks SET state=?,due=?,revision=revision+1 WHERE task_id=?",
                    (
                        "active" if following else "completed",
                        following.timestamp() if following else row["due"],
                        row["task_id"],
                    ),
                )
            await c.execute(
                "UPDATE assistant_deliveries SET state='missed' WHERE expires<=? AND "
                "state='pending'",
                (now,),
            )
            await c.execute(
                "UPDATE assistant_deliveries SET state='unhandled' WHERE expires<=? AND "
                "state='presented'",
                (now,),
            )
            # A leased write may already have reached EventKit. Never retry it automatically.
            await c.execute(
                "UPDATE assistant_operations SET state=CASE WHEN state='leased' THEN "
                "'uncertain' ELSE 'expired' END WHERE expires<=? AND state IN "
                "('queued','leased')",
                (now,),
            )
            await c.execute(
                "DELETE FROM assistant_deliveries WHERE expires<? AND state NOT IN "
                "('pending','presented')",
                (now - 30 * 86400,),
            )
            await c.execute(
                "DELETE FROM assistant_operations WHERE expires<? AND state NOT IN "
                "('queued','leased')",
                (now - 7 * 86400,),
            )

    async def poll(
        self,
        device_id: str,
        secret: str,
        sources: list[dict[str, Any]],
        now: float,
        *,
        deliver: bool = True,
        source_revision: int | None = None,
    ) -> dict[str, Any]:
        async with self.db.transaction() as c:
            old = await self._device(c, device_id, secret)
            if source_revision is not None and source_revision < old["source_revision"]:
                sources = json.loads(old["sources_json"])
                source_revision = old["source_revision"]
            allowed = {(s["resource"], s["id"]) for s in sources}
            writable = {(s["resource"], s["id"]) for s in sources if s["writable"]}
            if json.loads(old["sources_json"]) != sources:
                async with c.execute(
                    "SELECT kind,collection_id FROM assistant_write_destinations "
                    "WHERE provider='apple' AND device_id=?",
                    (device_id,),
                ) as cursor:
                    defaults = await cursor.fetchall()
                for destination in defaults:
                    if (destination["kind"], destination["collection_id"]) not in writable:
                        await c.execute(
                            "DELETE FROM assistant_write_destinations WHERE kind=? "
                            "AND provider='apple' AND device_id=?",
                            (destination["kind"], device_id),
                        )
                async with c.execute(
                    "SELECT operation_id,payload_json,state FROM assistant_operations "
                    "WHERE device_id=?",
                    (device_id,),
                ) as cursor:
                    operations = await cursor.fetchall()
                for op in operations:
                    payload = json.loads(op[1])
                    if (payload["resource"], payload["calendar_id"]) not in allowed:
                        await c.execute(
                            "UPDATE assistant_operations SET state=?,result_json='{}' "
                            "WHERE operation_id=?",
                            ("uncertain" if op[2] == "leased" else "cancelled", op[0]),
                        )
            await c.execute(
                "UPDATE assistant_devices SET "
                "last_seen=?,sources_json=?,source_revision=? WHERE device_id=?",
                (
                    now if deliver else old["last_seen"],
                    json.dumps(sources),
                    source_revision if source_revision is not None else old["source_revision"] + 1,
                    device_id,
                ),
            )
            if not deliver:
                return {"schema_version": "1.0", "updated": True}
            async with c.execute(
                "SELECT d.delivery_id,d.due,d.expires,d.state,t.payload_json FROM "
                "assistant_deliveries d JOIN assistant_tasks t USING(task_id) WHERE "
                "d.device_id=? AND d.state IN ('pending','presented') AND d.due<=? AND "
                "d.expires>? ORDER BY d.due LIMIT 20",
                (device_id, now, now),
            ) as cursor:
                rows = await cursor.fetchall()
            deliveries = [
                {
                    "delivery_id": r[0],
                    "due": r[1],
                    "expires": r[2],
                    "state": r[3],
                    **json.loads(r[4]),
                }
                for r in rows
            ]
            async with c.execute(
                "SELECT operation_id,payload_json,expires FROM assistant_operations WHERE "
                "device_id=? AND state='queued' AND expires>? ORDER BY expires LIMIT 1",
                (device_id, now),
            ) as cursor:
                row = await cursor.fetchone()
            operations = []
            if row:
                payload = json.loads(row[1])
                if (payload["resource"], payload["calendar_id"]) in allowed:
                    await c.execute(
                        "UPDATE assistant_operations SET state='leased' WHERE "
                        "operation_id=? AND state='queued'",
                        (row[0],),
                    )
                    operations = [{**payload, "expires_at": row[2]}]
                else:
                    await c.execute(
                        "UPDATE assistant_operations SET state='cancelled' WHERE operation_id=?",
                        (row[0],),
                    )
            return {"schema_version": "1.0", "deliveries": deliveries, "operations": operations}

    async def acknowledge(
        self,
        device_id: str,
        secret: str,
        item_id: str,
        action: str,
        result: dict[str, Any],
        now: float,
    ) -> None:
        async with self.db.transaction() as c:
            device = await self._device(c, device_id, secret)
            if action == "result":
                async with c.execute(
                    "SELECT * FROM assistant_operations WHERE operation_id=? AND device_id=?",
                    (item_id, device_id),
                ) as cursor:
                    row = await cursor.fetchone()
                if (
                    not row
                    or row["state"] not in ("leased", "succeeded", "failed")
                    or row["expires"] <= now
                ):
                    raise ValueError("operation_not_active")
                payload = json.loads(row["payload_json"])
                if not any(
                    s["resource"] == payload["resource"] and s["id"] == payload["calendar_id"]
                    for s in json.loads(device["sources_json"])
                ):
                    raise AssistantAccessError("source_no_longer_selected")
                if row["state"] == "leased":
                    await c.execute(
                        "UPDATE assistant_operations SET state=?,result_json=? WHERE "
                        "operation_id=?",
                        (
                            (
                                "uncertain"
                                if result.get("error")
                                in (
                                    "apple_write_outcome_uncertain",
                                    "apple_saved_readback_unavailable",
                                )
                                else "failed"
                                if result.get("error")
                                else "succeeded"
                            ),
                            json.dumps(result),
                            item_id,
                        ),
                    )
                return
            async with c.execute(
                "SELECT d.*,t.payload_json FROM assistant_deliveries d "
                "JOIN assistant_tasks t ON t.task_id=d.task_id "
                "WHERE d.delivery_id=? AND d.device_id=?",
                (item_id, device_id),
            ) as cursor:
                row = await cursor.fetchone()
            if row is not None and row["state"] == "acknowledged" and row["ack_action"] == action:
                return
            if action == "presented" and row is not None:
                if row["state"] == "unhandled":
                    return
                if row["state"] == "missed":
                    await c.execute(
                        "UPDATE assistant_deliveries SET state='unhandled' WHERE delivery_id=?",
                        (item_id,),
                    )
                    return
            if row is None or row["state"] not in ("pending", "presented") or row["expires"] <= now:
                raise ValueError("delivery_not_active")
            if action == "presented":
                await c.execute(
                    "UPDATE assistant_deliveries SET state='presented' WHERE delivery_id=?",
                    (item_id,),
                )
            else:
                await c.execute(
                    "UPDATE assistant_deliveries SET state='acknowledged',ack_action=? "
                    "WHERE delivery_id=?",
                    (action, item_id),
                )
                if action == "snooze":
                    task = TaskInput.model_validate_json(row["payload_json"])
                    snoozed_due = now + 300
                    lateness = 120 if task.kind == "alarm" else 3600
                    await c.execute(
                        "INSERT INTO "
                        "assistant_deliveries(delivery_id,task_id,device_id,due,expires) "
                        "VALUES(?,?,?,?,?)",
                        (
                            str(uuid4()),
                            row["task_id"],
                            device_id,
                            snoozed_due,
                            snoozed_due + lateness,
                        ),
                    )

    async def enqueue(self, operation: AppleOperation, now: float) -> dict[str, Any]:
        payload = operation.model_dump(mode="json")
        async with self.db.transaction() as c:
            device = await self._device(c, str(operation.device_id))
            if not any(
                s["resource"] == operation.resource
                and s["id"] == operation.calendar_id
                and (operation.action == "list" or s.get("writable"))
                for s in json.loads(device["sources_json"])
            ):
                raise AssistantAccessError("select_an_authorized_source_on_device")
            async with c.execute(
                "SELECT payload_json,state FROM assistant_operations WHERE operation_id=?",
                (str(operation.request_id),),
            ) as cursor:
                old = await cursor.fetchone()
            if old:
                if json.loads(old[0]) != payload:
                    raise ValueError("request_id_conflict")
                return {"operation_id": str(operation.request_id), "state": old[1]}
            async with c.execute(
                "SELECT COUNT(*) FROM assistant_operations WHERE state IN ('queued','leased')"
            ) as cursor:
                count = await cursor.fetchone()
            if count and count[0] >= 100:
                raise ValueError("pending_operation_limit_reached")
            # Short validity: delayed Apple writes should never appear hours later.
            await c.execute(
                "INSERT INTO "
                "assistant_operations(operation_id,device_id,payload_json,expires) "
                "VALUES(?,?,?,?)",
                (
                    str(operation.request_id),
                    str(operation.device_id),
                    json.dumps(payload),
                    now + 300,
                ),
            )
        return {"operation_id": str(operation.request_id), "state": "queued"}

    async def operations(self) -> list[dict[str, Any]]:
        rows = await self.db.fetchall(
            "SELECT o.operation_id,o.state,o.result_json FROM assistant_operations o "
            "JOIN assistant_devices d ON d.device_id=o.device_id WHERE d.revoked=0 "
            "ORDER BY o.expires DESC LIMIT 50"
        )
        return [{"operation_id": r[0], "state": r[1], "result": json.loads(r[2])} for r in rows]

    async def history(self) -> list[dict[str, Any]]:
        rows = await self.db.fetchall(
            "SELECT d.delivery_id,d.task_id,d.due,d.state,t.payload_json "
            "FROM assistant_deliveries d JOIN assistant_tasks t USING(task_id) "
            "WHERE d.state IN ('missed','unhandled') ORDER BY d.due DESC LIMIT 50"
        )
        items = [(row, TaskInput.model_validate_json(row[4])) for row in rows]
        return [
            {
                "delivery_id": r[0],
                "task_id": r[1],
                "due": r[2],
                "state": r[3],
                "title": payload.title,
                "kind": payload.kind,
            }
            for r, payload in items
        ]

    async def dismiss_history(self, delivery_id: str) -> None:
        async with self.db.transaction() as connection:
            async with connection.execute(
                "UPDATE assistant_deliveries SET state='dismissed' WHERE delivery_id=? "
                "AND state IN ('missed','unhandled')",
                (delivery_id,),
            ) as cursor:
                if cursor.rowcount == 1:
                    return
            async with connection.execute(
                "SELECT state FROM assistant_deliveries WHERE delivery_id=?",
                (delivery_id,),
            ) as cursor:
                row = await cursor.fetchone()
            if row is None or row[0] != "dismissed":
                raise ValueError("history_item_not_dismissible")
