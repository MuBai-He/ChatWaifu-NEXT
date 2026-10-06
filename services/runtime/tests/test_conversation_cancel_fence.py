"""Late provider output cannot revive a cancelled, still-owned generation."""

# pyright: reportPrivateUsage=false
from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Awaitable
from dataclasses import replace
from typing import Literal
from uuid import UUID, uuid4

import pytest
from chatwaifu_protocol.events import EventModel
from chatwaifu_protocol.session import ConversationState, GenerationState
from chatwaifu_runtime.bootstrap.container import RuntimeContainer
from chatwaifu_runtime.config.settings import Settings
from chatwaifu_runtime.conversation.models import ConversationTurnOptions, GenerationAccepted
from chatwaifu_runtime.providers.contracts import (
    LlmProvider,
    LlmRequest,
    LlmResponseCompleted,
    LlmStreamEvent,
    LlmTextDelta,
)
from chatwaifu_runtime.providers.model_config import ModelRoleConfig

from services.runtime.tests.test_conversation_shared_external import (
    bind_provider,
    options_for,
    scene_sessions,
)

Origin = Literal["private", "shared", "proactive"]
Cancellation = Literal["cancel", "request", "reset", "stop", "supersede"]


class LateProvider:
    kind = "demo"
    supports_tool_calling = True

    def __init__(self, *, uncancel: bool) -> None:
        self.uncancel = uncancel
        self.entered = asyncio.Event()
        self.cancelled = asyncio.Event()
        self.release = asyncio.Event()
        self.requests: list[LlmRequest] = []

    async def stream(self, request: LlmRequest) -> AsyncIterator[LlmStreamEvent]:
        self.requests.append(request)
        if len(self.requests) == 1:
            # A real first delta proves the cancellation occurs inside streaming,
            # rather than inside preparation or before the model is invoked.
            yield LlmTextDelta("already streamed")
            self.entered.set()
            try:
                await self.release.wait()
            except asyncio.CancelledError:
                self.cancelled.set()
                task = asyncio.current_task()
                assert task is not None
                if self.uncancel:
                    while task.cancelling():
                        task.uncancel()
                yield LlmTextDelta("LATE_AFTER_CANCEL")
                yield LlmResponseCompleted("stop")
            else:
                yield LlmTextDelta("normal completion")
                yield LlmResponseCompleted("stop")
        else:
            yield LlmTextDelta("replacement answer")
            yield LlmResponseCompleted("stop")


async def join_generation(container: RuntimeContainer, session_id: UUID) -> None:
    active = container.conversation._active.get(session_id)
    if active is not None and active.task is not None:
        await asyncio.wait_for(active.task, 2)


@pytest.mark.parametrize("origin", ["private", "shared", "proactive"])
@pytest.mark.parametrize("mode", ["cancel", "request", "reset", "stop", "supersede"])
@pytest.mark.parametrize("uncancel", [False, True])
async def test_running_provider_late_output_is_fenced_and_terminal_cleanup_keeps_ownership(
    runtime_settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
    origin: Origin,
    mode: Cancellation,
    uncancel: bool,
) -> None:
    container = RuntimeContainer(runtime_settings)
    provider = LateProvider(uncancel=uncancel)

    def create(_: ModelRoleConfig) -> LlmProvider:
        return provider

    monkeypatch.setattr(container.model_configurations, "create_chat_provider", create)
    await container.start()
    events: list[dict[str, object]] = []

    def observed(event: dict[str, object]) -> bool:
        if str(event.get("event_type", "")).startswith("assistant."):
            events.append(dict(event))
        return False

    subscription = container.event_hub.subscribe(observed, queue_size=1)
    try:
        if origin == "shared":
            session, _ = await scene_sessions(container)
            options = await options_for(container, session)
        else:
            session = await container.sessions.create_session("default")
            options = ConversationTurnOptions(output_modes=frozenset({"text"}), allow_tools=False)
        if origin == "proactive":
            first = await container.conversation.submit_proactive(
                session.session_id, options=options
            )
        else:
            first = await container.conversation.submit_text(
                session.session_id, "first input", options=options
            )
        await asyncio.wait_for(provider.entered.wait(), 2)
        owned = container.conversation._active[session.session_id].task
        assert owned is not None
        replacement: GenerationAccepted | None = None
        if mode == "cancel":
            assert await asyncio.wait_for(
                container.conversation.cancel(
                    session.session_id, expected_generation_id=first.generation_id
                ),
                2,
            )
        elif mode == "request":
            assert container.conversation.request_cancel(
                session.session_id,
                expected_generation_id=first.generation_id,
                reason="route_revoked",
            )
            assert container.conversation.active_generation_id(session.session_id) is None
        elif mode == "reset":
            await asyncio.wait_for(container.conversation.reset(session.session_id), 2)
        elif mode == "stop":
            await asyncio.wait_for(container.conversation.stop(), 2)
        else:
            replacement = await asyncio.wait_for(
                container.conversation.submit_text(
                    session.session_id,
                    "replacement input",
                    options=replace(options, origin="local_text")
                    if origin == "proactive"
                    else options,
                ),
                2,
            )
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(owned, 2)
        assert provider.cancelled.is_set()
        if replacement is not None:
            await join_generation(container, session.session_id)
            new = await container.conversation_repository.generation_result(
                replacement.generation_id
            )
            assert new is not None and new.state is GenerationState.COMPLETED
        old = await container.conversation_repository.generation_result(first.generation_id)
        if mode == "reset":
            assert old is None
        else:
            assert old is not None and old.state is GenerationState.CANCELLED
            assert old.output_text is None
        old_events = [
            event for event in events if event.get("generation_id") == str(first.generation_id)
        ]
        assert not any(
            event["event_type"] == "assistant.generation_completed" for event in old_events
        )
        assert (
            sum(event["event_type"] == "assistant.generation_cancelled" for event in old_events)
            == 1
        )
        assert all("LATE_AFTER_CANCEL" not in str(event.get("payload")) for event in old_events)
        current = await container.sessions.get_session(session.session_id)
        assert current is not None and current.conversation_state is ConversationState.IDLE
        assert container.conversation.active_generation_id(session.session_id) is None
        assert container.conversation.active_count == 0
    finally:
        subscription.close()
        await container.stop()


async def test_mismatched_generation_cancellation_does_not_revoke_owned_work(
    runtime_settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    container = RuntimeContainer(runtime_settings)
    provider = LateProvider(uncancel=False)

    def create(_: ModelRoleConfig) -> LlmProvider:
        return provider

    monkeypatch.setattr(container.model_configurations, "create_chat_provider", create)
    await container.start()
    try:
        session, _ = await scene_sessions(container)
        accepted = await container.conversation.submit_text(
            session.session_id, "normal input", options=await options_for(container, session)
        )
        await asyncio.wait_for(provider.entered.wait(), 2)
        wrong_id = uuid4()
        assert not container.conversation.request_cancel(
            session.session_id, expected_generation_id=wrong_id, reason="unrelated_generation"
        )
        assert not await container.conversation.cancel(
            session.session_id, expected_generation_id=wrong_id
        )
        provider.release.set()
        await join_generation(container, session.session_id)
        result = await container.conversation_repository.generation_result(accepted.generation_id)
        assert result is not None and result.state is GenerationState.COMPLETED
        assert not provider.cancelled.is_set()
    finally:
        provider.release.set()
        await container.stop()


async def test_completion_publication_barrier_does_not_get_revoked(
    runtime_settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    container = RuntimeContainer(runtime_settings)
    await container.start()
    bind_provider(container, monkeypatch)
    entered, release = asyncio.Event(), asyncio.Event()
    original = container.event_publisher.publish_persisted

    async def blocked_publish(event: EventModel) -> None:
        if event.event_type == "assistant.generation_completed":
            entered.set()
            await release.wait()
        await original(event)

    monkeypatch.setattr(container.event_publisher, "publish_persisted", blocked_publish)
    cancellation: asyncio.Task[bool] | None = None
    try:
        session, _ = await scene_sessions(container)
        accepted = await container.conversation.submit_text(
            session.session_id, "complete input", options=await options_for(container, session)
        )
        await asyncio.wait_for(entered.wait(), 2)
        assert not container.conversation.request_cancel(
            session.session_id, expected_generation_id=accepted.generation_id, reason="too_late"
        )
        owned = container.conversation._active[session.session_id].task
        assert owned is not None
        joined = asyncio.Event()
        original_shield = asyncio.shield

        def observed_shield[T](awaitable: Awaitable[T]) -> asyncio.Future[T]:
            if awaitable is owned:
                joined.set()
            return original_shield(awaitable)

        monkeypatch.setattr(asyncio, "shield", observed_shield)
        cancellation = asyncio.create_task(
            container.conversation.cancel(
                session.session_id, expected_generation_id=accepted.generation_id
            )
        )
        await asyncio.wait_for(joined.wait(), 2)
        assert not cancellation.done()
        release.set()
        assert not await asyncio.wait_for(cancellation, 2)
        await join_generation(container, session.session_id)
        result = await container.conversation_repository.generation_result(accepted.generation_id)
        assert result is not None and result.state is GenerationState.COMPLETED
        assert container.conversation.active_count == 0
        current = await container.sessions.get_session(session.session_id)
        assert current is not None and current.conversation_state is ConversationState.IDLE
    finally:
        release.set()
        if cancellation is not None:
            await asyncio.gather(cancellation, return_exceptions=True)
        await container.stop()


@pytest.mark.parametrize("second", ["cancel", "request", "stop"])
async def test_second_cancel_joins_durable_cleanup_after_provider_uncancel(
    runtime_settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
    second: Literal["cancel", "request", "stop"],
) -> None:
    container = RuntimeContainer(runtime_settings)
    provider = LateProvider(uncancel=True)

    def create(_: ModelRoleConfig) -> LlmProvider:
        return provider

    monkeypatch.setattr(container.model_configurations, "create_chat_provider", create)
    await container.start()
    entered, release = asyncio.Event(), asyncio.Event()
    original = container.event_publisher.publish_persisted

    async def blocked_publish(event: EventModel) -> None:
        if event.event_type == "assistant.generation_cancelled":
            entered.set()
            await release.wait()
        await original(event)

    monkeypatch.setattr(container.event_publisher, "publish_persisted", blocked_publish)
    follower: asyncio.Task[object] | None = None
    try:
        session, _ = await scene_sessions(container)
        accepted = await container.conversation.submit_text(
            session.session_id, "normal input", options=await options_for(container, session)
        )
        await asyncio.wait_for(provider.entered.wait(), 2)
        owned = container.conversation._active[session.session_id].task
        assert owned is not None
        assert container.conversation.request_cancel(
            session.session_id, expected_generation_id=accepted.generation_id, reason="first_cancel"
        )
        await asyncio.wait_for(entered.wait(), 2)
        assert provider.cancelled.is_set() and owned.cancelling() == 0
        if second == "request":
            assert container.conversation.request_cancel(
                session.session_id,
                expected_generation_id=accepted.generation_id,
                reason="repeat_cancel",
            )
        else:

            async def join_revocation() -> object:
                if second == "cancel":
                    return await container.conversation.cancel(
                        session.session_id, expected_generation_id=accepted.generation_id
                    )
                await container.conversation.stop()
                return None

            follower = asyncio.create_task(join_revocation())
        if follower is not None:
            # Task creation enqueues the follower first; this explicit loop event
            # observes its next yield without timing sleeps or cancelling it.
            yielded = asyncio.Event()
            asyncio.get_running_loop().call_soon(yielded.set)
            await asyncio.wait_for(yielded.wait(), 2)
            assert not follower.done()
        assert owned.cancelling() == 0
        release.set()
        if follower is not None:
            await asyncio.wait_for(follower, 2)
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(owned, 2)
        result = await container.conversation_repository.generation_result(accepted.generation_id)
        assert result is not None and result.state is GenerationState.CANCELLED
        row = await container.database.fetchone(
            "SELECT COUNT(*) AS n, SUM(CASE WHEN o.published_at IS NULL THEN 1 ELSE 0 END) "
            "AS pending FROM events AS e JOIN outbox AS o ON o.event_id=e.event_id WHERE "
            "e.event_type IN ('assistant.generation_cancelled','conversation.interrupted') "
            "AND json_extract(e.envelope_json,'$.generation_id')=?",
            (str(accepted.generation_id),),
        )
        assert row is not None and int(row["n"]) == 2 and int(row["pending"]) == 0
        assert container.conversation.active_count == 0
    finally:
        release.set()
        if follower is not None:
            await asyncio.gather(follower, return_exceptions=True)
        await container.stop()
