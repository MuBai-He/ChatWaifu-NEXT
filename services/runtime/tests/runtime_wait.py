"""Race-safe helpers for waiting on Runtime background work in HTTP tests."""

from __future__ import annotations

import asyncio
from typing import cast
from uuid import UUID

from anyio.from_thread import BlockingPortal
from chatwaifu_protocol.session import GenerationState
from chatwaifu_runtime.bootstrap.container import RuntimeContainer
from chatwaifu_runtime.conversation.repository import ConversationGenerationRecord
from fastapi import FastAPI
from fastapi.testclient import TestClient

_TERMINAL_GENERATION_STATES = {
    GenerationState.COMPLETED,
    GenerationState.FAILED,
    GenerationState.CANCELLED,
}
_TEST_DEADLOCK_TIMEOUT_SECONDS = 15.0
_DURABLE_RECHECK_SECONDS = 1.0


def wait_for_generation_terminal(
    client: TestClient,
    generation_id: UUID,
    *,
    timeout: float = _TEST_DEADLOCK_TIMEOUT_SECONDS,
) -> ConversationGenerationRecord:
    """Wait on the app loop without repeatedly contending for the SQLite lock."""

    container, portal = _runtime(client)

    async def wait() -> ConversationGenerationRecord:
        subscription = container.event_hub.subscribe(
            lambda event: str(event.get("generation_id")) == str(generation_id)
        )
        try:
            while True:
                generation = await container.conversation_repository.generation_result(
                    generation_id
                )
                if generation is None:
                    raise AssertionError(f"generation {generation_id} was not persisted")
                if generation.state in _TERMINAL_GENERATION_STATES:
                    return generation
                try:
                    await asyncio.wait_for(subscription.receive(), timeout=_DURABLE_RECHECK_SECONDS)
                except TimeoutError:
                    # A dropped or failed publish cannot hide a committed terminal state.
                    continue
        finally:
            container.event_hub.unsubscribe(subscription)

    future = portal.start_task_soon(wait)
    try:
        return future.result(timeout=timeout)
    except TimeoutError as error:
        future.cancel()
        raise AssertionError(
            f"generation {generation_id} did not reach a persisted terminal state"
        ) from error


def wait_for_skill_terminal(
    client: TestClient,
    run_id: UUID,
    *,
    timeout: float = _TEST_DEADLOCK_TIMEOUT_SECONDS,
) -> dict[str, object]:
    """Use the Runtime Skill service's durable, race-safe terminal waiter."""

    container, portal = _runtime(client)

    async def wait() -> dict[str, object]:
        snapshot = await container.runtime_skills.wait_for_terminal(run_id)
        return cast(dict[str, object], snapshot.model_dump(mode="json"))

    future = portal.start_task_soon(wait)
    try:
        return future.result(timeout=timeout)
    except TimeoutError as error:
        future.cancel()
        raise AssertionError(f"skill run {run_id} did not reach a terminal state") from error


def _runtime(client: TestClient) -> tuple[RuntimeContainer, BlockingPortal]:
    portal = client.portal
    if portal is None:
        raise AssertionError("TestClient portal is unavailable outside its context manager")
    app = cast(FastAPI, client.app)
    return cast(RuntimeContainer, app.state.container), portal
