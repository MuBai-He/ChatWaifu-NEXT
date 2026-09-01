"""Failure-atomic Runtime startup and best-effort teardown regression tests."""

import asyncio
from types import SimpleNamespace
from typing import Any, cast
from uuid import uuid4

import pytest
from chatwaifu_runtime.bootstrap.container import (
    RuntimeCleanupError,
    RuntimeContainer,
    RuntimeLifecycleError,
)
from chatwaifu_runtime.config.settings import Settings
from chatwaifu_runtime.conversation.service import ConversationService
from chatwaifu_runtime.main import create_app


@pytest.mark.asyncio
async def test_conversation_cancel_does_not_swallow_its_callers_cancellation() -> None:
    service = object.__new__(ConversationService)
    session_id = uuid4()
    generation_id = uuid4()
    cancellation_seen = asyncio.Event()
    keep_cancelling = asyncio.Event()

    async def active_generation() -> None:
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            cancellation_seen.set()
            await keep_cancelling.wait()
            raise

    generation_task = asyncio.create_task(active_generation())
    cast(Any, service)._active = {
        session_id: SimpleNamespace(
            generation_id=generation_id,
            task=generation_task,
            completing=False,
        )
    }
    cancelling = asyncio.create_task(service.cancel(session_id, "test cancellation"))
    await cancellation_seen.wait()
    cancelling.cancel()

    with pytest.raises(asyncio.CancelledError):
        await cancelling
    assert generation_task.done()


@pytest.mark.asyncio
async def test_late_start_failure_closes_every_owned_component_and_is_terminal(
    runtime_settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    container = RuntimeContainer(runtime_settings)
    closed: list[str] = []

    async def fail_pending_outbox(*_args: object, **_kwargs: object) -> list[object]:
        raise RuntimeError("injected late outbox startup failure")

    for name, owner, method_name in (
        ("ambient", container.ambient, "stop"),
        ("resources", container.resources, "stop"),
        ("voice_media", container.voice_media, "close"),
        ("conversation", container.conversation, "stop"),
        ("runtime_skills", container.runtime_skills, "stop"),
        ("memory", container.memory, "stop"),
        ("stt", container.stt, "close"),
        ("tts", container.providers.tts, "close"),
        ("audio_streams", container.audio_streams, "close"),
        ("event_hub", container.event_hub, "close"),
        ("database", container.database, "close"),
    ):
        original = getattr(owner, method_name)

        async def observe_close(_original: object = original, _name: str = name) -> None:
            closed.append(_name)
            await _original()  # type: ignore[operator]

        monkeypatch.setattr(owner, method_name, observe_close)
    monkeypatch.setattr(container.event_store, "pending_outbox", fail_pending_outbox)

    with pytest.raises(RuntimeError, match="late outbox"):
        await container.start()

    assert closed == [
        "ambient",
        "resources",
        "voice_media",
        "conversation",
        "runtime_skills",
        "memory",
        "stt",
        "tts",
        "audio_streams",
        "event_hub",
        "database",
    ]
    with pytest.raises(RuntimeError, match="database is not open"):
        await container.database.fetchone("SELECT 1")
    assert container.memory.projection_running is False
    with pytest.raises(RuntimeError, match="event hub is closed"):
        container.event_hub.subscribe()
    with pytest.raises(RuntimeError, match="audio stream hub is closed"):
        container.audio_streams.subscribe(__import__("uuid").uuid4())
    with pytest.raises(RuntimeError, match="terminal"):
        await container.start()
    await container.stop()


@pytest.mark.asyncio
async def test_stop_closes_constructor_owned_resources_before_start(
    runtime_settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    container = RuntimeContainer(runtime_settings)
    closed: list[str] = []
    original_tts_close = container.providers.tts.close

    async def observe_tts_close() -> None:
        closed.append("tts")
        await original_tts_close()

    monkeypatch.setattr(container.providers.tts, "close", observe_tts_close)
    await container.stop()
    assert closed == ["tts"]
    with pytest.raises(RuntimeError, match="terminal"):
        await container.start()


@pytest.mark.asyncio
async def test_stop_aggregates_failures_and_continues_then_retries_only_failed_cleanup(
    runtime_settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    container = RuntimeContainer(runtime_settings)
    await container.start()
    original_ambient_stop = container.ambient.stop
    original_resources_stop = container.resources.stop
    attempts: list[str] = []

    async def fail_ambient_stop() -> None:
        attempts.append("ambient")
        raise RuntimeError("ambient teardown failed")

    async def fail_resources_stop() -> None:
        attempts.append("resources")
        raise RuntimeError("resources teardown failed")

    monkeypatch.setattr(container.ambient, "stop", fail_ambient_stop)
    monkeypatch.setattr(container.resources, "stop", fail_resources_stop)

    with pytest.raises(RuntimeLifecycleError) as raised:
        await container.stop()

    cleanup_errors = [
        error for error in raised.value.exceptions if isinstance(error, RuntimeCleanupError)
    ]
    assert {error.component for error in cleanup_errors} == {"ambient", "resources"}
    assert attempts == ["ambient", "resources"]
    with pytest.raises(RuntimeError, match="database is not open"):
        await container.database.fetchone("SELECT 1")
    assert container.memory.projection_running is False

    monkeypatch.setattr(container.ambient, "stop", original_ambient_stop)
    monkeypatch.setattr(container.resources, "stop", original_resources_stop)
    await container.stop()
    await container.stop()
    assert attempts == ["ambient", "resources"]


@pytest.mark.asyncio
async def test_stop_finishes_other_cleanup_without_swallowing_cancellation(
    runtime_settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    container = RuntimeContainer(runtime_settings)
    await container.start()
    original_ambient_stop = container.ambient.stop

    async def cancel_ambient_stop() -> None:
        raise asyncio.CancelledError("injected teardown cancellation")

    monkeypatch.setattr(container.ambient, "stop", cancel_ambient_stop)
    with pytest.raises(BaseExceptionGroup) as raised:
        await container.stop()

    assert any(isinstance(error, asyncio.CancelledError) for error in raised.value.exceptions)
    with pytest.raises(RuntimeError, match="database is not open"):
        await container.database.fetchone("SELECT 1")

    monkeypatch.setattr(container.ambient, "stop", original_ambient_stop)
    await container.stop()


@pytest.mark.asyncio
async def test_application_lifespan_calls_stop_when_container_start_fails(
    runtime_settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[str] = []

    async def fail_start(_container: RuntimeContainer) -> None:
        calls.append("start")
        raise RuntimeError("injected application startup failure")

    async def observe_stop(_container: RuntimeContainer) -> None:
        calls.append("stop")

    monkeypatch.setattr(RuntimeContainer, "start", fail_start)
    monkeypatch.setattr(RuntimeContainer, "stop", observe_stop)
    app = create_app(runtime_settings)

    with pytest.raises(RuntimeError, match="injected application startup failure"):
        async with app.router.lifespan_context(app):
            pass

    assert calls == ["start", "stop"]


@pytest.mark.asyncio
async def test_application_lifespan_aggregates_start_and_cleanup_failures(
    runtime_settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def fail_start(_container: RuntimeContainer) -> None:
        raise RuntimeError("injected application startup failure")

    async def fail_stop(_container: RuntimeContainer) -> None:
        raise RuntimeError("injected container cleanup failure")

    monkeypatch.setattr(RuntimeContainer, "start", fail_start)
    monkeypatch.setattr(RuntimeContainer, "stop", fail_stop)
    app = create_app(runtime_settings)

    with pytest.raises(RuntimeLifecycleError) as raised:
        async with app.router.lifespan_context(app):
            pass

    messages = [str(error) for error in raised.value.exceptions]
    assert any("application startup failure" in message for message in messages)
    assert any("runtime_container cleanup failed" in message for message in messages)


@pytest.mark.asyncio
async def test_application_lifespan_aggregates_body_and_both_cleanup_failures(
    runtime_settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    from chatwaifu_runtime.mcp_server import RuntimeMcpServer

    async def fail_mcp_stop(_server: RuntimeMcpServer) -> None:
        raise RuntimeError("injected MCP cleanup failure")

    async def fail_container_stop(_container: RuntimeContainer) -> None:
        raise RuntimeError("injected container cleanup failure")

    monkeypatch.setattr(RuntimeMcpServer, "stop", fail_mcp_stop)
    monkeypatch.setattr(RuntimeContainer, "stop", fail_container_stop)
    app = create_app(runtime_settings)

    with pytest.raises(RuntimeLifecycleError) as raised:
        async with app.router.lifespan_context(app):
            raise RuntimeError("injected application body failure")

    messages = [str(error) for error in raised.value.exceptions]
    assert any("application body failure" in message for message in messages)
    assert any("mcp_server cleanup failed" in message for message in messages)
    assert any("runtime_container cleanup failed" in message for message in messages)
