"""Dedicated Cloud Realtime Tool Bridge (Phase 13.5A).

Coordinates asynchronous Runtime Skill execution for cloud speech sessions:
1. Validates session, generation, turn, and response lineage before any mutation.
2. Deduplicates tool rounds and call identities before async invocation.
3. Bounds execution to max 4 calls and 1 decision round per generation.
4. Executes calls asynchronously without blocking the realtime coordinator event pump.
5. Protects cancellation during invoke admission and execution with bounded shields.
6. Rechecks generation active and tombstone states after every await.
7. Routes outbound tool results through CloudEgressGateway (policy/consent/durable audit)
   before provider socket writes.
8. Enforces tools-disabled continuation for the final spoken turn.
"""

from __future__ import annotations

import asyncio
import copy
import json
import logging
from collections import OrderedDict
from collections.abc import Mapping, Sequence
from typing import TYPE_CHECKING
from uuid import UUID

from chatwaifu_protocol.base import JsonObject

from chatwaifu_runtime.agent.tool_calling import (
    MAX_AGENT_TOOL_CALLS,
    AgentSkillGateway,
    bounded_tool_result_payload,
    cancel_skill_run_safely,
    compute_invocation_digest,
    error_tool_result_payload,
    format_tool_result_payload,
)
from chatwaifu_runtime.realtime.cloud.context import (
    CloudEgressGateway,
    ConsentRequiredError,
    PolicyDeniedError,
)
from chatwaifu_runtime.realtime.cloud.contracts import (
    CloudRealtimeSession,
    RealtimeToolCall,
    ToolCallRequestedEvent,
)
from chatwaifu_runtime.runtime_skills.agent_router import ProjectedSkillTool

if TYPE_CHECKING:
    from chatwaifu_runtime.realtime.cloud.coordinator import CloudRealtimeCoordinator
    from chatwaifu_runtime.realtime.cloud.mirror import RealtimeSessionMirror

_LOGGER = logging.getLogger(__name__)

MAX_EXECUTED_GENERATIONS = 200
MAX_SEEN_CALL_IDS = 1000


class CloudToolBridge:
    """Dedicated bridge between cloud realtime events and permissioned Runtime Skills."""

    def __init__(
        self,
        *,
        session: CloudRealtimeSession,
        skills: AgentSkillGateway,
        egress_gateway: CloudEgressGateway,
        tools_snapshot: Mapping[str, ProjectedSkillTool],
        backend_id: str,
        coordinator: CloudRealtimeCoordinator | None = None,
        mirror: RealtimeSessionMirror | None = None,
    ) -> None:
        self._session = session
        self._skills = skills
        self._egress_gateway = egress_gateway
        self._tools_snapshot = copy.deepcopy(dict(tools_snapshot))
        self._backend_id = backend_id
        self._coordinator = coordinator
        self._mirror = mirror

        # Lineage and reservation state
        self._executed_generations: OrderedDict[UUID, None] = OrderedDict()
        self._seen_call_ids: OrderedDict[tuple[UUID, str], str] = OrderedDict()
        self._active_tasks: dict[UUID, asyncio.Task[None]] = {}
        self._active_runs: dict[UUID, set[UUID]] = {}

    @property
    def tools_snapshot(self) -> dict[str, ProjectedSkillTool]:
        return copy.deepcopy(self._tools_snapshot)

    def attach(
        self,
        coordinator: CloudRealtimeCoordinator,
        mirror: RealtimeSessionMirror,
    ) -> None:
        """Attach runtime coordinator and mirror after bridge instantiation."""
        self._coordinator = coordinator
        self._mirror = mirror

    async def handle_tool_calls(
        self,
        event: ToolCallRequestedEvent,
        *,
        turn_id: UUID,
    ) -> None:
        """Reserve call identities, enforce mutation/dedup, and spawn async execution.

        Never blocks the coordinator event pump loop while skills execute.
        """
        gen_id = event.generation_id
        calls: Sequence[RealtimeToolCall] = copy.deepcopy(event.calls)

        # 0. Strict 4-way lineage validation before ANY dedup, state change, or effect:
        # (a) session_id
        if event.session_id != self._session.session_id:
            _LOGGER.warning(
                "Tool call rejected: session mismatch %s != %s",
                event.session_id,
                self._session.session_id,
            )
            return

        # (b) generation_id must be active and not tombstoned
        if not self._is_generation_active(gen_id):
            _LOGGER.debug(
                "Tool call rejected for inactive or tombstoned generation %s",
                gen_id,
            )
            return

        # (c) turn_id consistency against mirror
        if self._mirror is not None:
            registered_turn = self._mirror.get_turn_id(gen_id)
            if registered_turn is None or registered_turn != turn_id:
                _LOGGER.warning(
                    "Tool call rejected: turn mismatch %s != %s for generation %s",
                    turn_id,
                    registered_turn,
                    gen_id,
                )
                return

        # (d) provider_response_id consistency against mirror
        if event.provider_response_id and self._mirror is not None:
            mapped_gen = self._mirror.lookup_response_generation(event.provider_response_id)
            if mapped_gen != gen_id:
                _LOGGER.warning(
                    "Tool call rejected: provider_response_id %s already bound to %s, not %s",
                    event.provider_response_id,
                    mapped_gen,
                    gen_id,
                )
                return

        # 1. Reject changed args on same call_id (both within this single event and across events)
        seen_in_event: dict[str, str] = {}
        for call in calls:
            digest = compute_invocation_digest(call.name, call.arguments)
            # Check within this single event
            if call.call_id in seen_in_event:
                if seen_in_event[call.call_id] != digest:
                    _LOGGER.warning(
                        "Call ID %s arguments mutated within single event on generation %s",
                        call.call_id,
                        gen_id,
                    )
                    if self._coordinator is not None:
                        await self._coordinator.cancel_generation(
                            gen_id, reason="call_args_mutation_rejected"
                        )
                    return
            else:
                seen_in_event[call.call_id] = digest

            # Check across previously seen calls
            key = (gen_id, call.call_id)
            if key in self._seen_call_ids:
                if self._seen_call_ids[key] != digest:
                    _LOGGER.warning(
                        "Call ID %s arguments mutated across events on generation %s; rejecting",
                        call.call_id,
                        gen_id,
                    )
                    if self._coordinator is not None:
                        await self._coordinator.cancel_generation(
                            gen_id, reason="call_args_mutation_rejected"
                        )
                    return

        # 2. At most one decision / tool round per generation
        if gen_id in self._executed_generations:
            _LOGGER.debug(
                "Dropping duplicate tool round for generation %s (session %s)",
                gen_id,
                event.session_id,
            )
            return

        # 3. Check call count limit
        if len(calls) > MAX_AGENT_TOOL_CALLS:
            _LOGGER.warning(
                "Generation %s requested %d tool calls; exceeding limit of %d",
                gen_id,
                len(calls),
                MAX_AGENT_TOOL_CALLS,
            )

        # 4. Reserve and dedup call identities before async invocation
        round_calls: list[RealtimeToolCall] = []
        for call in calls[:MAX_AGENT_TOOL_CALLS]:
            digest = compute_invocation_digest(call.name, call.arguments)
            key = (gen_id, call.call_id)
            if key in self._seen_call_ids:
                _LOGGER.debug(
                    "Duplicate call ID %s on generation %s across differing event IDs",
                    call.call_id,
                    gen_id,
                )
                continue
            self._seen_call_ids[key] = digest
            if len(self._seen_call_ids) > MAX_SEEN_CALL_IDS:
                self._seen_call_ids.popitem(last=False)
            round_calls.append(call)

        self._executed_generations[gen_id] = None
        if len(self._executed_generations) > MAX_EXECUTED_GENERATIONS:
            self._executed_generations.popitem(last=False)

        # 5. Launch asynchronous execution without blocking coordinator event pump
        task = asyncio.create_task(
            self._execute_tool_round(
                event=event,
                turn_id=turn_id,
                calls=tuple(round_calls),
                had_excess=len(calls) > MAX_AGENT_TOOL_CALLS,
            ),
            name=f"cloud-tool-round-{gen_id}",
        )
        self._active_tasks[gen_id] = task

        def _cleanup(t: asyncio.Task[None]) -> None:
            self._active_tasks.pop(gen_id, None)

        task.add_done_callback(_cleanup)

    async def _execute_tool_round(
        self,
        *,
        event: ToolCallRequestedEvent,
        turn_id: UUID,
        calls: tuple[RealtimeToolCall, ...],
        had_excess: bool,
    ) -> None:
        gen_id = event.generation_id
        session_id = event.session_id

        # Recheck generation active state before starting
        if not self._is_generation_active(gen_id):
            return

        results: list[tuple[str, JsonObject]] = []
        seen_in_round: set[str] = set()

        if had_excess:
            for excess_call in event.calls[MAX_AGENT_TOOL_CALLS:]:
                err_payload = error_tool_result_payload(
                    "tool_call_limit_exceeded",
                    f"At most {MAX_AGENT_TOOL_CALLS} Runtime tools may be called in one turn",
                )
                results.append((excess_call.call_id, err_payload))

        # Clone arguments before any await to prevent external mutation
        cloned_calls = tuple(
            RealtimeToolCall(
                call_id=c.call_id,
                name=c.name,
                arguments=copy.deepcopy(c.arguments),
            )
            for c in calls
        )

        for call in cloned_calls:
            # Recheck generation after each step
            if not self._is_generation_active(gen_id):
                return

            projection = self._tools_snapshot.get(call.name)
            if projection is None:
                err_payload = error_tool_result_payload(
                    "unknown_tool",
                    f"The requested Runtime tool '{call.name}' was not exposed",
                )
                results.append((call.call_id, err_payload))
                continue

            digest = compute_invocation_digest(call.name, call.arguments)
            if digest in seen_in_round:
                err_payload = error_tool_result_payload(
                    "duplicate_tool_call",
                    "The same Runtime tool invocation was already attempted",
                )
                results.append((call.call_id, err_payload))
                continue
            seen_in_round.add(digest)

            # Execute via RuntimeSkillService / AgentSkillGateway
            active_run_id: UUID | None = None
            invoke_task = asyncio.create_task(
                self._skills.invoke(
                    session_id=session_id,
                    invocation=projection.to_invocation(call.arguments),
                    principal="character_agent",
                    turn_id=turn_id,
                    generation_id=gen_id,
                    origin="agent",
                    provider_tool_call_id=call.call_id,
                    allow_confirmation=False,
                    require_cloud_readonly=True,
                ),
                name=f"invoke-cloud-skill-{call.call_id}",
            )
            try:
                # Handle cancellation during invoke admission
                try:
                    snapshot = await asyncio.shield(invoke_task)
                    active_run_id = snapshot.skill_run_id
                    self._track_run(gen_id, active_run_id)
                except asyncio.CancelledError:
                    try:
                        admitted = await asyncio.wait_for(invoke_task, timeout=1.0)
                        await cancel_skill_run_safely(self._skills, admitted.skill_run_id)
                    except asyncio.CancelledError:
                        raise
                    except Exception:
                        pass
                    raise

                # Recheck generation after admission
                if not self._is_generation_active(gen_id):
                    await cancel_skill_run_safely(self._skills, active_run_id)
                    return

                terminal = await self._skills.wait_for_terminal(active_run_id)
                payload, summary = format_tool_result_payload(terminal)
                bounded = bounded_tool_result_payload(payload, summary)
                results.append((call.call_id, bounded))
            except asyncio.CancelledError:
                if active_run_id is not None:
                    await cancel_skill_run_safely(self._skills, active_run_id)
                raise
            except Exception as exc:
                if active_run_id is not None:
                    await cancel_skill_run_safely(self._skills, active_run_id)
                _LOGGER.warning("Tool invocation rejected for %s: %s", call.name, exc)
                results.append(
                    (
                        call.call_id,
                        error_tool_result_payload(
                            "tool_invocation_rejected",
                            "Runtime rejected the tool invocation before it could complete",
                        ),
                    )
                )
            finally:
                if active_run_id is not None:
                    self._untrack_run(gen_id, active_run_id)

        # All executions in the round completed.
        # Recheck generation before egress
        if not self._is_generation_active(gen_id):
            return

        # Route tool results through CloudEgressGateway before writing provider!
        for call_id, payload in results:
            if not self._is_generation_active(gen_id):
                return
            output_str = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
            try:
                await self._egress_gateway.evaluate_and_submit_tool_result(
                    session=self._session,
                    backend_id=self._backend_id,
                    call_id=call_id,
                    output=output_str,
                    generation_id=gen_id,
                )
            except (PolicyDeniedError, ConsentRequiredError) as egress_err:
                _LOGGER.warning(
                    "Tool result egress blocked by policy for generation %s: %s",
                    gen_id,
                    egress_err,
                )
                if self._coordinator is not None:
                    await self._coordinator.cancel_generation(
                        gen_id, reason=f"egress_denied: {egress_err}"
                    )
                return
            except Exception as audit_err:
                _LOGGER.error(
                    "Tool result egress audit failed for generation %s: %s",
                    gen_id,
                    audit_err,
                )
                if self._coordinator is not None:
                    await self._coordinator.cancel_generation(gen_id, reason="egress_audit_failed")
                return

        # Recheck generation after all result writes and before continuation
        if not self._is_generation_active(gen_id):
            return

        # Reserve continuation and trigger provider continuation with tools disabled
        if self._mirror is not None:
            self._mirror.reserve_continuation(gen_id)
            self._mirror.reset_provider_response_done(gen_id)

        try:
            await self._session.request_continuation(gen_id, disable_tools=True)
        except Exception as exc:
            _LOGGER.error(
                "Failed to request continuation for generation %s: %s",
                gen_id,
                exc,
            )
            if self._coordinator is not None:
                await self._coordinator.cancel_generation(
                    gen_id, reason=f"continuation_failed: {exc}"
                )

    def _is_generation_active(self, generation_id: UUID) -> bool:
        if self._mirror is None:
            return True
        return self._mirror.is_active(generation_id) and not self._mirror.is_tombstoned(
            generation_id
        )

    def active_task(self, generation_id: UUID) -> asyncio.Task[None] | None:
        """Return in-flight asyncio task for a generation if running."""
        return self._active_tasks.get(generation_id)

    def active_runs(self, generation_id: UUID) -> set[UUID]:
        """Return currently running skill run IDs for a generation."""
        return set(self._active_runs.get(generation_id, set()))

    def _track_run(self, generation_id: UUID, run_id: UUID) -> None:
        self._active_runs.setdefault(generation_id, set()).add(run_id)

    def _untrack_run(self, generation_id: UUID, run_id: UUID) -> None:
        runs = self._active_runs.get(generation_id)
        if runs is not None:
            runs.discard(run_id)
            if not runs:
                self._active_runs.pop(generation_id, None)

    async def cancel_active_runs(self, generation_id: UUID) -> None:
        """Cancel active tool execution tasks and active skill runs for a generation.

        Avoids cancelling or awaiting the current task if invoked from within a worker.
        Does not swallow caller CancelledError.
        """
        current = asyncio.current_task()
        task = self._active_tasks.get(generation_id)
        if task is not None and task is not current and not task.done():
            task.cancel()

        runs = list(self._active_runs.get(generation_id, set()))
        for run_id in runs:
            await cancel_skill_run_safely(self._skills, run_id)

        if task is not None and task is not current and not task.done():
            try:
                await asyncio.wait_for(
                    asyncio.gather(task, return_exceptions=True),
                    timeout=2.0,
                )
            except TimeoutError:
                pass
            except asyncio.CancelledError:
                if current is not None and current.cancelling() > 0:
                    raise
            except Exception:
                pass

    async def stop(self) -> None:
        """Cleanly cancel and join all in-flight tool tasks and runs upon teardown."""
        current = asyncio.current_task()
        all_tasks = [
            task for task in self._active_tasks.values() if not task.done() and task is not current
        ]
        for task in all_tasks:
            task.cancel()

        for _gen_id, runs in list(self._active_runs.items()):
            for run_id in list(runs):
                await cancel_skill_run_safely(self._skills, run_id)

        if all_tasks:
            try:
                await asyncio.wait_for(
                    asyncio.gather(*all_tasks, return_exceptions=True),
                    timeout=2.0,
                )
            except TimeoutError:
                pass
            except asyncio.CancelledError:
                raise
            except Exception:
                pass
        self._active_tasks.clear()
        self._active_runs.clear()
