from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import cast

import pytest
from chatwaifu_protocol.character import (
    AffectState,
    CharacterKernelSnapshot,
    RelationshipState,
    ResponsePlan,
)
from chatwaifu_protocol.memory import MemoryContextPacket
from chatwaifu_runtime.character_kernel.prompt import PromptCompiler
from chatwaifu_runtime.characters.service import CharacterService
from chatwaifu_runtime.providers.model_config import ModelConfigurationService

CHARACTERS_ROOT = Path(__file__).resolve().parents[3] / "characters"


def test_six_file_character_package_loads_renderer_independent_policy() -> None:
    characters = CharacterService(CHARACTERS_ROOT)
    characters.start()

    nene = characters.get("default")

    assert nene is not None
    assert nene.system_prompt.startswith("你是 ChatWaifu NEXT")
    assert "headpat" in nene.avatar_capabilities["motions"]
    assert nene.relationship_policy["maximum_turn_delta"] == 0.08


@pytest.mark.asyncio
async def test_prompt_compiler_keeps_latest_contiguous_history_and_summarizes_prefix() -> None:
    models = _PromptModels()
    compiler = PromptCompiler(cast(ModelConfigurationService, models))
    characters = CharacterService(CHARACTERS_ROOT)
    characters.start()
    character = characters.get("default")
    assert character is not None
    now = datetime.now(UTC)
    kernel = CharacterKernelSnapshot(
        character_id="default",
        user_scope="local",
        revision=2,
        affect=AffectState(updated_at=now),
        relationship=RelationshipState(updated_at=now),
    )
    history = tuple(
        ("user" if index % 2 == 0 else "assistant", f"history-{index}-" + "长" * 390)
        for index in range(6)
    )

    result = await compiler.compile(
        character=character,
        kernel=kernel,
        plan=ResponsePlan(
            intent="answer",
            tone="gentle",
            expression="neutral",
            rationale="test",
        ),
        memory=MemoryContextPacket(token_budget_used=0),
        history=history,
        user_text="现在的问题",
    )

    assert result.history == history[-3:]
    assert result.report.dropped_history_turns == 3
    assert models.summary_inputs == ["\n".join(f"{role}: {text}" for role, text in history[:3])]
    assert "[SAFETY]" in result.system_prompt
    assert "[CHARACTER CANON]" in result.system_prompt
    assert "Earlier Conversation Summary" in result.context[0][1]


class _PromptModels:
    def __init__(self) -> None:
        self.summary_inputs: list[str] = []

    def get(self, role: str) -> SimpleNamespace:
        assert role == "chat"
        return SimpleNamespace(context_window=1024)

    async def complete(self, role: str, system: str, user: str) -> str:
        del system
        assert role == "memory_summary"
        self.summary_inputs.append(user)
        return "较早对话摘要"
