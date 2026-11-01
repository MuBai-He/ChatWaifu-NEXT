"""Occurrence identities and receipts, shared by Runtime projections and native sync."""

import hashlib
import json
from datetime import UTC, datetime
from typing import cast
from uuid import NAMESPACE_URL, UUID, uuid5

import aiosqlite
from chatwaifu_protocol.base import JsonObject
from chatwaifu_protocol.harness import ReminderOccurrence, WorkItem

_TERMINAL = {"dismissed", "snoozed", "completed", "cancelled"}


async def _rows(c: aiosqlite.Connection, sql: str, args: tuple[object, ...]) -> list[aiosqlite.Row]:
    return list(await c.execute_fetchall(sql, args))


async def _save(c: aiosqlite.Connection, item: ReminderOccurrence) -> None:
    await c.execute(
        (
            "INSERT INTO harness_reminders VALUES(?,?,?,?,?,?) ON CONFLIC"
            "T(occurrence_id) DO UPDATE SET state=excluded.state,payload="
            "excluded.payload"
        ),
        (
            str(item.occurrence_id),
            item.owner,
            item.work_item_id,
            int(item.scheduled_at.timestamp() * 1000),
            item.state,
            item.model_dump_json(),
        ),
    )


async def project_reminder_in(c: aiosqlite.Connection, work: WorkItem) -> JsonObject | None:
    now = datetime.now(UTC)
    rows = await _rows(
        c,
        "SELECT payload FROM harness_reminders WHERE owner=? AND work_item_id=?",
        (work.owner, work.work_item_id),
    )
    current = [ReminderOccurrence.model_validate_json(row[0]) for row in rows]
    active = (
        work.target_id is None
        and work.reminder_policy != "none"
        and work.remind_at is not None
        and work.state
        not in {
            "completed",
            "cancelled",
            "rejected",
            "proposed",
        }
    )
    identifier = (
        uuid5(
            NAMESPACE_URL,
            (
                f"reminder:{work.owner}:{work.work_item_id}:"
                f"{int(work.remind_at.timestamp() * 1000)}:{work.primary_device_id}"
            ),
        )
        if active and work.remind_at
        else None
    )
    for old in current:
        if old.occurrence_id != identifier and old.state not in _TERMINAL:
            await _save(
                c,
                old.model_copy(
                    update={
                        "state": "completed" if work.state == "completed" else "cancelled",
                        "updated_at": now,
                    }
                ),
            )
    if identifier is None or work.remind_at is None:
        return None
    old = next((value for value in current if value.occurrence_id == identifier), None)
    fingerprint = hashlib.sha256(
        json.dumps(
            work.model_dump(
                mode="json",
                include={
                    "title",
                    "details",
                    "starts_at",
                    "ends_at",
                    "due_at",
                    "due_date",
                    "remind_at",
                    "primary_device_id",
                },
            ),
            sort_keys=True,
        ).encode()
    ).hexdigest()
    item = (
        old.model_copy(
            update={
                "work_revision": old.work_revision
                if old.content_fingerprint == fingerprint
                else work.revision,
                "content_fingerprint": fingerprint,
                "updated_at": now,
            }
        )
        if old
        else ReminderOccurrence(
            occurrence_id=identifier,
            owner=work.owner,
            work_item_id=work.work_item_id,
            work_revision=work.revision,
            content_fingerprint=fingerprint,
            scheduled_at=work.remind_at,
            primary_device_id=work.primary_device_id,
            state="scheduled" if work.primary_device_id else "waiting_device",
            updated_at=now,
        )
    )
    await _save(c, item)
    return json.loads(item.model_dump_json())


async def native_receipt_in(
    c: aiosqlite.Connection, owner: str, device: UUID, receipt: JsonObject
) -> None:
    identifier = receipt.get("occurrence_id")
    if not isinstance(identifier, str):
        return
    rows = await _rows(
        c,
        "SELECT payload FROM harness_reminders WHERE owner=? AND occurrence_id=?",
        (owner, identifier),
    )
    if not rows:
        return
    item = ReminderOccurrence.model_validate_json(rows[0][0])
    state = receipt.get("state")
    if state not in {
        "accepted",
        "presented",
        "dismissed",
        "snoozed",
        "completed",
        "blocked",
        "outcome_unknown",
    }:
        return
    if state not in {"dismissed", "snoozed", "completed"} and item.primary_device_id != device:
        return
    observed = receipt.get("observed_at")
    when = (
        datetime.fromtimestamp(float(observed) / 1000, UTC)
        if isinstance(observed, (int, float))
        and 0 <= observed <= datetime.now(UTC).timestamp() * 1000 + 300000
        else datetime.now(UTC)
    )
    await c.execute(
        "INSERT OR IGNORE INTO harness_reminder_receipts VALUES(?,?,?,?,?)",
        (identifier, str(device), str(state), when.isoformat(), datetime.now(UTC).isoformat()),
    )
    updates: dict[str, object] = {"updated_at": datetime.now(UTC)}
    if state == "presented":
        updates["presented_at"] = when
    if state in {"dismissed", "snoozed", "completed"}:
        updates["acknowledged_at"] = when
    # Late delivery is recorded as a fact without reviving an invalid occurrence.
    if item.state not in _TERMINAL:
        updates["state"] = state
    await _save(c, item.model_copy(update=updates))


async def accept_download_in(
    c: aiosqlite.Connection, owner: str, device: str, record_id: str, revision: int
) -> None:
    rows = await _rows(
        c,
        "SELECT payload FROM knowledge_records WHERE owner=? AND record_id=? AND revision=?",
        (owner, record_id, revision),
    )
    if not rows:
        return
    record = json.loads(rows[0][0])
    receipt = record.get("origin", {}).get("reminder_occurrence")
    if not isinstance(receipt, dict):
        return
    receipt = cast(JsonObject, receipt)
    if receipt.get("primary_device_id") != device:
        return
    values = await _rows(
        c,
        "SELECT payload FROM harness_reminders WHERE owner=? AND occurrence_id=?",
        (owner, str(receipt.get("occurrence_id"))),
    )
    if not values:
        return
    item = ReminderOccurrence.model_validate_json(values[0][0])
    if item.state in _TERMINAL:
        return
    acknowledged = receipt.get("work_revision")
    if not isinstance(acknowledged, int):
        return
    now = datetime.now(UTC)
    item = item.model_copy(
        update={
            "accepted_work_revision": max(item.accepted_work_revision, acknowledged),
            "accepted_at": now,
            "state": "accepted" if item.state in {"scheduled", "waiting_device"} else item.state,
            "updated_at": now,
        }
    )
    await _save(c, item)
