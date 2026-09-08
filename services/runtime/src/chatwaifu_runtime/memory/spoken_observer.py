from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, cast
from uuid import UUID

from chatwaifu_runtime.memory.ports import SpokenMemoryFact, SpokenMemoryRepository
from chatwaifu_runtime.memory.service import deserialize_candidates, serialize_candidates

if TYPE_CHECKING:
    from chatwaifu_runtime.eventing.hub import EventHub, EventSubscription
    from chatwaifu_runtime.memory.service import MemoryService
    from chatwaifu_runtime.playback.service import PlaybackService
    from chatwaifu_runtime.sessions.service import SessionService

_LOGGER = logging.getLogger(__name__)


class SpokenMemoryObserver:
    """Durably observes committed assistant spoken text and extracts shared memory."""

    def __init__(
        self,
        sessions: SessionService,
        memory: MemoryService,
        event_hub: EventHub,
        repository: SpokenMemoryRepository,
        playback: PlaybackService | None = None,
        *,
        clock: Callable[[], datetime] | None = None,
        max_retries: int = 5,
        initial_retry_delay_s: float = 1.0,
        max_retry_delay_s: float = 60.0,
        backoff_factor: float = 2.0,
        reconcile_interval_s: float = 5.0,
        hook_after_stage: Callable[[SpokenMemoryFact], Awaitable[None]] | None = None,
        hook_after_candidate_applied: (
            Callable[[SpokenMemoryFact, int], Awaitable[None]] | None
        ) = None,
        hook_after_apply: Callable[[SpokenMemoryFact], Awaitable[None]] | None = None,
    ) -> None:
        self._sessions = sessions
        self._memory = memory
        self._event_hub = event_hub
        self._repository = repository
        self._playback = playback
        self._clock = clock or (lambda: datetime.now(UTC))
        self._max_retries = max_retries
        self._initial_retry_delay_s = initial_retry_delay_s
        self._max_retry_delay_s = max_retry_delay_s
        self._backoff_factor = backoff_factor
        self._reconcile_interval_s = reconcile_interval_s

        # Test hooks for deterministic crash and barrier injection
        self.hook_after_stage = hook_after_stage
        self.hook_after_candidate_applied = hook_after_candidate_applied
        self.hook_after_apply = hook_after_apply

        self._subscription: EventSubscription | None = None
        self._pump_task: asyncio.Task[None] | None = None
        self._worker_task: asyncio.Task[None] | None = None
        self._wakeup_event = asyncio.Event()
        self._idle_event = asyncio.Event()
        self._idle_event.set()
        self._is_running = False

    async def start(self) -> None:
        if self._is_running:
            return
        self._is_running = True

        # Backfill bounded batch of unprocessed spoken events
        await self._repository.backfill_unprocessed_spoken_events(limit=100)
        pending = await self._repository.list_pending(as_of=self._clock(), limit=1)
        if pending:
            self._idle_event.clear()
        else:
            self._idle_event.set()

        def _filter(event: dict[str, object]) -> bool:
            return event.get("event_type") == "assistant.spoken_text_committed"

        self._subscription = self._event_hub.subscribe(_filter)
        self._pump_task = asyncio.create_task(
            self._pump_events(),
            name="spoken-memory-observer-pump",
        )
        self._worker_task = asyncio.create_task(
            self._process_queue_loop(),
            name="spoken-memory-observer-worker",
        )
        self._wakeup_event.set()

    async def stop(self) -> None:
        self._is_running = False
        if self._subscription is not None:
            self._event_hub.unsubscribe(self._subscription)
            self._subscription = None
        self._wakeup_event.set()
        tasks = [t for t in (self._pump_task, self._worker_task) if t is not None]
        for t in tasks:
            t.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self._pump_task = None
        self._worker_task = None

    async def _pump_events(self) -> None:
        assert self._subscription is not None
        while self._is_running:
            try:
                event = await self._subscription.receive()
            except asyncio.CancelledError:
                break
            except Exception:
                _LOGGER.exception("Error receiving from event hub in SpokenMemoryObserver")
                continue

            try:
                session_id_raw = event.get("session_id")
                if not isinstance(session_id_raw, (str, UUID)):
                    continue
                session_id = UUID(str(session_id_raw))

                turn_id_raw = event.get("turn_id")
                if not isinstance(turn_id_raw, (str, UUID)):
                    continue
                turn_id = UUID(str(turn_id_raw))

                event_id_raw = event.get("event_id")
                if not isinstance(event_id_raw, (str, UUID)):
                    continue
                event_id = UUID(str(event_id_raw))

                payload: object = event.get("payload")
                spoken_text = ""
                if isinstance(payload, dict):
                    payload_dict = cast(dict[str, object], payload)
                    raw_text: object = payload_dict.get("spoken_text")
                    if isinstance(raw_text, str):
                        spoken_text = raw_text

                if not spoken_text.strip():
                    continue

                await self._repository.record_fact_if_new(
                    source_event_id=event_id,
                    session_id=session_id,
                    turn_id=turn_id,
                    spoken_text=spoken_text,
                    created_at=datetime.fromisoformat(str(event["occurred_at"])),
                )
                if not await self._repository.is_completed(event_id):
                    self._idle_event.clear()
                    self._wakeup_event.set()
            except Exception:
                _LOGGER.exception("Error recording spoken fact in SpokenMemoryObserver")

    async def _process_queue_loop(self) -> None:
        loop = asyncio.get_running_loop()
        last_reconcile = loop.time()
        while self._is_running:
            try:
                now_monotonic = loop.time()
                if now_monotonic - last_reconcile >= self._reconcile_interval_s:
                    last_reconcile = now_monotonic
                    await self._repository.backfill_unprocessed_spoken_events(limit=50)

                now = self._clock()
                pending = await self._repository.list_pending(as_of=now, limit=50)
                if not pending:
                    earliest_retry = await self._repository.get_earliest_retry_at()
                    if earliest_retry is None:
                        self._idle_event.set()
                        timeout = 5.0
                    else:
                        self._idle_event.clear()
                        diff = (earliest_retry - now).total_seconds()
                        timeout = min(max(0.05, diff), 5.0)

                    self._wakeup_event.clear()
                    try:
                        await asyncio.wait_for(self._wakeup_event.wait(), timeout=timeout)
                    except TimeoutError:
                        pass
                    continue

                self._idle_event.clear()
                for fact in pending:
                    if not self._is_running:
                        break
                    await self._process_single_fact(fact)
            except asyncio.CancelledError:
                break
            except Exception:
                _LOGGER.exception("Error in SpokenMemoryObserver worker loop")
                await asyncio.sleep(0.5)

    async def _process_single_fact(self, fact: SpokenMemoryFact) -> None:
        if await self._repository.is_completed(fact.source_event_id):
            return

        session = await self._sessions.get_session(fact.session_id)
        if session is None or not session.character_id:
            _LOGGER.warning(
                "Skipping spoken memory observation for session %s: character not found",
                fact.session_id,
            )
            await self._repository.mark_completed(fact.source_event_id, self._clock())
            return

        # Check privacy reset fence: do not resurrect cleared character memory
        if await self._repository.is_scope_reset(session.character_id, fact.created_at):
            _LOGGER.info(
                "Skipping spoken memory fact %s for reset character %s",
                fact.source_event_id,
                session.character_id,
            )
            await self._repository.mark_completed(fact.source_event_id, self._clock())
            return

        try:
            # 1. Staging phase: ensure candidates are durably staged before apply
            if fact.staged_candidates_json is not None:
                staged = deserialize_candidates(fact.staged_candidates_json)
            else:
                staged = await self._memory.extract_spoken_candidates(
                    source_event_id=fact.source_event_id,
                    session_id=fact.session_id,
                    character_id=session.character_id,
                    spoken_text=fact.spoken_text,
                )
                await self._repository.stage_candidates(
                    fact.source_event_id, serialize_candidates(staged)
                )

            if self.hook_after_stage is not None:
                await self.hook_after_stage(fact)

            # 2. Apply phase: apply candidates from checkpoint index
            if staged:

                async def _on_candidate_applied(applied_index: int) -> None:
                    await self._repository.update_checkpoint(
                        fact.source_event_id, applied_index + 1
                    )
                    if self.hook_after_candidate_applied is not None:
                        await self.hook_after_candidate_applied(fact, applied_index)

                await self._memory.apply_spoken_candidates(
                    session_id=fact.session_id,
                    turn_id=fact.turn_id,
                    source_event_id=fact.source_event_id,
                    character_id=session.character_id,
                    candidates=staged,
                    start_index=fact.checkpoint_index,
                    on_candidate_applied=_on_candidate_applied,
                )

            if self.hook_after_apply is not None:
                await self.hook_after_apply(fact)

            # 3. Mark completed
            await self._repository.mark_completed(fact.source_event_id, self._clock())
        except asyncio.CancelledError:
            raise
        except Exception as error:
            now = self._clock()
            new_retry = fact.retry_count + 1
            error_msg = type(error).__name__
            if new_retry >= self._max_retries:
                _LOGGER.error(
                    "SpokenMemoryObserver reached max retries (%d) for fact %s: %s",
                    self._max_retries,
                    fact.source_event_id,
                    error_msg,
                )
                await self._repository.record_dead_letter(
                    fact.source_event_id, error_msg, new_retry
                )
            else:
                delay = min(
                    self._initial_retry_delay_s * (self._backoff_factor ** (new_retry - 1)),
                    self._max_retry_delay_s,
                )
                next_retry_at = now + timedelta(seconds=delay)
                _LOGGER.warning(
                    "SpokenMemoryObserver failed for fact %s (retry %d/%d in %.2fs): %s",
                    fact.source_event_id,
                    new_retry,
                    self._max_retries,
                    delay,
                    type(error).__name__,
                )
                await self._repository.record_retry_failure(
                    fact.source_event_id, error_msg, new_retry, next_retry_at
                )

    async def wait_until_idle(self) -> None:
        """Wait deterministically until all pending spoken facts have been processed."""
        while self._is_running:
            pending = await self._repository.list_pending(as_of=self._clock(), limit=1)
            if not pending and self._idle_event.is_set():
                return
            if pending:
                self._idle_event.clear()
                self._wakeup_event.set()
            await self._idle_event.wait()
