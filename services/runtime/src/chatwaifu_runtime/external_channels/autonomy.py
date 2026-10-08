"""Opt-in group observation batching; all actions re-enter the existing fenced Runtime."""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID
from zoneinfo import ZoneInfo

from chatwaifu_protocol.agent import (
    AgentTask,
    DecisionRecord,
    GroupAutonomyPolicy,
    GroupAutonomyUpdate,
)
from chatwaifu_protocol.base import JsonObject

from chatwaifu_runtime.agent.behavior import BehaviorDecisionService
from chatwaifu_runtime.agent.behavior_ports import BehaviorRepository
from chatwaifu_runtime.characters.service import CharacterService
from chatwaifu_runtime.conversation.discussion_models import GroupDiscussionContext
from chatwaifu_runtime.external_channels.group_models import (
    ChannelGroupInboundDescriptor,
    ChannelGroupRouteRecord,
)

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class GroupObservation:
    descriptor: ChannelGroupInboundDescriptor
    route: ChannelGroupRouteRecord
    discussion: GroupDiscussionContext
    authorize: Callable[[], Awaitable[None]]
    respond: Callable[[int], Awaitable[object]]


class GroupAutonomyService:
    def __init__(
        self,
        repository: BehaviorRepository,
        decisions: BehaviorDecisionService,
        characters: CharacterService,
    ) -> None:
        self.repository = repository
        self.decisions = decisions
        self.characters = characters
        self._pending: dict[UUID, GroupObservation] = {}
        self._workers: dict[UUID, asyncio.Task[None]] = {}
        self._closing = False
        self.revoke_actions: Callable[[UUID], Awaitable[None]] | None = None
        self.context_reader: Callable[[GroupObservation], Awaitable[JsonObject]] | None = None
        self.task_creator: (
            Callable[[GroupObservation, GroupAutonomyPolicy, DecisionRecord], Awaitable[AgentTask]]
            | None
        ) = None
        self.memory_writer: (
            Callable[[GroupObservation, tuple[str, ...]], Awaitable[None]] | None
        ) = None

    async def policy(self, route: ChannelGroupRouteRecord) -> GroupAutonomyPolicy:
        stored = await self.repository.get(route.route_id)
        return stored or GroupAutonomyPolicy(route_id=route.route_id, route_revision=route.revision)

    async def configure(
        self, route: ChannelGroupRouteRecord, update: GroupAutonomyUpdate
    ) -> GroupAutonomyPolicy:
        current = await self.policy(route)
        policy = update.policy
        ZoneInfo(policy.timezone)
        if (
            policy.route_id != route.route_id
            or policy.route_revision != route.revision
            or current.revision != update.expected_revision
        ):
            raise ValueError("group autonomy policy or route revision changed")
        changed = policy.model_copy(update={"revision": current.revision + 1})
        if not await self.repository.save(changed, current.revision):
            raise ValueError("group autonomy revision conflict")
        self._pending.pop(route.route_id, None)
        worker = self._workers.get(route.route_id)
        if worker:
            worker.cancel()
            await asyncio.gather(worker, return_exceptions=True)
        if self.revoke_actions is not None:
            await self.revoke_actions(route.route_id)
        return changed

    async def start(self) -> None:
        self._closing = False

    def observe(self, observation: GroupObservation) -> None:
        route_id = observation.route.route_id
        if self._closing or (route_id not in self._pending and len(self._pending) >= 32):
            return
        self._pending[route_id] = observation
        if route_id not in self._workers:
            self._workers[route_id] = asyncio.create_task(self._run(route_id))

    async def stop(self) -> None:
        self._closing = True
        self._pending.clear()
        workers = tuple(self._workers.values())
        for worker in workers:
            worker.cancel()
        await asyncio.gather(*workers, return_exceptions=True)

    async def _run(self, route_id: UUID) -> None:
        try:
            while route_id in self._pending:
                observation = self._pending[route_id]
                policy = await self.policy(observation.route)
                if policy.mode == "off" or policy.route_revision != observation.route.revision:
                    self._pending.pop(route_id, None)
                    return
                # Keep the newest batch until its budget interval opens. A dropped
                # reservation must never discard the final message in a discussion.
                now = datetime.now(UTC)
                next_due = await self.repository.next_observation(policy, now)
                delay = max(policy.merge_seconds, (next_due - now).total_seconds())
                if delay >= 120:
                    self._pending.pop(route_id, None)
                    return
                timer = asyncio.Event()
                try:
                    await asyncio.wait_for(timer.wait(), delay)
                except TimeoutError:
                    pass
                observation = self._pending.pop(route_id, observation)
                now = datetime.now(UTC)
                local_hour = now.astimezone(ZoneInfo(policy.timezone)).hour
                quiet = (
                    (
                        policy.quiet_start <= local_hour < policy.quiet_end
                        if policy.quiet_start < policy.quiet_end
                        else local_hour >= policy.quiet_start or local_hour < policy.quiet_end
                    )
                    if policy.quiet_start != policy.quiet_end
                    else False
                )
                quiet_until = await self.repository.quiet_until(route_id)
                if quiet or (quiet_until is not None and quiet_until > now):
                    continue
                await observation.authorize()
                if not await self.repository.reserve(policy, now, speech=False):
                    continue
                profile = self.characters.get(observation.route.character_id)
                if profile is None:
                    raise ValueError("group character unavailable")
                messages = observation.discussion.messages
                refs = frozenset(m.message_id for m in messages)
                if not refs:
                    continue
                context: JsonObject = {
                    "scene": observation.route.scene_id,
                    "messages": [
                        {"source_ref": m.message_id, "speaker": m.participant_id, "text": m.text}
                        for m in messages
                        if m.expires_at > now
                    ],
                    "mode": policy.mode,
                    "available_actions": [
                        "wait",
                        "respond",
                        "clarify",
                        "task",
                        "defer",
                        "capability_gap",
                    ],
                    "memory_selection": (
                        "Select original messages with stable self-stated preferences, agreements "
                        "or unfinished commitments; exclude passing chatter and third-party claims."
                    ),
                }
                if self.context_reader is not None:
                    context["working_context"] = await self.context_reader(observation)
                started = time.monotonic()
                decision = await self.decisions.decide(profile.system_prompt, context, refs)
                await observation.authorize()
                latest = await self.repository.get(route_id)
                if latest != policy or route_id in self._pending:
                    continue
                await self.repository.record(policy, decision, datetime.now(UTC))
                if (
                    policy.mode == "member"
                    and policy.memory_enabled
                    and decision.memory_source_refs
                    and self.memory_writer is not None
                ):
                    await self.memory_writer(observation, tuple(decision.memory_source_refs))
                logger.info(
                    "agent.group_decision action=%s mode=%s duration_ms=%d",
                    decision.action,
                    policy.mode,
                    int((time.monotonic() - started) * 1000),
                )
                if decision.quiet_seconds:
                    await self.repository.set_quiet(
                        route_id, now + timedelta(seconds=decision.quiet_seconds)
                    )
                if policy.mode == "member" and decision.action in {"respond", "clarify"}:
                    if await self.repository.reserve(policy, datetime.now(UTC), speech=True):
                        await observation.authorize()
                        await observation.respond(policy.revision)
                elif (
                    policy.mode == "member"
                    and decision.action in {"task", "defer"}
                    and self.task_creator is not None
                ):
                    # The task's actual outbound plans reserve speech atomically;
                    # creating a goal is not itself a group message.
                    await self.task_creator(observation, policy, decision)
        except asyncio.CancelledError:
            raise
        except Exception as error:
            logger.warning("agent.group_decision_failed type=%s", type(error).__name__)
        finally:
            self._workers.pop(route_id, None)
            if route_id in self._pending and not self._closing:
                self.observe(self._pending[route_id])
