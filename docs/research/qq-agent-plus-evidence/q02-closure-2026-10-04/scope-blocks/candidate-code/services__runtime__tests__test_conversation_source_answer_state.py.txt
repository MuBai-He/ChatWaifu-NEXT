"""Actual Conversation publication, eligibility and teardown own coverage."""

# pyright: reportPrivateUsage=false
from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from dataclasses import replace
from typing import Any, cast
from uuid import UUID

import pytest
from chatwaifu_protocol.session import GenerationState
from chatwaifu_runtime.agent.source_answer_state import (
    SourceAnswerOriginal,
    supplied_source_context,
)
from chatwaifu_runtime.bootstrap.container import RuntimeContainer
from chatwaifu_runtime.character_kernel.prompt import PromptCompilation
from chatwaifu_runtime.config.settings import Settings
from chatwaifu_runtime.conversation.models import (
    REDACTED_ASSISTANT_PLACEHOLDER,
    ConversationHistoryEntry,
    ConversationTurnOptions,
    GenerationAccepted,
)
from chatwaifu_runtime.providers.contracts import (
    LlmProvider,
    LlmRequest,
    LlmResponseCompleted,
    LlmStreamEvent,
    LlmTextDelta,
)
from chatwaifu_runtime.providers.model_config import ModelRole, ModelRoleConfig

_SOURCE = SourceAnswerOriginal("https://example.org/notice", "a" * 64, "Limit two; scope A.")
_GAP = "Later changes and other operators remain unverified."
_QUESTION = "只依据提供的资料回答，不要联网。"
_FOLLOWUP = "将这份资料整理成三条清单，保留适用范围。"


class _Provider:
    kind = "controlled"
    supports_tool_calling = False
    supports_response_schema = True

    def __init__(self) -> None:
        self.requests: list[LlmRequest] = []
        self.invalid = False
        self.frames = 0

    async def stream(self, request: LlmRequest) -> AsyncIterator[LlmStreamEvent]:
        self.requests.append(request)
        if request.response_schema is None:
            yield LlmTextDelta("ordinary reply")
        elif self.invalid:
            yield LlmTextDelta("{private broken output")
        else:
            self.frames += 1
            yield LlmTextDelta(
                json.dumps(
                    {
                        "version": "1.2",
                        "blocks": [
                            {
                                "kind": "paragraph",
                                "text": "Limit two; scope A.",
                                "source_indices": [0],
                                "prior_gap_ids": [],
                                "unresolved_scope": False,
                            }
                        ]
                        + (
                            [
                                {
                                    "kind": "paragraph",
                                    "text": _GAP,
                                    "source_indices": [0],
                                    "prior_gap_ids": [],
                                    "unresolved_scope": True,
                                }
                            ]
                            if self.frames == 1
                            else []
                        ),
                    }
                )
            )
        yield LlmResponseCompleted("stop")


async def _container(
    settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
    *,
    enabled: bool = True,
) -> tuple[RuntimeContainer, _Provider]:
    container = RuntimeContainer(settings, source_answer_frames=enabled)
    await container.start()
    provider = _Provider()
    original_compile = container.prompt_compiler.compile

    async def compile_with_material(*args: Any, **kwargs: Any) -> PromptCompilation:
        compiled = await original_compile(*args, **kwargs)
        return replace(compiled, context=(*compiled.context, supplied_source_context((_SOURCE,))))

    monkeypatch.setattr(container.prompt_compiler, "compile", compile_with_material)

    def create_provider(_: ModelRoleConfig) -> LlmProvider:
        return cast(LlmProvider, provider)

    monkeypatch.setattr(container.model_configurations, "create_chat_provider", create_provider)
    return container, provider


async def _turn(
    container: RuntimeContainer,
    session: UUID,
    text: str,
    options: ConversationTurnOptions | None = None,
) -> GenerationAccepted:
    accepted = await container.conversation.submit_text(
        session,
        text,
        options=options or ConversationTurnOptions(output_modes=frozenset({"text"})),
    )
    task = container.conversation._active[session].task
    assert task is not None
    await asyncio.wait_for(task, timeout=5)
    return accepted


def _prior(request: LlmRequest) -> list[dict[str, object]]:
    for role, text in request.context:
        if role == "user" and text.startswith("[SOURCE ANSWER FRAME DATA]\n"):
            return json.loads(text.split("\n", 1)[1])["prior_gaps"]
    return []


async def test_completed_conversation_retains_actual_frame_and_marks_rendered_origin(
    runtime_settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    container, provider = await _container(runtime_settings, monkeypatch)
    try:
        session = (await container.sessions.create_session("default")).session_id
        first = await _turn(container, session, _QUESTION)
        assert (
            session,
            first.generation_id,
        ) in container.conversation._source_answer_coverage._records
        await _turn(container, session, _FOLLOWUP)
        assert _prior(provider.requests[3])[0]["statement"] == _GAP
        messages = await container.conversation.list_messages(session)
        assert _GAP in str(messages[-1]["committed_text"])
        rows = await container.database.fetchall(
            "SELECT payload_json FROM events WHERE event_type = 'assistant.generation_completed'"
        )
        assert all(
            json.loads(str(row["payload_json"]))["reply_origin"] == "provider_frame_rendered"
            for row in rows
        )
    finally:
        await container.stop()


@pytest.mark.parametrize("mode", ["reset", "stop", "redacted", "model", "foreign_session"])
async def test_completed_coverage_cannot_cross_reset_stop_redaction_route_or_session(
    runtime_settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
    mode: str,
) -> None:
    container, provider = await _container(runtime_settings, monkeypatch)
    try:
        session = (await container.sessions.create_session("default")).session_id
        first = await _turn(container, session, _QUESTION)
        if mode == "reset":
            await container.conversation.reset(session)
        elif mode == "stop":
            await container.conversation.stop()
        elif mode == "redacted":
            prepare = container.conversation_repository.prepare_history

            async def redact(generation: UUID, history: tuple[ConversationHistoryEntry, ...]):
                prepared = await prepare(generation, history)
                return tuple(
                    replace(entry, text=REDACTED_ASSISTANT_PLACEHOLDER)
                    if entry.generation_id == first.generation_id
                    else entry
                    for entry in prepared
                )

            monkeypatch.setattr(container.conversation_repository, "prepare_history", redact)
        elif mode == "model":
            get = container.model_configurations.get

            def changed(role: ModelRole) -> ModelRoleConfig:
                config = get(role)
                return (
                    config.model_copy(update={"model": "another-model"})
                    if role == "chat"
                    else config
                )

            monkeypatch.setattr(container.model_configurations, "get", changed)
        else:
            session = (await container.sessions.create_session("default")).session_id
        await _turn(container, session, _FOLLOWUP)
        assert _prior(provider.requests[-1]) == []
    finally:
        await container.stop()


@pytest.mark.parametrize("mode", ["cancel", "failure", "stop_during_commit", "reset_during_commit"])
async def test_provisional_frame_never_becomes_completed_coverage_after_failure_or_stop(
    runtime_settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
    mode: str,
) -> None:
    container, provider = await _container(runtime_settings, monkeypatch)
    entered, release = asyncio.Event(), asyncio.Event()
    try:
        session = (await container.sessions.create_session("default")).session_id
        if mode in {"stop_during_commit", "reset_during_commit"}:
            commit = container.conversation_repository.complete_generation

            async def held_commit(*args: Any, **kwargs: Any):
                entered.set()
                await release.wait()
                return await commit(*args, **kwargs)

            monkeypatch.setattr(
                container.conversation_repository, "complete_generation", held_commit
            )
        else:
            publish = container.event_publisher.publish_ephemeral

            async def held_publish(event: Any):
                if event.event_type == "assistant.text_delta":
                    entered.set()
                    if mode == "failure":
                        raise RuntimeError("controlled publication failure")
                    await release.wait()
                await publish(event)

            monkeypatch.setattr(container.event_publisher, "publish_ephemeral", held_publish)
        accepted = await container.conversation.submit_text(
            session,
            _QUESTION,
            options=ConversationTurnOptions(output_modes=frozenset({"text"})),
        )
        task = container.conversation._active[session].task
        assert task is not None
        await asyncio.wait_for(entered.wait(), timeout=2)
        assert container.conversation._source_answer_coverage._records == {}
        if mode == "cancel":
            await container.conversation.cancel(session)
        elif mode == "stop_during_commit":
            await container.conversation.stop()
            release.set()
        elif mode == "reset_during_commit":
            # Reset observes the existing completion barrier before deletion.
            reset_entered = asyncio.Event()
            cancel = container.conversation.cancel

            async def observed_cancel(*args: Any, **kwargs: Any) -> bool:
                reset_entered.set()
                return await cancel(*args, **kwargs)

            monkeypatch.setattr(container.conversation, "cancel", observed_cancel)
            reset_task = asyncio.create_task(container.conversation.reset(session))
            await asyncio.wait_for(reset_entered.wait(), timeout=2)
            assert not reset_task.done()
            release.set()
            await asyncio.wait_for(reset_task, timeout=5)
        try:
            await asyncio.wait_for(task, timeout=5)
        except asyncio.CancelledError:
            assert mode == "cancel"
        assert container.conversation._source_answer_coverage._records == {}
        record = await container.conversation_repository.generation_result(accepted.generation_id)
        if mode == "reset_during_commit":
            assert record is None
            return
        assert record is not None
        assert (
            record.state
            is {
                "cancel": GenerationState.CANCELLED,
                "failure": GenerationState.FAILED,
                "stop_during_commit": GenerationState.COMPLETED,
            }[mode]
        )
        assert provider.requests[-1].response_schema is not None
    finally:
        release.set()
        await container.stop()


@pytest.mark.parametrize("mode", ["invalid", "unsupported"])
async def test_schema_rejection_is_distinct_safe_failure_without_publication_or_cache(
    runtime_settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
    mode: str,
) -> None:
    container, provider = await _container(runtime_settings, monkeypatch)
    provider.invalid = mode == "invalid"
    if mode == "unsupported":
        provider.supports_response_schema = False
    try:
        session = (await container.sessions.create_session("default")).session_id
        accepted = await _turn(container, session, _QUESTION)
        record = await container.conversation_repository.generation_result(accepted.generation_id)
        assert record is not None and record.state is GenerationState.FAILED
        assert record.error_code == (
            "source_answer_frame_rejected" if mode == "invalid" else "response_schema_unavailable"
        )
        rows = await container.database.fetchall(
            "SELECT payload_json FROM events WHERE event_type = 'system.error_raised'"
        )
        assert "private broken output" not in str(rows)
        assert container.conversation._source_answer_coverage._records == {}
        assert len(provider.requests) == (2 if mode == "invalid" else 0)
    finally:
        await container.stop()


async def test_default_off_conversation_preserves_legacy_path(
    runtime_settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    container, provider = await _container(runtime_settings, monkeypatch, enabled=False)
    try:
        session = (await container.sessions.create_session("default")).session_id
        await _turn(container, session, _QUESTION)
        assert provider.requests[0].response_schema is None
        assert container.conversation._source_answer_coverage._records == {}
    finally:
        await container.stop()
