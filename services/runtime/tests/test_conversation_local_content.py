"""Local content can finish with relevant native schemas and no external action."""

# pyright: reportPrivateUsage=false
from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from typing import Literal

import pytest
from chatwaifu_protocol.session import GenerationState
from chatwaifu_runtime.bootstrap.container import RuntimeContainer
from chatwaifu_runtime.character_kernel.prompt import PromptCompiler
from chatwaifu_runtime.config.settings import Settings
from chatwaifu_runtime.conversation.models import ConversationTurnOptions
from chatwaifu_runtime.providers.contracts import (
    LlmProvider,
    LlmRequest,
    LlmResponseCompleted,
    LlmStreamEvent,
    LlmTextDelta,
)
from chatwaifu_runtime.providers.model_config import ModelRoleConfig


class _LocalAnswer:
    kind = "demo"
    supports_tool_calling = True

    def __init__(self) -> None:
        self.requests: list[LlmRequest] = []

    async def stream(self, request: LlmRequest) -> AsyncIterator[LlmStreamEvent]:
        self.requests.append(request)
        yield LlmTextDelta("Local content answer.")
        yield LlmResponseCompleted("stop")


@pytest.mark.asyncio
@pytest.mark.parametrize("contract_position", ["system", "pre_user"])
@pytest.mark.parametrize(
    "text",
    [
        "不要打开 https://example.org/guide，只解释这个 URL 的组成。",
        "Create a short poem about rain.",
        "Please check this calculation: 17 * 23 = 391.",
        '请看看这段代码有没有语法错误\uff1aprint("hello")',
    ],
)
async def test_local_content_does_not_require_a_gratuitous_operation(
    runtime_settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
    text: str,
    contract_position: Literal["system", "pre_user"],
) -> None:
    provider = _LocalAnswer()

    def create_provider(_: ModelRoleConfig) -> LlmProvider:
        return provider

    container = RuntimeContainer(runtime_settings)
    await container.start()
    monkeypatch.setattr(
        container.conversation,
        "_prompt_compiler",
        PromptCompiler(container.model_configurations, output_contract_position=contract_position),
    )
    monkeypatch.setattr(container.model_configurations, "create_chat_provider", create_provider)
    try:
        assert len(container.runtime_skills.list()) == 13
        session = await container.sessions.create_session("default")
        accepted = await container.conversation.submit_text(
            session.session_id,
            text,
            options=ConversationTurnOptions(output_modes=frozenset({"text"})),
        )
        task = container.conversation._active[session.session_id].task
        assert task is not None
        await asyncio.wait_for(task, timeout=5)
        record = await container.conversation_repository.generation_result(accepted.generation_id)
        assert record is not None and record.state is GenerationState.COMPLETED
        assert record.output_text == "Local content answer."
        assert len(provider.requests) == 1
        actual = provider.requests[0]
        assert actual.tools and actual.tool_choice == "auto"
        character = container.characters.get("default")
        assert character is not None and character.system_prompt in actual.system_prompt
        if contract_position == "pre_user":
            assert actual.pre_user_system_prompt is not None
            assert "[OUTPUT CONTRACT]" in actual.pre_user_system_prompt
            assert "[OUTPUT CONTRACT]" not in actual.system_prompt
        else:
            assert actual.pre_user_system_prompt is None
            assert "[OUTPUT CONTRACT]" in actual.system_prompt
        assert await container.runtime_skills.list_runs(session.session_id) == []
    finally:
        await container.stop()
