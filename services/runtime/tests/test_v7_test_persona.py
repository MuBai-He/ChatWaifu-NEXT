"""Check the frozen test persona's loading and section-budget boundaries."""

from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import cast

import pytest
from chatwaifu_protocol.character import (
    AffectState,
    CharacterKernelSnapshot,
    ModelContextBudget,
    RelationshipState,
    ResponsePlan,
)
from chatwaifu_protocol.memory import MemoryContextPacket
from chatwaifu_runtime.character_kernel.prompt import PromptCompiler
from chatwaifu_runtime.characters.service import CharacterService
from chatwaifu_runtime.providers.model_config import ModelConfigurationService

ROOT = Path(__file__).resolve().parents[3]
V7 = ROOT / (
    "docs/research/qq-agent-plus-evidence/"
    "persona-candidate-v7-source-preservation-2026-10-01.md.txt"
)


class _Models:
    def __init__(self, window: int, budget: ModelContextBudget) -> None:
        self.config = SimpleNamespace(context_window=window, budget=budget)

    def get(self, role: str) -> SimpleNamespace:
        assert role == "chat"
        return self.config


@pytest.mark.asyncio
@pytest.mark.parametrize("presentation", ["instant_message", "single_text", "default_voice"])
@pytest.mark.parametrize(
    ("window", "budget", "complete"),
    [
        (1024, ModelContextBudget(), False),
        (4096, ModelContextBudget(), False),
        (8192, ModelContextBudget(), True),
        (
            32768,
            ModelContextBudget(
                output_reserve_tokens=8192,
                max_output_tokens=8192,
                estimate_margin_ratio=0.15,
                section_policy="scaled",
            ),
            True,
        ),
    ],
)
async def test_v7_persona_is_loaded_verbatim_and_clipping_is_reported(
    presentation: str, window: int, budget: ModelContextBudget, complete: bool
) -> None:
    characters = CharacterService(ROOT / "characters")
    characters.start()
    character = characters.get("default")
    assert character is not None
    expected = V7.read_text(encoding="utf-8").strip()
    assert character.system_prompt == expected
    assert (ROOT / "characters/default/persona.md").read_bytes() == V7.read_bytes()

    now = datetime(2026, 10, 4, tzinfo=UTC)
    compilation = await PromptCompiler(
        cast(ModelConfigurationService, _Models(window, budget))
    ).compile(
        character=character,
        kernel=CharacterKernelSnapshot(
            character_id="default",
            user_scope="local",
            revision=1,
            affect=AffectState(updated_at=now),
            relationship=RelationshipState(updated_at=now),
        ),
        plan=ResponsePlan(intent="answer", tone="gentle", expression="neutral", rationale="test"),
        memory=MemoryContextPacket(token_budget_used=0),
        history=(),
        user_text="你好",
        presentation_profile=presentation,
        as_of=now,
    )

    if complete:
        assert compilation.report.persona_omitted_characters == 0
        assert "[CHARACTER CANON]\n" + expected in compilation.system_prompt
    else:
        assert compilation.report.persona_omitted_characters > 0
        assert expected not in compilation.system_prompt
