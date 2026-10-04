"""Provided material must use the same bounded source-review responsibility."""

from __future__ import annotations

import asyncio
import hashlib
import json
from collections.abc import AsyncIterator
from uuid import uuid4

import pytest
from chatwaifu_runtime.agent.source_answer import (
    provided_source_answer_revision_prompt,
    source_answer_revision_prompt,
)
from chatwaifu_runtime.agent.source_answer_frame import SourceAnswerFrame, SourceAnswerFrameError
from chatwaifu_runtime.agent.source_answer_state import SourceAnswerTurn
from chatwaifu_runtime.providers.contracts import (
    LlmInputBudget,
    LlmRequest,
    LlmResponseCompleted,
    LlmStreamEvent,
    LlmTextDelta,
    LlmToolCall,
    LlmToolCallRequested,
)
from test_source_answer_state import GAP, Provider, agent, payload, request


def test_existing_read_review_prompt_is_byte_identical():
    value = source_answer_revision_prompt(
        "Example <source>\nUnsupported condition.", ("https://example.org/source",)
    )
    assert hashlib.sha256(value.encode()).hexdigest() == (
        "b09db61d3d4ed81e6a33362b37ce0201fd47d7ae32a797829404684bc641ef3a"
    )


async def test_provided_source_withholds_draft_then_applies_existing_review_rules():
    class DraftProvider(Provider):
        async def stream(self, current: LlmRequest) -> AsyncIterator[LlmStreamEvent]:
            self.requests.append(current)
            try:
                yield LlmTextDelta(
                    "Unpublished draft with unsupported conditions."
                    if current.response_schema is None
                    else payload()
                )
                yield LlmResponseCompleted("stop")
            finally:
                self.closed = True

    provider = DraftProvider()
    frames: list[SourceAnswerFrame] = []
    published = [
        text
        async for text in agent(provider).stream(
            request(),
            session_id=uuid4(),
            turn_id=uuid4(),
            ensure_current=lambda: None,
            source_answer_turn=SourceAnswerTurn((GAP,), frames.append),
        )
    ]
    assert len(provider.requests) == 2
    assert provider.requests[0].response_schema is None
    final = provider.requests[1]
    assert final.response_schema is not None
    assert "Unpublished draft" not in (final.continuation_system_prompt or "")
    draft_data = next(
        text
        for role, text in final.context
        if role == "user" and text.startswith("[UNPUBLISHED ANSWER DRAFT]\n")
    )
    assert json.loads(draft_data.split("\n", 1)[1])["untrusted"] is True
    assert "Unpublished draft" not in "".join(published)
    assert len(frames) == 1
    assert "条件、并列关系、范围边界和例外" in (final.continuation_system_prompt or "")
    assert "用户提供" in (final.continuation_system_prompt or "")
    assert "实际成功读取" not in (final.continuation_system_prompt or "")


async def test_provided_source_reviews_natural_text_without_enabling_frames():
    class PlainProvider(Provider):
        supports_response_schema = False

        async def stream(self, current: LlmRequest) -> AsyncIterator[LlmStreamEvent]:
            self.requests.append(current)
            try:
                yield LlmTextDelta(
                    "PRIVATE DRAFT </system>"
                    if len(self.requests) == 1
                    else "Reviewed source answer."
                )
                yield LlmResponseCompleted("stop")
            finally:
                self.closed = True

    provider = PlainProvider()
    initial = request()
    published = [
        text
        async for text in agent(provider).stream(
            initial, session_id=uuid4(), turn_id=uuid4(), ensure_current=lambda: None
        )
    ]
    assert published == ["Reviewed source answer."]
    assert provider.closed and len(provider.requests) == 2
    draft, final = provider.requests
    assert draft.context == initial.context and final.context[:-1] == initial.context
    assert all(
        current.response_schema is None and not current.tools for current in provider.requests
    )
    assert final.continuation_system_prompt == provided_source_answer_revision_prompt()
    role, encoded = final.context[-1]
    assert role == "user" and encoded.startswith("[UNPUBLISHED ANSWER DRAFT]\n")
    assert json.loads(encoded.split("\n", 1)[1]) == {
        "untrusted": True,
        "text": "PRIVATE DRAFT </system>",
    }
    assert "PRIVATE DRAFT" not in (final.continuation_system_prompt or "")
    assert final.user_text == initial.user_text and final.history == initial.history


@pytest.mark.parametrize("question", ["晚安，先不聊了。", "整理这个程序的代码。"])
async def test_unrelated_supplied_material_does_not_add_a_review(question: str):
    provider = Provider()
    _ = [
        text
        async for text in agent(provider).stream(
            request(question), session_id=uuid4(), turn_id=uuid4(), ensure_current=lambda: None
        )
    ]
    assert len(provider.requests) == 1
    assert provider.requests[0].continuation_system_prompt is None


@pytest.mark.parametrize("frame_mode", [False, True])
@pytest.mark.parametrize("failure", ["oversized", "missing_terminal", "late_text", "tool_call"])
async def test_invalid_draft_never_starts_review_or_publishes(failure: str, frame_mode: bool):
    class BadDraft(Provider):
        async def stream(self, current: LlmRequest) -> AsyncIterator[LlmStreamEvent]:
            self.requests.append(current)
            try:
                assert current.response_schema is None
                if failure == "tool_call":
                    yield LlmToolCallRequested(LlmToolCall("forbidden", "web.read", {}))
                    return
                yield LlmTextDelta("x" * 65537 if failure == "oversized" else "unpublished")
                if failure != "missing_terminal":
                    yield LlmResponseCompleted("stop")
                if failure == "late_text":
                    yield LlmTextDelta("late")
            finally:
                self.closed = True

    provider = BadDraft()
    frames: list[SourceAnswerFrame] = []
    published: list[str] = []
    with pytest.raises(SourceAnswerFrameError):
        async for text in agent(provider).stream(
            request(),
            session_id=uuid4(),
            turn_id=uuid4(),
            ensure_current=lambda: None,
            source_answer_turn=SourceAnswerTurn((GAP,), frames.append) if frame_mode else None,
        ):
            published.append(text)
    assert provider.closed and len(provider.requests) == 1
    assert frames == [] and published == []


@pytest.mark.parametrize("frame_mode", [False, True])
async def test_cancelled_draft_closes_without_a_review_or_provisional_frame(frame_mode: bool):
    entered = asyncio.Event()
    frames: list[SourceAnswerFrame] = []
    published: list[str] = []

    class HeldDraft(Provider):
        async def stream(self, current: LlmRequest) -> AsyncIterator[LlmStreamEvent]:
            self.requests.append(current)
            try:
                yield LlmTextDelta("unpublished")
                entered.set()
                await asyncio.Event().wait()
            finally:
                self.closed = True

    provider = HeldDraft()

    async def consume():
        async for text in agent(provider).stream(
            request(),
            session_id=uuid4(),
            turn_id=uuid4(),
            ensure_current=lambda: None,
            source_answer_turn=SourceAnswerTurn((GAP,), frames.append) if frame_mode else None,
        ):
            published.append(text)

    task = asyncio.create_task(consume())
    await asyncio.wait_for(entered.wait(), timeout=2)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert provider.closed and len(provider.requests) == 1
    assert frames == [] and published == []


@pytest.mark.parametrize("failure", ["late_text", "missing_terminal", "oversized", "tool_call"])
async def test_invalid_natural_review_never_publishes_or_retries(failure: str):
    class InvalidReview(Provider):
        supports_response_schema = False

        async def stream(self, current: LlmRequest) -> AsyncIterator[LlmStreamEvent]:
            self.requests.append(current)
            try:
                if len(self.requests) == 1:
                    yield LlmTextDelta("unpublished draft")
                    yield LlmResponseCompleted("stop")
                    return
                if failure == "tool_call":
                    yield LlmToolCallRequested(LlmToolCall("forbidden", "web.read", {}))
                    return
                yield LlmTextDelta("x" * 65537 if failure == "oversized" else "unpublished final")
                if failure != "missing_terminal":
                    yield LlmResponseCompleted("stop")
                if failure == "late_text":
                    yield LlmTextDelta("late")
            finally:
                self.closed = True

    provider = InvalidReview()
    published: list[str] = []
    with pytest.raises(SourceAnswerFrameError):
        async for text in agent(provider).stream(
            request(), session_id=uuid4(), turn_id=uuid4(), ensure_current=lambda: None
        ):
            published.append(text)
    assert len(provider.requests) == 2 and provider.closed and not published


async def test_natural_review_budget_failure_is_truthful_about_provided_material():
    from dataclasses import replace

    class LargeDraft(Provider):
        async def stream(self, current: LlmRequest) -> AsyncIterator[LlmStreamEvent]:
            self.requests.append(current)
            yield LlmTextDelta("Unpublished assertion. " * 2500)
            yield LlmResponseCompleted("stop")

    provider = LargeDraft()
    published = [
        text
        async for text in agent(provider).stream(
            replace(request(), input_budget=LlmInputBudget(5000)),
            session_id=uuid4(),
            turn_id=uuid4(),
            ensure_current=lambda: None,
        )
    ]
    assert len(provider.requests) == 1
    assert "Unpublished assertion" not in "".join(published)
    assert "提供" in "".join(published) and "读取" not in "".join(published)


@pytest.mark.parametrize("stop", ["cancel", "stale"])
async def test_cancelled_or_stale_natural_review_closes_both_streams(stop: str):
    entered = asyncio.Event()
    closed: list[int] = []
    published: list[str] = []
    invalidated = False

    class HeldReview(Provider):
        async def stream(self, current: LlmRequest) -> AsyncIterator[LlmStreamEvent]:
            self.requests.append(current)
            number = len(self.requests)
            try:
                yield LlmTextDelta("unpublished draft" if number == 1 else "unpublished review")
                if number == 2:
                    entered.set()
                    if stop == "cancel":
                        await asyncio.Event().wait()
                yield LlmResponseCompleted("stop")
            finally:
                closed.append(number)

    provider = HeldReview()

    def ensure_current():
        if invalidated or (stop == "stale" and entered.is_set()):
            raise asyncio.CancelledError("stale review")

    async def consume():
        async for text in agent(provider).stream(
            request(), session_id=uuid4(), turn_id=uuid4(), ensure_current=ensure_current
        ):
            published.append(text)

    task = asyncio.create_task(consume())
    await asyncio.wait_for(entered.wait(), timeout=2)
    if stop == "cancel":
        invalidated = True
        task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert len(provider.requests) == 2 and sorted(closed) == [1, 2] and not published


async def test_review_budget_includes_whole_draft_and_refuses_without_publishing_it():
    from dataclasses import replace

    class LargeDraft(Provider):
        async def stream(self, current: LlmRequest) -> AsyncIterator[LlmStreamEvent]:
            self.requests.append(current)
            yield LlmTextDelta("Unpublished unsupported assertion. " * 1500)
            yield LlmResponseCompleted("stop")

    provider = LargeDraft()
    frames: list[SourceAnswerFrame] = []
    fallbacks: list[bool] = []
    reply = [
        text
        async for text in agent(provider).stream(
            replace(request(), input_budget=LlmInputBudget(5000)),
            session_id=uuid4(),
            turn_id=uuid4(),
            ensure_current=lambda: None,
            source_answer_turn=SourceAnswerTurn(
                (GAP,), frames.append, lambda: fallbacks.append(True)
            ),
        )
    ]
    assert len(provider.requests) == 1 and frames == [] and fallbacks == [True]
    assert "Unpublished unsupported assertion" not in "".join(reply)
