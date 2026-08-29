"""Policy-driven proactive companion scheduling with an auditable local ledger."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, time, timedelta
from typing import Literal
from uuid import UUID, uuid4

from chatwaifu_protocol.base import PrivacyLevel
from chatwaifu_protocol.events import GenericCoreEvent

from chatwaifu_runtime.companion.activity import ActivityTracker
from chatwaifu_runtime.companion.models import (
    CompanionSettings,
    CompanionStatus,
    ResourceStatus,
)
from chatwaifu_runtime.companion.settings import CompanionSettingsService
from chatwaifu_runtime.conversation.models import GenerationAccepted
from chatwaifu_runtime.conversation.service import ConversationService
from chatwaifu_runtime.eventing.publisher import EventPublisher
from chatwaifu_runtime.persistence.database import Database
from chatwaifu_runtime.sessions.service import SessionService

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class ProactiveDecision:
    action: Literal["trigger", "defer", "wait"]
    reason: str


def decide_proactive(
    settings: CompanionSettings,
    *,
    now: datetime,
    idle_seconds: float,
    generation_active: bool,
    proactive_today: int,
    last_proactive_at: datetime | None,
) -> ProactiveDecision:
    """Pure policy function kept separate from scheduling and model calls."""

    if not settings.proactive_enabled:
        return ProactiveDecision("wait", "disabled")
    if idle_seconds < settings.proactive_idle_minutes * 60:
        return ProactiveDecision("wait", "idle_threshold_not_reached")
    if settings.quiet_hours_enabled and is_quiet_time(
        now, settings.quiet_start, settings.quiet_end
    ):
        return ProactiveDecision("defer", "quiet_hours")
    if generation_active:
        return ProactiveDecision("defer", "conversation_busy")
    if proactive_today >= settings.proactive_daily_budget:
        return ProactiveDecision("defer", "daily_budget_exhausted")
    if last_proactive_at is not None:
        elapsed = now.astimezone(UTC) - last_proactive_at.astimezone(UTC)
        if elapsed < timedelta(minutes=settings.proactive_cooldown_minutes):
            return ProactiveDecision("wait", "cooldown_active")
    return ProactiveDecision("trigger", "idle_check_in")


def is_quiet_time(now: datetime, start_value: str, end_value: str) -> bool:
    start = time.fromisoformat(start_value)
    end = time.fromisoformat(end_value)
    current = now.timetz().replace(tzinfo=None)
    if start == end:
        return True
    if start < end:
        return start <= current < end
    return current >= start or current < end


class AmbientCompanionService:
    def __init__(
        self,
        database: Database,
        settings: CompanionSettingsService,
        activity: ActivityTracker,
        sessions: SessionService,
        conversation: ConversationService,
        publisher: EventPublisher,
        resource_status: Callable[[], ResourceStatus],
        *,
        on_trigger: Callable[[], None] = lambda: None,
        poll_seconds: float = 15,
    ) -> None:
        self._database = database
        self._settings = settings
        self._activity = activity
        self._sessions = sessions
        self._conversation = conversation
        self._publisher = publisher
        self._resource_status = resource_status
        self._on_trigger = on_trigger
        self._poll_seconds = max(1, poll_seconds)
        self._changed = asyncio.Event()
        self._task: asyncio.Task[None] | None = None

    async def start(self) -> None:
        if self._task is None:
            self._task = asyncio.create_task(self._run(), name="ambient-companion-scheduler")

    async def stop(self) -> None:
        task = self._task
        self._task = None
        self._changed.set()
        if task is not None:
            task.cancel("runtime_stopping")
            try:
                await task
            except asyncio.CancelledError:
                pass

    def settings_changed(self) -> None:
        self._changed.set()

    async def status(self, *, now: datetime | None = None) -> CompanionStatus:
        current = now or datetime.now().astimezone()
        count, last_at = await self._daily_usage(current)
        return CompanionStatus(
            settings=self._settings.get(),
            resources=self._resource_status(),
            proactive_today=count,
            last_proactive_at=last_at,
        )

    async def evaluate_once(self, *, now: datetime | None = None) -> int:
        current = now or datetime.now().astimezone()
        settings = self._settings.get()
        proactive_today, last_at = await self._daily_usage(current)
        triggered = 0
        for session in await self._sessions.list_ready_sessions():
            decision = decide_proactive(
                settings,
                now=current,
                idle_seconds=self._activity.session_idle_seconds(session.session_id),
                generation_active=(
                    self._conversation.active_generation_id(session.session_id) is not None
                ),
                proactive_today=proactive_today,
                last_proactive_at=last_at,
            )
            if decision.action == "wait":
                continue
            if decision.action == "defer":
                await self._defer_once(session.session_id, decision.reason, current)
                continue
            await self._trigger(session.session_id, decision.reason, current)
            triggered += 1
            break
        return triggered

    async def trigger_manual(self, session_id: UUID) -> GenerationAccepted:
        session = await self._sessions.get_session(session_id)
        if session is None:
            raise KeyError(f"unknown session {session_id}")
        return await self._trigger(session_id, "manual_preview", datetime.now().astimezone())

    async def _trigger(
        self, session_id: UUID, reason: str, scheduled_at: datetime
    ) -> GenerationAccepted:
        action_id = await self._record(session_id, "triggered", reason, scheduled_at)
        try:
            self._on_trigger()
            accepted = await self._conversation.submit_proactive(session_id, reason=reason)
        except BaseException:
            await self._execute(
                """
                UPDATE ambient_actions
                SET decision = 'deferred', reason = 'generation_rejected'
                WHERE action_id = ?
                """,
                (str(action_id),),
            )
            raise
        await self._execute(
            "UPDATE ambient_actions SET emitted_at = ? WHERE action_id = ?",
            (datetime.now(UTC).isoformat(), str(action_id)),
        )
        return accepted

    async def _defer_once(self, session_id: UUID, reason: str, now: datetime) -> None:
        row = await self._database.fetchone(
            """
            SELECT scheduled_at FROM ambient_actions
            WHERE session_id = ? AND decision = 'deferred' AND reason = ?
            ORDER BY scheduled_at DESC LIMIT 1
            """,
            (str(session_id), reason),
        )
        if row is not None:
            previous = datetime.fromisoformat(str(row["scheduled_at"]))
            if now.astimezone(UTC) - previous.astimezone(UTC) < timedelta(minutes=5):
                return
        await self._record(session_id, "deferred", reason, now)
        await self._publisher.emit(
            GenericCoreEvent.model_validate(
                {
                    "event_id": uuid4(),
                    "event_type": "companion.proactive_deferred",
                    "session_id": session_id,
                    "occurred_at": datetime.now(UTC),
                    "source": "runtime.companion",
                    "privacy": PrivacyLevel.LOCAL,
                    "payload": {"reason": reason},
                }
            )
        )

    async def _record(
        self,
        session_id: UUID,
        decision: Literal["triggered", "deferred"],
        reason: str,
        scheduled_at: datetime,
    ) -> UUID:
        action_id = uuid4()
        await self._execute(
            """
            INSERT INTO ambient_actions(
                action_id, session_id, kind, decision, reason, scheduled_at, emitted_at
            ) VALUES (?, ?, 'idle_check_in', ?, ?, ?, NULL)
            """,
            (
                str(action_id),
                str(session_id),
                decision,
                reason,
                scheduled_at.astimezone(UTC).isoformat(),
            ),
        )
        return action_id

    async def _execute(self, query: str, parameters: tuple[object, ...]) -> None:
        async with self._database.transaction() as connection:
            await connection.execute(query, parameters)

    async def _daily_usage(self, now: datetime) -> tuple[int, datetime | None]:
        local_now = now.astimezone()
        local_start = local_now.replace(hour=0, minute=0, second=0, microsecond=0)
        local_end = local_start + timedelta(days=1)
        row = await self._database.fetchone(
            """
            SELECT COUNT(*) AS action_count, MAX(emitted_at) AS last_emitted_at
            FROM ambient_actions
            WHERE decision = 'triggered' AND scheduled_at >= ? AND scheduled_at < ?
            """,
            (local_start.astimezone(UTC).isoformat(), local_end.astimezone(UTC).isoformat()),
        )
        if row is None:
            return 0, None
        raw_last = row["last_emitted_at"]
        return int(row["action_count"]), (
            datetime.fromisoformat(str(raw_last)) if raw_last is not None else None
        )

    async def _run(self) -> None:
        try:
            while True:
                self._changed.clear()
                try:
                    await asyncio.wait_for(self._changed.wait(), timeout=self._poll_seconds)
                except TimeoutError:
                    pass
                try:
                    await self.evaluate_once()
                except Exception:
                    logger.exception("ambient companion evaluation failed")
        except asyncio.CancelledError:
            raise
