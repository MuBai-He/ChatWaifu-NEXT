"""Admitted group request reaches QQ account execution even with participation off."""

from collections.abc import AsyncIterator, Awaitable, Callable
from typing import cast
from uuid import UUID

import pytest
from chatwaifu_protocol.base import JsonObject
from chatwaifu_protocol.channel_settings import ChannelRuntimeSettingsUpdate
from chatwaifu_runtime.providers.contracts import (
    LlmRequest,
    LlmResponseCompleted,
    LlmStreamEvent,
    LlmTextDelta,
    LlmToolCall,
    LlmToolCallRequested,
)

from services.runtime.tests.test_channel_group_application import App

pytest_plugins = ["services.runtime.tests.test_channel_group_application"]


async def test_group_account_poke_executes_without_enabling_autonomous_participation(
    app: App, monkeypatch: pytest.MonkeyPatch
) -> None:
    await app.enable()
    container = app.container
    container.qq_scene_capabilities.groups = app.service
    app.service.scene_skill_policy = container.group_agent_skills
    before = container.channel_settings.get()
    await container.channel_settings.update(
        ChannelRuntimeSettingsUpdate(
            expected_revision=before.revision,
            policy=before.policy.model_copy(update={"qq_account_enabled": True}),
        )
    )
    route = await app.repository.get_route(app.route.route_id)
    assert route is not None
    assert (await container.group_autonomy.policy(route)).mode == "off"
    assert await container.group_agent_skills(route) == {"qq.scene", "qq.account"}
    monkeypatch.setattr(type(container.qq_channels), "agent_available", property(lambda _: True))
    calls: list[JsonObject] = []

    async def call(
        connection: UUID,
        account: str,
        action: str,
        params: JsonObject,
        guard: Callable[[], Awaitable[bool]],
        version: str,
    ) -> JsonObject:
        assert await guard()
        assert connection == app.connection_id and account == "900" and version == "4.18.28"
        calls.append({"action": action, "params": params})
        return {}

    async def stream(request: LlmRequest) -> AsyncIterator[LlmStreamEvent]:
        if not calls:
            assert request.tool_choice == "required"
            tool = next(
                t
                for t in request.tools
                if cast(
                    JsonObject, cast(JsonObject, t.input_schema["properties"]).get("action", {})
                ).get("const")
                == "send_poke"
            )
            yield LlmToolCallRequested(
                LlmToolCall("poke", tool.name, {"action": "send_poke", "params": {}})
            )
            yield LlmResponseCompleted("tool_calls")
        else:
            yield LlmTextDelta("戳一戳已执行。")
            yield LlmResponseCompleted("stop")

    monkeypatch.setattr(container.qq_account_capabilities, "call", call)
    monkeypatch.setattr(app.provider, "stream", stream)
    accepted = await app.ingest(text="戳一戳我")
    await app.join(accepted.channel_turn_id)
    assert calls == [{"action": "send_poke", "params": {"user_id": "111", "group_id": "500"}}]
    rows = await container.database.fetchall(
        "SELECT state, confirmation_request_id FROM skill_runs WHERE skill_id = 'qq.account'"
    )
    assert len(rows) == 1 and rows[0]["state"] == "succeeded"
    assert rows[0]["confirmation_request_id"] is None
    assert (await container.group_autonomy.policy(route)).mode == "off"
