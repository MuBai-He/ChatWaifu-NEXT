"""Evaluation consumes real permissioned tools and preserves actual call evidence."""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from pathlib import Path
from uuid import UUID, uuid4

import httpx2
import pytest
from chatwaifu_runtime.bootstrap.container import RuntimeContainer
from chatwaifu_runtime.config.settings import Settings
from chatwaifu_runtime.conversation.source_context import SourceContextPacket
from chatwaifu_runtime.providers.contracts import (
    LlmInputBudget,
    LlmRequest,
    LlmResponseCompleted,
    LlmStreamEvent,
    LlmTextDelta,
    LlmToolCall,
    LlmToolCallingUnavailableError,
    LlmToolCallRequested,
    LlmUsage,
)
from chatwaifu_runtime.runtime_skills.agent_router import RuntimeSkillRouter
from chatwaifu_runtime.runtime_skills.transports import ValidatedMcpEndpoint

from tools.runtime_source_evaluation import (
    ProviderRequestLimit,
    RuntimeMissingTerminal,
    RuntimeSourceEvaluation,
)


class _Provider:
    kind = "scripted"
    supports_tool_calling = True

    def __init__(self) -> None:
        self.requests: list[LlmRequest] = []

    async def stream(self, request: LlmRequest) -> AsyncIterator[LlmStreamEvent]:
        self.requests.append(request)
        if request.tools and not request.tool_exchanges:
            tool = next(
                tool for tool in request.tools if tool.input_schema.get("required") == ["url"]
            )
            yield LlmToolCallRequested(
                LlmToolCall("read-source", tool.name, {"url": "https://source.example/"})
            )
            yield LlmResponseCompleted("tool_calls", LlmUsage(10, 2, 12, None))
        else:
            yield LlmTextDelta("Actual source result is in the tool exchange.")
            yield LlmResponseCompleted("stop", LlmUsage(20, 3, 23, None))


class _MissingFirstProvider(_Provider):
    async def stream(self, request: LlmRequest) -> AsyncIterator[LlmStreamEvent]:
        if not self.requests:
            self.requests.append(request)
            yield LlmTextDelta("Unverified source claim")
            yield LlmResponseCompleted("stop", LlmUsage(5, 1, 6, None))
            return
        async for event in super().stream(request):
            yield event


@pytest.mark.asyncio
@pytest.mark.parametrize("allow_once", [True, False])
@pytest.mark.parametrize("use_correction", [False, True])
async def test_runtime_eval_records_actual_confirmation_result_and_all_provider_rounds(
    runtime_settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    allow_once: bool,
    use_correction: bool,
) -> None:
    requests: list[httpx2.Request] = []

    async def resolve(
        *args: object, **kwargs: object
    ) -> list[tuple[int, int, int, str, tuple[str, int]]]:
        return [(2, 1, 6, "", ("93.184.216.34", 443))]

    def transport(endpoint: ValidatedMcpEndpoint) -> httpx2.AsyncBaseTransport:
        def handle(request: httpx2.Request) -> httpx2.Response:
            requests.append(request)
            return httpx2.Response(200, headers={"content-type": "text/plain"}, text="Actual body")

        return httpx2.MockTransport(handle)

    monkeypatch.setattr(asyncio.get_running_loop(), "getaddrinfo", resolve)
    monkeypatch.setattr(
        "chatwaifu_runtime.runtime_skills.public_web.PinnedAsyncHTTPTransport", transport
    )
    container = RuntimeContainer(runtime_settings)
    await container.start()
    try:
        session = await container.sessions.create_session("ayachi_nene")
        provider = _MissingFirstProvider() if use_correction else _Provider()
        expected_rounds = 3 if use_correction else 2
        evaluation = RuntimeSourceEvaluation(
            provider,
            container.runtime_skills,
            trace_path=tmp_path / "rounds.jsonl",
            max_provider_requests=expected_rounds,
            allow_once=allow_once,
            dns_resolver="system",
        )
        request = LlmRequest(
            uuid4(),
            "请核查这个网页来源 https://source.example/",
            "Persona",
            tool_decision_system_prompt="TRUSTED_SAFETY_AND_FROZEN_CLOCK",
            input_budget=LlmInputBudget(7292),
        )
        outcome = await evaluation.run(
            request, session_id=session.session_id, turn_id=uuid4(), sample_key="one"
        )
        assert outcome.reply_origin == ("provider" if allow_once else "runtime_fallback")
        if not allow_once:
            assert "Actual source result" not in outcome.reply
            assert "尚未核实" in outcome.reply
        assert outcome.usage == (
            LlmUsage(35, 6, 41, None) if use_correction else LlmUsage(30, 5, 35, None)
        )
        assert len(provider.requests) == expected_rounds
        assert provider.requests[0].system_prompt.startswith("TRUSTED_SAFETY_AND_FROZEN_CLOCK")
        assert "dns_resolver=system" in provider.requests[0].system_prompt
        assert "TRUSTED_SAFETY_AND_FROZEN_CLOCK" not in provider.requests[-1].system_prompt
        assert "Unverified source claim" not in outcome.reply
        result = provider.requests[-1].tool_exchanges[0].results[0]
        assert isinstance(result.content, dict)
        assert result.content["ok"] is allow_once
        assert len(requests) == int(allow_once)
        assert outcome.trace["permission_policy"] == ("allow_once" if allow_once else "deny")
        assert len(outcome.trace["provider_calls"]) == expected_rounds
        assert all(
            call["input_budget"]["estimated_token_limit"] == 7292
            and call["input_budget_report"]["estimated_input_tokens"] <= 7292
            for call in outcome.trace["provider_calls"]
        )
        assert outcome.trace["tool_runs"][0]["state"] == ("succeeded" if allow_once else "failed")
        allowed = RuntimeSkillRouter(
            lambda: [
                skill
                for skill in container.runtime_skills.list()
                if skill.skill_id in {"web.read", "web.search"}
            ]
        ).select(request.user_text)
        assert {tool.name for tool in provider.requests[0].tools} == {tool.name for tool in allowed}
        assert await container.runtime_skills.pending_confirmations(session.session_id) == []
        assert await container.database.fetchall("SELECT * FROM permission_grants") == []
        journal = [
            json.loads(line)
            for line in (tmp_path / "rounds.jsonl").read_text(encoding="utf-8").splitlines()
        ]
        assert [row["event"] for row in journal] == ["started", "finished"] * expected_rounds
        # A fresh helper must account for paid rounds even after a resume/restart.
        resumed = RuntimeSourceEvaluation(
            provider,
            container.runtime_skills,
            trace_path=tmp_path / "rounds.jsonl",
            max_provider_requests=expected_rounds,
            allow_once=allow_once,
            dns_resolver="system",
        )
        with pytest.raises(ProviderRequestLimit):
            await resumed.run(
                request, session_id=session.session_id, turn_id=uuid4(), sample_key="two"
            )
        assert len(provider.requests) == expected_rounds
        followup = RuntimeSourceEvaluation(
            provider,
            container.runtime_skills,
            trace_path=tmp_path / "followup.jsonl",
            max_provider_requests=1,
            allow_once=allow_once,
        )
        outcome = await followup.run(
            LlmRequest(
                uuid4(), "把刚才的条件整理成简表。", "Persona", input_budget=LlmInputBudget(7292)
            ),
            session_id=session.session_id,
            turn_id=uuid4(),
            sample_key="followup",
            source_generation_ids=(request.generation_id,),
        )
        context = "\n".join(text for _role, text in provider.requests[-1].context)
        assert outcome.trace["tool_runs"] == []
        assert len(outcome.trace["source_context"]["receipts"]) == 1
        assert (
            outcome.trace["source_context"]["receipts"][0]["original_result_available"]
            is allow_once
        )
        if allow_once:
            assert "Actual body" in context
        else:
            packet = json.loads(
                next(
                    text
                    for _role, text in provider.requests[-1].context
                    if text.startswith("[PUBLIC SOURCE DATA]\n")
                ).split("\n", 1)[1]
            )
            assert packet["receipts"][0]["error_code"] == "permission_denied"
            assert packet["receipts"][0]["original_result"] == "not_succeeded"
        assert len(requests) == int(allow_once)
        assert await container.database.fetchall("SELECT * FROM permission_grants") == []
    finally:
        await container.stop()


@pytest.mark.asyncio
async def test_source_loading_keeps_single_turn_guard_and_cancel_releases_it(
    runtime_settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    container = RuntimeContainer(runtime_settings)
    await container.start()
    entered, release = asyncio.Event(), asyncio.Event()
    original = container.runtime_skills.load_source_context

    async def paused(session_id: UUID, generation_ids: tuple[UUID, ...]) -> SourceContextPacket:
        entered.set()
        await release.wait()
        return await original(session_id, generation_ids)

    monkeypatch.setattr(container.runtime_skills, "load_source_context", paused)
    try:
        session = await container.sessions.create_session("default")
        provider = _Provider()
        evaluation = RuntimeSourceEvaluation(
            provider,
            container.runtime_skills,
            trace_path=tmp_path / "source-load.jsonl",
            max_provider_requests=1,
            allow_once=False,
        )
        request = LlmRequest(uuid4(), "你好", "Persona", input_budget=LlmInputBudget(7292))
        first = asyncio.create_task(
            evaluation.run(
                request, session_id=session.session_id, turn_id=uuid4(), sample_key="first"
            )
        )
        await asyncio.wait_for(entered.wait(), timeout=2)
        with pytest.raises(RuntimeError, match="one turn at a time"):
            await evaluation.run(
                request, session_id=session.session_id, turn_id=uuid4(), sample_key="second"
            )
        first.cancel()
        with pytest.raises(asyncio.CancelledError):
            await first
        assert provider.requests == []
        monkeypatch.setattr(container.runtime_skills, "load_source_context", original)
        outcome = await evaluation.run(
            request, session_id=session.session_id, turn_id=uuid4(), sample_key="recovery"
        )
        assert outcome.reply_origin == "provider" and len(provider.requests) == 1
    finally:
        await container.stop()


class _IncompleteProvider:
    kind = "scripted"
    supports_tool_calling = False

    async def stream(self, request: LlmRequest) -> AsyncIterator[LlmStreamEvent]:
        yield LlmTextDelta("partial response")


class _SearchThenReadProvider(_Provider):
    async def stream(self, request: LlmRequest) -> AsyncIterator[LlmStreamEvent]:
        self.requests.append(request)
        if not request.tool_exchanges:
            tool = next(t for t in request.tools if t.input_schema.get("required") == ["query"])
            yield LlmToolCallRequested(LlmToolCall("search-source", tool.name, {"query": "topic"}))
            yield LlmResponseCompleted("tool_calls", LlmUsage(10, 2, 12))
        elif len(request.tool_exchanges) == 1:
            search_result = request.tool_exchanges[0].results[0].content
            assert isinstance(search_result, dict) and search_result["ok"] is True
            data = search_result["data"]
            assert isinstance(data, dict)
            results = data["results"]
            assert isinstance(results, list) and isinstance(results[0], dict)
            tool = next(t for t in request.tools if t.input_schema.get("required") == ["url"])
            yield LlmToolCallRequested(
                LlmToolCall("read-source", tool.name, {"url": results[0]["url"]})
            )
            yield LlmResponseCompleted("tool_calls", LlmUsage(20, 2, 22))
        else:
            yield LlmTextDelta("Read the actual original source, not the search snippet.")
            yield LlmResponseCompleted("stop", LlmUsage(30, 5, 35))


@pytest.mark.asyncio
async def test_real_search_registration_confirmation_and_original_read_chain(
    runtime_settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    requests: list[httpx2.Request] = []

    async def resolve(
        *args: object, **kwargs: object
    ) -> list[tuple[int, int, int, str, tuple[str, int]]]:
        return [(2, 1, 6, "", ("93.184.216.34", 443))]

    def transport(endpoint: ValidatedMcpEndpoint) -> httpx2.AsyncBaseTransport:
        def handle(request: httpx2.Request) -> httpx2.Response:
            requests.append(request)
            if request.url.host == "lite.duckduckgo.com":
                body = (
                    '<a class="result-link" href="https://source.example/">Original</a>'
                    '<td class="result-snippet">Snippet is not the original body</td>'
                )
                return httpx2.Response(200, headers={"content-type": "text/html"}, text=body)
            return httpx2.Response(
                200, headers={"content-type": "text/plain"}, text="Actual original body"
            )

        return httpx2.MockTransport(handle)

    monkeypatch.setattr(asyncio.get_running_loop(), "getaddrinfo", resolve)
    monkeypatch.setattr(
        "chatwaifu_runtime.runtime_skills.public_web.PinnedAsyncHTTPTransport", transport
    )
    container = RuntimeContainer(runtime_settings)
    await container.start()
    try:
        session = await container.sessions.create_session("ayachi_nene")
        provider = _SearchThenReadProvider()
        evaluation = RuntimeSourceEvaluation(
            provider,
            container.runtime_skills,
            trace_path=tmp_path / "rounds.jsonl",
            max_provider_requests=3,
            allow_once=True,
        )
        outcome = await evaluation.run(
            LlmRequest(uuid4(), "请搜索并核查网页来源", "Persona"),
            session_id=session.session_id,
            turn_id=uuid4(),
            sample_key="one",
        )
        assert outcome.usage == LlmUsage(60, 9, 69)
        assert [request.url.host for request in requests] == [
            "lite.duckduckgo.com",
            "source.example",
        ]
        assert [run["skill_id"] for run in outcome.trace["tool_runs"]] == ["web.search", "web.read"]
        assert all(run["state"] == "succeeded" for run in outcome.trace["tool_runs"])
        result = provider.requests[2].tool_exchanges[1].results[0].content
        assert isinstance(result, dict)
        data = result["data"]
        assert isinstance(data, dict) and data["text"] == "Actual original body"
        persisted = str(await container.database.fetchall("SELECT result_json FROM skill_runs"))
        assert "Actual original body" not in persisted
        assert await container.database.fetchall("SELECT * FROM permission_grants") == []
    finally:
        await container.stop()


class _UnsupportedProvider(_IncompleteProvider):
    supports_tool_calling = True

    async def stream(self, request: LlmRequest) -> AsyncIterator[LlmStreamEvent]:
        raise LlmToolCallingUnavailableError("private endpoint detail must not be saved")
        yield LlmTextDelta("unreachable")


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["incomplete", "unsupported"])
async def test_missing_terminal_and_runtime_fallback_are_not_fake_provider_success(
    runtime_settings: Settings,
    tmp_path: Path,
    mode: str,
) -> None:
    container = RuntimeContainer(runtime_settings)
    await container.start()
    try:
        session = await container.sessions.create_session("ayachi_nene")
        provider = _IncompleteProvider() if mode == "incomplete" else _UnsupportedProvider()
        evaluation = RuntimeSourceEvaluation(
            provider,
            container.runtime_skills,
            trace_path=tmp_path / "rounds.jsonl",
            max_provider_requests=2,
            allow_once=True,
        )
        request = LlmRequest(uuid4(), "请核查网页来源", "Persona")
        if mode == "incomplete":
            with pytest.raises(RuntimeMissingTerminal):
                await evaluation.run(
                    request, session_id=session.session_id, turn_id=uuid4(), sample_key="one"
                )
        else:
            outcome = await evaluation.run(
                request, session_id=session.session_id, turn_id=uuid4(), sample_key="one"
            )
            assert outcome.reply_origin == "runtime_fallback"
            assert outcome.finish_reason == "other"
            assert outcome.usage is None
            assert outcome.trace["provider_calls"][0]["finish_reason"] is None
        assert "private endpoint detail" not in (tmp_path / "rounds.jsonl").read_text(
            encoding="utf-8"
        )
    finally:
        await container.stop()


class _PartialUsageProvider(_Provider):
    async def stream(self, request: LlmRequest) -> AsyncIterator[LlmStreamEvent]:
        async for event in super().stream(request):
            if isinstance(event, LlmResponseCompleted) and request.tool_exchanges:
                yield LlmResponseCompleted("stop", LlmUsage(20, None, None, None))
            else:
                yield event


@pytest.mark.asyncio
async def test_missing_round_usage_stays_unknown_and_never_becomes_zero(
    runtime_settings: Settings,
    tmp_path: Path,
) -> None:
    container = RuntimeContainer(runtime_settings)
    await container.start()
    try:
        session = await container.sessions.create_session("ayachi_nene")
        evaluation = RuntimeSourceEvaluation(
            _PartialUsageProvider(),
            container.runtime_skills,
            trace_path=tmp_path / "rounds.jsonl",
            max_provider_requests=2,
            allow_once=False,
        )
        outcome = await evaluation.run(
            LlmRequest(uuid4(), "请核查网页来源", "Persona"),
            session_id=session.session_id,
            turn_id=uuid4(),
            sample_key="one",
        )
        assert outcome.usage == LlmUsage(30, None, None, None)
        assert outcome.trace["provider_calls"][0]["usage"]["completion_tokens"] == 2
        assert outcome.trace["provider_calls"][1]["usage"]["completion_tokens"] is None
    finally:
        await container.stop()


@pytest.mark.asyncio
async def test_runtime_eval_cancellation_closes_source_and_terminalizes_run(
    runtime_settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    entered, closed = asyncio.Event(), asyncio.Event()

    async def resolve(
        *args: object, **kwargs: object
    ) -> list[tuple[int, int, int, str, tuple[str, int]]]:
        return [(2, 1, 6, "", ("93.184.216.34", 443))]

    def transport(endpoint: ValidatedMcpEndpoint) -> httpx2.AsyncBaseTransport:
        async def handle(request: httpx2.Request) -> httpx2.Response:
            entered.set()
            try:
                await asyncio.Future()
            finally:
                closed.set()
            raise AssertionError("unreachable")

        return httpx2.MockTransport(handle)

    monkeypatch.setattr(asyncio.get_running_loop(), "getaddrinfo", resolve)
    monkeypatch.setattr(
        "chatwaifu_runtime.runtime_skills.public_web.PinnedAsyncHTTPTransport", transport
    )
    container = RuntimeContainer(runtime_settings)
    await container.start()
    task: asyncio.Task[object] | None = None
    try:
        session = await container.sessions.create_session("ayachi_nene")
        evaluation = RuntimeSourceEvaluation(
            _Provider(),
            container.runtime_skills,
            trace_path=tmp_path / "rounds.jsonl",
            max_provider_requests=2,
            allow_once=True,
        )
        task = asyncio.create_task(
            evaluation.run(
                LlmRequest(uuid4(), "请核查网页来源", "Persona"),
                session_id=session.session_id,
                turn_id=uuid4(),
                sample_key="one",
            )
        )
        await asyncio.wait_for(entered.wait(), 2)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert closed.is_set()
        runs = await container.runtime_skills.list_runs(session.session_id)
        assert len(runs) == 1 and runs[0].state.value == "cancelled"
        assert evaluation.last_trace is not None
        assert evaluation.last_trace["tool_runs"][0]["state"] == "cancelled"
    finally:
        if task is not None and not task.done():
            task.cancel()
        await container.stop()
