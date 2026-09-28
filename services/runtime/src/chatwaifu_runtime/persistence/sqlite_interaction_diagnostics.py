"""SQLite projection of existing interaction, event, delivery, and playback facts."""

import json
import re
from collections.abc import Sequence
from datetime import datetime
from sqlite3 import Row
from typing import cast
from uuid import UUID

from chatwaifu_protocol.character import PromptBudgetReport, PromptContextIdentity
from chatwaifu_protocol.diagnostics import (
    DiagnosticDeliveryPart,
    DiagnosticMemoryReference,
    DiagnosticPlaybackSegment,
    DiagnosticResponsePlan,
    DiagnosticTimelineItem,
    DiagnosticToolCall,
    InteractionTraceDetail,
    InteractionTracePage,
    InteractionTraceSummary,
)
from pydantic import ValidationError

from chatwaifu_runtime.persistence.database import Database

_CODE = re.compile(r"^[a-z][a-z0-9_.-]{0,63}$")
_NONPARTICIPATION_EVENTS = ("voice.utterance_ignored", "companion.proactive_deferred")
_RESET_FENCE = """
    NOT EXISTS (
        SELECT 1 FROM memory_scope_resets AS reset
        WHERE (
            (reset.character_id = s.character_id AND reset.user_scope = s.user_scope)
            OR reset.character_id = '__all__'
        ) AND {occurred_at} <= reset.reset_at
    )
"""


def _safe_code(value: object) -> str | None:
    return value if isinstance(value, str) and _CODE.fullmatch(value) else None


def _payload(value: object) -> dict[str, object]:
    try:
        parsed: object = json.loads(str(value))
    except (TypeError, ValueError):
        return {}
    return cast(dict[str, object], parsed) if isinstance(parsed, dict) else {}


def _uuid(value: object) -> UUID | None:
    try:
        return UUID(str(value)) if value is not None else None
    except (TypeError, ValueError):
        return None


def _list_cursor(value: str | None) -> tuple[str, str] | None:
    if value is None:
        return None
    if len(value) > 128 or "|" not in value:
        raise ValueError("invalid interaction cursor")
    occurred_at, _, identifier = value.partition("|")
    try:
        datetime.fromisoformat(occurred_at)
        UUID(identifier)
    except ValueError as exc:
        raise ValueError("invalid interaction cursor") from exc
    return occurred_at, identifier


def _summary(row: Row) -> InteractionTraceSummary:
    values = cast(dict[str, object], dict(row))
    trigger = str(values["trigger"])
    reason = None
    if values.get("payload_json") is not None:
        reason = _safe_code(_payload(values["payload_json"]).get("reason"))
    return InteractionTraceSummary(
        interaction_id=UUID(str(values["interaction_id"])),
        session_id=UUID(str(values["session_id"])),
        turn_id=_uuid(values.get("turn_id")),
        generation_id=_uuid(values.get("generation_id")),
        occurred_at=datetime.fromisoformat(str(values["occurred_at"])),
        trigger=trigger,  # type: ignore[arg-type]
        reason=reason,
        generation_state=str(values["generation_state"])
        if values.get("generation_state") is not None
        else None,
    )


class SQLiteInteractionDiagnosticsReader:
    """Owner-only projection. Callers validate the session before using this adapter."""

    def __init__(self, database: Database) -> None:
        self._database = database

    async def list_interactions(
        self,
        session_id: UUID,
        *,
        cursor: str | None = None,
        limit: int = 50,
        include_nonparticipation: bool = False,
    ) -> InteractionTracePage:
        bounded = min(max(limit, 1), 50)
        marker = _list_cursor(cursor)
        rows = await self._database.fetchall(
            f"""
            WITH candidates AS (
                SELECT g.generation_id AS interaction_id, g.session_id, g.turn_id,
                       g.generation_id, COALESCE(g.started_at, t.created_at) AS occurred_at,
                       CASE WHEN t.role = 'system' THEN 'proactive' ELSE 'user' END AS trigger,
                       g.state AS generation_state, NULL AS payload_json
                FROM generations AS g
                JOIN turns AS t ON t.turn_id = g.turn_id AND t.session_id = g.session_id
                JOIN sessions AS s ON s.session_id = g.session_id
                WHERE g.session_id = ?
                  AND {_RESET_FENCE.format(occurred_at="t.created_at")}
                UNION ALL
                SELECT e.event_id, e.session_id,
                       json_extract(e.envelope_json, '$.turn_id'), NULL, e.occurred_at,
                       CASE WHEN e.event_type = 'voice.utterance_ignored'
                            THEN 'ignored_voice' ELSE 'proactive_deferred' END,
                       NULL, e.payload_json
                FROM events AS e
                JOIN sessions AS s ON s.session_id = e.session_id
                WHERE e.session_id = ?
                  AND ? = 1
                  AND e.event_type IN (?, ?)
                  AND {_RESET_FENCE.format(occurred_at="e.occurred_at")}
            )
            SELECT * FROM candidates
            WHERE (? IS NULL OR occurred_at < ?
                   OR (occurred_at = ? AND interaction_id < ?))
            ORDER BY occurred_at DESC, interaction_id DESC LIMIT ?
            """,
            (
                str(session_id),
                str(session_id),
                int(include_nonparticipation),
                *_NONPARTICIPATION_EVENTS,
                marker[0] if marker else None,
                marker[0] if marker else None,
                marker[0] if marker else None,
                marker[1] if marker else None,
                bounded + 1,
            ),
        )
        visible = rows[:bounded]
        next_cursor = None
        if len(rows) > bounded and visible:
            last = visible[-1]
            next_cursor = f"{last['occurred_at']}|{last['interaction_id']}"
        return InteractionTracePage(
            items=[_summary(row) for row in visible],
            has_more=len(rows) > bounded,
            next_cursor=next_cursor,
        )

    async def read_interaction(
        self,
        session_id: UUID,
        interaction_id: UUID,
        *,
        visible_namespaces: Sequence[str],
        after_sequence: int = 0,
        limit: int = 200,
    ) -> InteractionTraceDetail | None:
        generation = await self._database.fetchone(
            f"""
            SELECT g.generation_id AS interaction_id, g.session_id, g.turn_id,
                   g.generation_id, COALESCE(g.started_at, t.created_at) AS occurred_at,
                   CASE WHEN t.role = 'system' THEN 'proactive' ELSE 'user' END AS trigger,
                   g.state AS generation_state, NULL AS payload_json
            FROM generations AS g
            JOIN turns AS t ON t.turn_id = g.turn_id AND t.session_id = g.session_id
            JOIN sessions AS s ON s.session_id = g.session_id
            WHERE g.session_id = ? AND g.generation_id = ?
              AND {_RESET_FENCE.format(occurred_at="t.created_at")}
            """,
            (str(session_id), str(interaction_id)),
        )
        row = generation
        if row is None:
            row = await self._database.fetchone(
                f"""
                SELECT e.event_id AS interaction_id, e.session_id,
                       json_extract(e.envelope_json, '$.turn_id') AS turn_id,
                       NULL AS generation_id, e.occurred_at,
                       CASE WHEN e.event_type = 'voice.utterance_ignored'
                            THEN 'ignored_voice' ELSE 'proactive_deferred' END AS trigger,
                       NULL AS generation_state, e.payload_json
                FROM events AS e
                JOIN sessions AS s ON s.session_id = e.session_id
                WHERE e.session_id = ? AND e.event_id = ?
                  AND e.event_type IN (?, ?)
                  AND {_RESET_FENCE.format(occurred_at="e.occurred_at")}
                """,
                (str(session_id), str(interaction_id), *_NONPARTICIPATION_EVENTS),
            )
        if row is None:
            return None
        summary = _summary(row)
        bounded = min(max(limit, 1), 200)
        event_rows = await self._database.fetchall(
            """
            SELECT sequence, event_type, occurred_at, payload_json
            FROM events
            WHERE session_id = ? AND sequence > ? AND (
                (json_extract(envelope_json, '$.generation_id') = ?)
                OR (event_id = ?)
                OR (? IS NOT NULL AND json_extract(envelope_json, '$.turn_id') = ?
                    AND json_extract(envelope_json, '$.generation_id') IS NULL)
            )
            ORDER BY sequence ASC LIMIT ?
            """,
            (
                str(session_id),
                after_sequence,
                str(summary.generation_id) if summary.generation_id else "",
                str(interaction_id),
                str(summary.turn_id) if summary.turn_id else None,
                str(summary.turn_id) if summary.turn_id else "",
                bounded + 1,
            ),
        )
        timeline = [
            DiagnosticTimelineItem(
                sequence=int(item["sequence"]),
                event_type=str(item["event_type"]),
                occurred_at=datetime.fromisoformat(str(item["occurred_at"])),
                reason=_safe_code(_payload(item["payload_json"]).get("reason")),
                status=_safe_code(_payload(item["payload_json"]).get("status")),
            )
            for item in event_rows[:bounded]
        ]
        event_truncated = len(event_rows) > bounded
        if summary.generation_id is None:
            return InteractionTraceDetail(
                summary=summary,
                timeline=timeline,
                truncated=event_truncated,
                next_cursor=timeline[-1].sequence if event_truncated and timeline else None,
            )
        return await self._generation_detail(
            summary,
            visible_namespaces=visible_namespaces,
            timeline=timeline,
            event_truncated=event_truncated,
        )

    async def _generation_detail(
        self,
        summary: InteractionTraceSummary,
        *,
        visible_namespaces: Sequence[str],
        timeline: list[DiagnosticTimelineItem],
        event_truncated: bool,
    ) -> InteractionTraceDetail:
        assert summary.generation_id is not None
        session_key = str(summary.session_id)
        generation_key = str(summary.generation_id)
        turn_key = str(summary.turn_id) if summary.turn_id else ""
        metadata = await self._database.fetchall(
            """
            SELECT event_type, payload_json FROM events
            WHERE session_id = ? AND event_type IN (
                'memory.recalled', 'character.prompt_compiled', 'character.response_planned'
            ) AND (
                json_extract(envelope_json, '$.generation_id') = ?
                OR (json_extract(envelope_json, '$.turn_id') = ?
                    AND json_extract(envelope_json, '$.generation_id') IS NULL)
            ) ORDER BY sequence ASC LIMIT 500
            """,
            (session_key, generation_key, turn_key),
        )
        recalled: dict[UUID, float | None] = {}
        selected: list[UUID] | None = None
        identity: PromptContextIdentity | None = None
        budget: PromptBudgetReport | None = None
        plan: DiagnosticResponsePlan | None = None
        for item in metadata:
            payload = _payload(item["payload_json"])
            if item["event_type"] == "memory.recalled":
                ids = payload.get("memory_ids")
                scores = payload.get("scores")
                if isinstance(ids, list):
                    memory_ids = cast(list[object], ids)
                    memory_scores = cast(list[object], scores) if isinstance(scores, list) else []
                    for index, raw_id in enumerate(memory_ids[:200]):
                        memory_id = _uuid(raw_id)
                        score = memory_scores[index] if index < len(memory_scores) else None
                        if memory_id is not None:
                            recalled[memory_id] = (
                                float(score) if isinstance(score, int | float) else None
                            )
            elif item["event_type"] == "character.prompt_compiled":
                raw_selected = payload.get("selected_memory_ids")
                if isinstance(raw_selected, list):
                    selected = []
                    for raw in cast(list[object], raw_selected)[:200]:
                        identifier = _uuid(raw)
                        if identifier is not None:
                            selected.append(identifier)
                try:
                    if isinstance(payload.get("identity"), dict):
                        identity = PromptContextIdentity.model_validate(payload["identity"])
                    if isinstance(payload.get("report"), dict):
                        budget = PromptBudgetReport.model_validate(payload["report"])
                except ValidationError:
                    pass
            elif item["event_type"] == "character.response_planned":
                raw_plan = payload.get("plan")
                if isinstance(raw_plan, dict):
                    plan_values = cast(dict[str, object], raw_plan)
                    plan = DiagnosticResponsePlan(
                        intent=_safe_code(plan_values.get("intent")),
                        tone=_safe_code(plan_values.get("tone")),
                        expression=_safe_code(plan_values.get("expression")),
                        motion=_safe_code(plan_values.get("motion")),
                        response_length=_safe_code(plan_values.get("response_length")),
                    )
        all_ids: list[UUID] = []
        seen_ids: set[UUID] = set()
        for memory_id in recalled:
            if memory_id not in seen_ids and len(all_ids) < 200:
                seen_ids.add(memory_id)
                all_ids.append(memory_id)
        for memory_id in selected or []:
            if memory_id not in seen_ids and len(all_ids) < 200:
                seen_ids.add(memory_id)
                all_ids.append(memory_id)
        visible: set[UUID] = set()
        if all_ids and visible_namespaces:
            placeholders = ",".join("?" for _ in all_ids)
            namespace_marks = ",".join("?" for _ in visible_namespaces)
            rows = await self._database.fetchall(
                f"SELECT memory_id FROM memory_records WHERE state = 'active' "
                f"AND memory_id IN ({placeholders}) AND namespace IN ({namespace_marks})",
                (*[str(item) for item in all_ids], *visible_namespaces),
            )
            visible = {UUID(str(item["memory_id"])) for item in rows}
        memory_refs = [
            DiagnosticMemoryReference(
                memory_id=item,
                score=recalled.get(item),
                selected_for_prompt=item in (selected or ()),
                currently_visible=item in visible,
            )
            for item in all_ids
        ]
        tool_rows = await self._database.fetchall(
            """
            SELECT call.tool_call_id, call.status, call.started_at, call.completed_at
            FROM skill_tool_calls AS call
            JOIN skill_runs AS run ON run.skill_run_id = call.skill_run_id
            WHERE run.session_id = ? AND run.generation_id = ?
            ORDER BY call.started_at, call.tool_call_id LIMIT 201
            """,
            (session_key, generation_key),
        )
        tool_calls = [
            DiagnosticToolCall(
                tool_call_id=UUID(str(item["tool_call_id"])),
                status=str(item["status"]),
                duration_ms=_duration_ms(item["started_at"], item["completed_at"]),
            )
            for item in tool_rows[:200]
        ]
        delivery = await self._database.fetchone(
            """
            SELECT delivery.delivery_id, delivery.status
            FROM channel_turns AS turn
            JOIN channel_deliveries AS delivery ON delivery.channel_turn_id = turn.channel_turn_id
            WHERE turn.session_id = ? AND turn.generation_id = ?
            ORDER BY delivery.created_at DESC LIMIT 1
            """,
            (session_key, generation_key),
        )
        part_rows = (
            await self._database.fetchall(
                """
                SELECT part_id, ordinal, kind, required, status, attempt, delivered_at
                FROM channel_delivery_parts WHERE delivery_id = ?
                ORDER BY ordinal LIMIT 201
                """,
                (str(delivery["delivery_id"]),),
            )
            if delivery is not None
            else []
        )
        delivery_parts = [
            DiagnosticDeliveryPart(
                part_id=UUID(str(item["part_id"])),
                ordinal=int(item["ordinal"]),
                kind=str(item["kind"]),
                required=bool(item["required"]),
                status=str(item["status"]),
                attempt=int(item["attempt"]),
                delivered_at=datetime.fromisoformat(str(item["delivered_at"]))
                if item["delivered_at"]
                else None,
            )
            for item in part_rows[:200]
        ]
        playback_rows = await self._database.fetchall(
            """
            SELECT segment_id, segment_index, state, played_pts_ms, transport
            FROM playback_segments WHERE session_id = ? AND generation_id = ?
            ORDER BY segment_index LIMIT 201
            """,
            (session_key, generation_key),
        )
        playback_segments = [
            DiagnosticPlaybackSegment(
                segment_id=UUID(str(item["segment_id"])),
                segment_index=int(item["segment_index"]),
                state=str(item["state"]),
                played_pts_ms=int(item["played_pts_ms"]),
                transport=str(item["transport"]) if item["transport"] else None,
            )
            for item in playback_rows[:200]
        ]
        return InteractionTraceDetail(
            summary=summary,
            prompt_identity=identity,
            response_plan=plan,
            prompt_budget=budget,
            memory_candidates=memory_refs,
            selected_memory_ids=selected,
            tool_calls=tool_calls,
            delivery_status=str(delivery["status"]) if delivery else None,
            delivery_parts=delivery_parts,
            playback_segments=playback_segments,
            timeline=timeline,
            truncated=any(
                (
                    event_truncated,
                    len(metadata) == 500,
                    len(tool_rows) > 200,
                    len(part_rows) > 200,
                    len(playback_rows) > 200,
                )
            ),
            next_cursor=timeline[-1].sequence if event_truncated and timeline else None,
        )


def _duration_ms(started: object, completed: object) -> int | None:
    if started is None or completed is None:
        return None
    try:
        delta = datetime.fromisoformat(str(completed)) - datetime.fromisoformat(str(started))
    except ValueError:
        return None
    return max(0, round(delta.total_seconds() * 1000))
