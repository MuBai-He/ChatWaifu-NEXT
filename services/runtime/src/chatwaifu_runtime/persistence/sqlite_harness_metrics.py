"""Derived owner health and measured resource usage; no prompts, keys or provider prices."""

import math
import time
from typing import cast

from chatwaifu_protocol.base import JsonObject

from chatwaifu_runtime.persistence.database import Database
from chatwaifu_runtime.providers.metering import ModelUsageSample


def now_ms() -> int:
    return time.time_ns() // 1_000_000


def stored_tokens(value: int | None) -> int | None:
    # Reject implausible per-invocation counters without losing the invocation receipt.
    return value if type(value) is int and 0 <= value <= 2**31 - 1 else None


class SQLiteHarnessMetricsRepository:
    def __init__(self, database: Database) -> None:
        self.db = database

    async def record_usage(self, sample: ModelUsageSample) -> None:
        trace, usage = sample.trace, sample.usage
        async with self.db.transaction() as connection:
            await connection.execute(
                "INSERT OR IGNORE INTO harness_model_usage VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    str(sample.request_id),
                    trace.owner,
                    str(trace.task_id) if trace.task_id else None,
                    trace.work_item_id,
                    str(trace.observation_id) if trace.observation_id else None,
                    sample.role,
                    sample.model,
                    sample.duration_ms,
                    sample.state,
                    stored_tokens(usage.prompt_tokens) if usage else None,
                    stored_tokens(usage.completion_tokens) if usage else None,
                    stored_tokens(usage.total_tokens) if usage else None,
                    stored_tokens(usage.reasoning_tokens) if usage else None,
                    int(sample.synthetic),
                    sample.observed_at,
                ),
            )

            sources = set(trace.observation_ids)
            if trace.observation_id is not None:
                sources.add(trace.observation_id)
            await connection.executemany(
                "INSERT OR IGNORE INTO harness_model_usage_sources VALUES(?,?)",
                [(str(sample.request_id), str(source)) for source in sources],
            )

    async def _counts(self, query: str, parameters: tuple[object, ...]) -> JsonObject:
        return {str(row[0]): int(row[1]) for row in await self.db.fetchall(query, parameters)}

    async def _usage(
        self, owner: str, where: str = "", parameters: tuple[object, ...] = ()
    ) -> JsonObject:
        # Summed reported tokens are a lower bound whenever any invocation omits usage.
        row = await self.db.fetchone(
            "SELECT COUNT(*),SUM(duration_ms),SUM(prompt_tokens),SUM(completion_tokens),"
            "SUM(total_tokens),SUM(reasoning_tokens),SUM(total_tokens IS NULL),"
            "SUM(state='failed'),SUM(state='cancelled') FROM harness_model_usage "
            "WHERE owner=? AND synthetic=0 " + where,
            (owner, *parameters),
        )
        assert row is not None
        return {
            "invocations": int(row[0]),
            "wall_ms_including_admission": int(row[1] or 0),
            "reported_prompt_tokens": row[2],
            "reported_completion_tokens": row[3],
            "reported_total_tokens": row[4],
            "reported_reasoning_tokens": row[5],
            "invocations_without_total_tokens": int(row[6] or 0),
            "failed_invocations": int(row[7] or 0),
            "cancelled_invocations": int(row[8] or 0),
            "currency": None,
            "amount": None,
            "pricing": "not_configured",
            "unit": "runtime_model_invocation",
            "coverage": "traced_harness_invocations_only",
            "http_attempts": None,
        }

    async def snapshot(self, owner: str, work_item_id: str | None = None) -> JsonObject:
        stamp = now_ms()
        observations = await self._counts(
            "SELECT state,COUNT(*) FROM harness_observations WHERE owner=? GROUP BY state",
            (owner,),
        )
        rooms = await self._counts(
            "SELECT state,COUNT(*) FROM harness_processing WHERE owner=? GROUP BY state",
            (owner,),
        )
        work = await self._counts(
            "SELECT json_extract(payload,'$.state'),COUNT(*) FROM harness_work_items "
            "WHERE owner=? GROUP BY json_extract(payload,'$.state')",
            (owner,),
        )
        oldest = await self.db.fetchone(
            "SELECT MIN(json_extract(payload,'$.received_at')) FROM harness_observations "
            "WHERE owner=? AND state IN ('pending','running') "
            "AND json_extract(payload,'$.ingestion_mode')!='historical'",
            (owner,),
        )
        delays: list[float] = []
        for row in await self.db.fetchall(
            "SELECT json_extract(payload,'$.completed_at')-json_extract(payload,'$.received_at') "
            "FROM harness_observations WHERE owner=? AND state='done' "
            "AND json_extract(payload,'$.ingestion_mode')!='historical' "
            "AND json_extract(payload,'$.completed_at') IS NOT NULL ORDER BY rowid DESC LIMIT 1000",
            (owner,),
        ):
            if row[0] is not None:
                delays.append(max(0, int(row[0])) / 1000)
        delays.sort()
        tasks = await self.db.fetchone(
            "SELECT COUNT(*),SUM(json_extract(payload_json,'$.tool_calls')),"
            "SUM(json_extract(payload_json,'$.active_seconds')),"
            "SUM(state NOT IN ('succeeded','failed','cancelled') AND "
            "json_extract(payload_json,'$.wait_condition.kind')='outcome') FROM agent_tasks "
            "WHERE scope=?"
            + (" AND json_extract(payload_json,'$.work_item_id')=?" if work_item_id else ""),
            (owner, work_item_id) if work_item_id else (owner,),
        )
        assert tasks is not None
        alerts = await self._counts(
            "SELECT alert_key,active FROM harness_metric_alerts WHERE owner=? AND active=1",
            (owner,),
        )
        cost = await self._usage(
            owner,
            "AND work_item_id=?" if work_item_id else "",
            (work_item_id,) if work_item_id else (),
        )
        # One extraction may feed several work items. It is attributed to its observation,
        # displayed separately and never added repeatedly to the owner total.
        shared: JsonObject | None = None
        if work_item_id:
            shared = await self._usage(
                owner,
                "AND work_item_id IS NULL AND request_id IN (SELECT u.request_id "
                "FROM harness_model_usage_sources u JOIN harness_observations o "
                "ON o.observation_id=u.observation_id "
                "JOIN harness_work_items w ON w.owner=o.owner "
                "JOIN json_each(w.payload,'$.evidence') e "
                "ON json_extract(e.value,'$.kind')='knowledge' "
                "AND json_extract(e.value,'$.reference')=o.reference "
                "AND json_extract(e.value,'$.revision')=o.revision "
                "WHERE w.owner=? AND w.work_item_id=?)",
                (owner, work_item_id),
            )
        return {
            "schema_version": "1.0",
            "generated_at": stamp,
            "work_item_id": work_item_id,
            "scope": "owner_and_selected_work" if work_item_id else "owner",
            "observation_states": observations,
            "processing_room_states": rooms,
            "work_states": work,
            "source_coverage": {
                "known_rooms": sum(cast(int, n) for n in rooms.values()),
                "uncollected_messages": None,
            },
            "latency_seconds": {
                "oldest_current_pending": max(0, stamp - int(oldest[0])) / 1000
                if oldest and oldest[0] is not None
                else None,
                "completed_current_p50": delays[math.ceil(len(delays) * 0.5) - 1]
                if delays
                else None,
                "completed_current_p95": delays[math.ceil(len(delays) * 0.95) - 1]
                if delays
                else None,
                "completion_sample_size": len(delays),
                "completion_sample_limit": 1000,
            },
            "action_event_states": await self._counts(
                "SELECT state,COUNT(*) FROM harness_activity WHERE owner=? "
                "AND kind LIKE 'action.%' GROUP BY state",
                (owner,),
            ),
            "review_decisions": await self._counts(
                "SELECT json_extract(details,'$.decision'),COUNT(*) FROM harness_activity "
                "WHERE owner=? AND kind='review' GROUP BY json_extract(details,'$.decision')",
                (owner,),
            ),
            "task_cost": {
                "tasks": int(tasks[0]),
                "tool_calls": int(tasks[1] or 0),
                "active_seconds": float(tasks[2] or 0),
                "unknown_outcomes": int(tasks[3] or 0),
            },
            "model_cost": cost,
            "shared_source_model_cost": shared,
            "active_alerts": alerts,
            "duplicate_action_claims": None,
            "wrong_target_writes": None,
        }

    async def alert_changes(self, owner: str) -> list[tuple[str, bool, int]]:
        """Persist only threshold transitions. Reconciliation never needs a model."""
        oldest = await self.db.fetchone(
            "SELECT MIN(json_extract(payload,'$.received_at')) FROM harness_observations "
            "WHERE owner=? AND state IN ('pending','running') "
            "AND json_extract(payload,'$.ingestion_mode')!='historical'",
            (owner,),
        )
        desired = {
            "current_processing_delayed": bool(
                oldest and oldest[0] is not None and now_ms() - int(oldest[0]) >= 300000
            )
        }
        changed: list[tuple[str, bool, int]] = []
        async with self.db.transaction() as connection:
            for key, active in desired.items():
                async with connection.execute(
                    "SELECT active,updated_at,notified FROM harness_metric_alerts "
                    "WHERE owner=? AND alert_key=?",
                    (owner, key),
                ) as cursor:
                    row = await cursor.fetchone()
                previous = bool(row[0]) if row else False
                if previous != active:
                    version = max(now_ms(), int(row[1]) + 1 if row else 0)
                    await connection.execute(
                        "INSERT INTO harness_metric_alerts VALUES(?,?,?,?,0) "
                        "ON CONFLICT(owner,alert_key) DO UPDATE SET active=excluded.active,"
                        "updated_at=excluded.updated_at,notified=0",
                        (owner, key, int(active), version),
                    )
                    changed.append((key, active, version))
                elif row is not None and not row[2]:
                    changed.append((key, active, int(row[1])))
        return changed

    async def acknowledge_alert(self, owner: str, key: str, version: int) -> None:
        async with self.db.transaction() as connection:
            await connection.execute(
                "UPDATE harness_metric_alerts SET notified=1 "
                "WHERE owner=? AND alert_key=? AND updated_at=?",
                (owner, key, version),
            )
