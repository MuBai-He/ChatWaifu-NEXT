"""Opt-in synthetic evaluation through the existing read-only Runtime tool loop.

Full source/tool evidence lives only in explicitly requested evaluation artifacts.
Production audit redaction, permissions, adapters and orchestration are unchanged.
"""

from __future__ import annotations

import hashlib
import json
import time
from collections.abc import AsyncIterator
from dataclasses import asdict, dataclass, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal, cast
from uuid import UUID, uuid4

from chatwaifu_protocol.skills import SkillInvocation, SkillRunSnapshot, SkillRunState
from chatwaifu_runtime.agent.tool_calling import (
    AgentTurnOrchestrator,
    cancel_skill_run_safely,
    compute_tools_digest,
)
from chatwaifu_runtime.providers.contracts import (
    LlmProvider,
    LlmRequest,
    LlmStreamEvent,
    LlmTextDelta,
    LlmToolCallRequested,
    LlmUsage,
)
from chatwaifu_runtime.runtime_skills.agent_router import RuntimeSkillRouter
from chatwaifu_runtime.runtime_skills.service import RuntimeSkillService

_SOURCE_SKILLS = frozenset({"web.search", "web.read"})


class ProviderRequestLimit(RuntimeError):
    """No more provider stream invocations are authorized in this evaluation."""


class RuntimeMissingTerminal(RuntimeError):
    """Partial provider text cannot establish a completed evaluation turn."""


@dataclass(frozen=True)
class RuntimeSourceOutcome:
    reply: str
    finish_reason: str
    reply_origin: Literal["provider", "runtime_fallback"]
    usage: LlmUsage | None
    trace: dict[str, Any]


class _RecordedProvider:
    def __init__(self, provider: LlmProvider, path: Path, maximum: int) -> None:
        self.provider = provider
        self.path = path
        self.maximum = maximum
        self.calls: list[dict[str, Any]] = []
        self.sample_key = ""
        started: set[str] = set()
        if path.exists():
            for line in path.read_text(encoding="utf-8").splitlines():
                row = json.loads(line)
                if row["event"] == "started":
                    if row["call_id"] in started:
                        raise ValueError("duplicate provider round in evaluation journal")
                    started.add(row["call_id"])
        self.used = len(started)

    @property
    def kind(self) -> str:
        return self.provider.kind

    @property
    def supports_tool_calling(self) -> bool:
        return self.provider.supports_tool_calling

    def _append(self, value: dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as target:
            target.write(json.dumps(value, ensure_ascii=False) + "\n")

    async def stream(self, request: LlmRequest) -> AsyncIterator[LlmStreamEvent]:
        if self.used >= self.maximum:
            raise ProviderRequestLimit("actual provider round limit reached")
        call: dict[str, Any] = {
            "call_id": str(uuid4()),
            "sample_key": self.sample_key,
            "generation_id": str(request.generation_id),
            "started_at": datetime.now(UTC).isoformat(),
            "system_prompt_sha256": hashlib.sha256(request.system_prompt.encode()).hexdigest(),
            "tools": [asdict(tool) for tool in request.tools],
            "tool_choice": request.tool_choice if request.tools else None,
            "input_tool_exchanges": [asdict(exchange) for exchange in request.tool_exchanges],
            "requested_calls": [],
            "text": "",
            "finish_reason": None,
            "usage": None,
            "error_type": None,
        }
        # Persist the chargeable attempt before dispatch; unfinished attempts still
        # consume the global limit on resume. This journal has exactly one writer.
        self._append({"event": "started", **call})
        self.used += 1
        self.calls.append(call)
        started = time.perf_counter()
        try:
            async for event in self.provider.stream(request):
                if isinstance(event, LlmTextDelta):
                    call["text"] += event.text
                elif isinstance(event, LlmToolCallRequested):
                    call["requested_calls"].append(asdict(event.call))
                else:
                    call["finish_reason"] = event.finish_reason
                    call["usage"] = asdict(event.usage) if event.usage is not None else None
                yield event
        except BaseException as error:
            # Error messages may contain private provider addresses or credentials.
            call["error_type"] = type(error).__name__
            raise
        finally:
            call["latency_ms"] = int((time.perf_counter() - started) * 1000)
            self._append({"event": "finished", **call})

    def aggregate_usage(self) -> LlmUsage | None:
        values: dict[str, int | None] = {}
        for name in ("prompt_tokens", "completion_tokens", "total_tokens", "reasoning_tokens"):
            reported: list[object] = [
                cast(dict[str, Any], call["usage"]).get(name)
                if call.get("usage") is not None
                else None
                for call in self.calls
            ]
            values[name] = (
                sum(cast(list[int], reported))
                if reported and all(type(v) is int and v >= 0 for v in reported)
                else None
            )
        return LlmUsage(**values) if any(v is not None for v in values.values()) else None


class _SourceGateway:
    def __init__(self, service: RuntimeSkillService, allow_once: bool) -> None:
        self.service = service
        self.allow_once = allow_once
        self.runs: list[dict[str, Any]] = []

    def _record(self, snapshot: SkillRunSnapshot) -> None:
        record = snapshot.model_dump(mode="json")
        for i, previous in enumerate(self.runs):
            if previous["skill_run_id"] == record["skill_run_id"]:
                self.runs[i] = record
                return
        self.runs.append(record)

    async def invoke(
        self,
        session_id: UUID,
        invocation: SkillInvocation,
        *,
        principal: str = "local_user",
        turn_id: UUID | None = None,
        generation_id: UUID | None = None,
        origin: Literal["manual", "agent", "external_mcp"] = "manual",
        provider_tool_call_id: str | None = None,
        allow_confirmation: bool = True,
        require_cloud_readonly: bool = False,
    ) -> SkillRunSnapshot:
        if invocation.skill_id not in _SOURCE_SKILLS or invocation.background:
            raise PermissionError("evaluation only permits foreground public source tools")
        created = await self.service.invoke(
            session_id,
            invocation,
            principal=principal,
            turn_id=turn_id,
            generation_id=generation_id,
            origin=origin,
            provider_tool_call_id=provider_tool_call_id,
            allow_confirmation=allow_confirmation,
            require_cloud_readonly=require_cloud_readonly,
        )
        self._record(created)
        try:
            if created.state is SkillRunState.WAITING_FOR_CONFIRMATION:
                if created.confirmation_request_id is None:
                    raise RuntimeError("Runtime confirmation is missing its request ID")
                created = await self.service.decide_confirmation(
                    created.confirmation_request_id, "allow_once" if self.allow_once else "deny"
                )
                self._record(created)
            return created
        except BaseException:
            await cancel_skill_run_safely(self.service, created.skill_run_id)
            raise

    async def wait_for_terminal(self, run_id: UUID) -> SkillRunSnapshot:
        terminal = await self.service.wait_for_terminal(run_id)
        self._record(terminal)
        return terminal

    async def cancel(self, run_id: UUID) -> SkillRunSnapshot:
        terminal = await self.service.cancel(run_id)
        self._record(terminal)
        return terminal


class RuntimeSourceEvaluation:
    def __init__(
        self,
        provider: LlmProvider,
        service: RuntimeSkillService,
        *,
        trace_path: Path,
        max_provider_requests: int,
        allow_once: bool,
        dns_resolver: Literal["system", "cloudflare"] = "system",
    ) -> None:
        if max_provider_requests < 1:
            raise ValueError("provider request limit must be positive")
        self._provider = _RecordedProvider(provider, trace_path, max_provider_requests)
        self._gateway = _SourceGateway(service, allow_once)
        self._router = RuntimeSkillRouter(
            lambda: [skill for skill in service.list() if skill.skill_id in _SOURCE_SKILLS]
        )
        self._agent = AgentTurnOrchestrator(self._provider, self._gateway, self._router)
        self._resolver = dns_resolver
        self._running = False
        self.last_trace: dict[str, Any] | None = None

    async def run(
        self, request: LlmRequest, *, session_id: UUID, turn_id: UUID, sample_key: str
    ) -> RuntimeSourceOutcome:
        if self._running:
            raise RuntimeError("evaluation helper permits one turn at a time")
        self._provider.calls = []
        self._provider.sample_key = sample_key
        self._gateway.runs = []
        projections = self._agent.select_tools(
            request.user_text,
            routing_previous_user_text=request.routing_previous_user_text,
            supports_tool_calling=self._provider.supports_tool_calling,
        )
        request = replace(
            request,
            system_prompt=request.system_prompt
            + (
                "\nPublic source tools use an evaluation-only permission policy. "
                f"When selecting a source tool use dns_resolver={self._resolver}. "
                "This operational setting supplies no facts or source URLs."
            ),
        )
        self.last_trace = {
            "schema_version": "1.0",
            "execution_path": "runtime_source_tools",
            "permission_policy": "allow_once" if self._gateway.allow_once else "deny",
            "dns_resolver": self._resolver,
            "tools_digest": compute_tools_digest(projections),
            "provider_calls": self._provider.calls,
            "tool_runs": self._gateway.runs,
            "reply_origin": None,
        }
        reply = ""
        self._running = True
        try:
            async for text in self._agent.stream(
                request,
                session_id=session_id,
                turn_id=turn_id,
                ensure_current=lambda: None,
                tools=projections,
            ):
                reply += text
            last = self._provider.calls[-1] if self._provider.calls else {}
            if last and last.get("finish_reason") is None and last.get("error_type") is None:
                raise RuntimeMissingTerminal("provider stream ended without its terminal event")
            origin: Literal["provider", "runtime_fallback"] = (
                "provider"
                if last.get("finish_reason") not in {None, "tool_calls"}
                and last.get("text") == reply
                else "runtime_fallback"
            )
            self.last_trace["reply_origin"] = origin
            return RuntimeSourceOutcome(
                reply,
                str(last["finish_reason"]) if origin == "provider" else "other",
                origin,
                self._provider.aggregate_usage(),
                self.last_trace,
            )
        finally:
            self._running = False
