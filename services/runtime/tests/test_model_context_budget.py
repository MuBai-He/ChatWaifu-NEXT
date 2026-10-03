# pyright: reportPrivateUsage=false
"""Model budget persistence, frozen admission, whole-wire output and information retention."""

import asyncio
import json
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from pathlib import Path
from typing import cast
from uuid import UUID, uuid4

import httpx2
import pytest
from chatwaifu_protocol.character import (
    AffectState,
    CharacterKernelSnapshot,
    ModelContextBudget,
    PromptContextIdentity,
    RelationshipState,
    ResponsePlan,
)
from chatwaifu_protocol.memory import MemoryContextPacket, MemoryExcerpt
from chatwaifu_runtime.bootstrap.container import RuntimeContainer
from chatwaifu_runtime.character_kernel.prompt import PromptCompilation, PromptCompiler
from chatwaifu_runtime.characters.service import CharacterService
from chatwaifu_runtime.config.settings import Settings
from chatwaifu_runtime.conversation.models import ConversationHistoryEntry, ConversationTurnOptions
from chatwaifu_runtime.persistence.database import Database
from chatwaifu_runtime.persistence.migrations import MIGRATIONS
from chatwaifu_runtime.providers.context_budget import resolve_context_budget
from chatwaifu_runtime.providers.contracts import (
    LlmProvider,
    LlmRequest,
    LlmResponseCompleted,
    LlmStreamEvent,
    LlmTextDelta,
)
from chatwaifu_runtime.providers.model_config import (
    ModelConfigurationService,
    ModelRoleConfig,
    extract_nonsecret_route,
)
from chatwaifu_runtime.providers.openai_compatible import OpenAiCompatibleLlmProvider
from pydantic import ValidationError

CHARACTERS_ROOT = Path(__file__).resolve().parents[3] / "characters"


def expanded_budget() -> ModelContextBudget:
    return ModelContextBudget(
        output_reserve_tokens=8192,
        max_output_tokens=8192,
        estimate_margin_ratio=0.15,
        section_policy="scaled",
        history_turn_limit=32,
        memory_candidate_limit=24,
        tool_result_max_bytes=131072,
    )


def test_separate_input_cap_reserve_and_estimation_margin() -> None:
    configured = expanded_budget().model_copy(update={"input_token_limit": 12000})
    resolved = resolve_context_budget(32768, configured)
    assert resolved.input_tokens == 10434
    assert resolve_context_budget(1024, ModelContextBudget()).input_tokens == 124
    assert resolve_context_budget(8192, ModelContextBudget()).input_tokens == 7292
    with pytest.raises(ValueError, match="no estimated input"):
        resolve_context_budget(8192, configured.model_copy(update={"output_reserve_tokens": 8192}))


@pytest.mark.parametrize(
    "settings",
    [
        {"output_reserve_tokens": 100, "max_output_tokens": 101},
        {"output_reserve_tokens": 8192, "max_output_tokens": 4096, "output_token_limit": 2048},
        {"estimate_margin_ratio": -0.1},
        {"history_turn_limit": 129},
        {"tool_result_max_bytes": 1_048_577},
    ],
)
def test_invalid_budget_settings_fail_validation(settings: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        ModelContextBudget.model_validate(settings)


@pytest.mark.asyncio
async def test_model_budget_migration_and_restart_keep_old_rows_and_new_settings(
    tmp_path: Path, runtime_settings: Settings
) -> None:
    path = tmp_path / "legacy.sqlite"
    # This fixture predates budget migration 36, regardless of later channel migrations.
    old = Database(
        path,
        runtime_settings.storage,
        migrations=tuple(item for item in MIGRATIONS if item[0] < 36),
    )
    await old.open()
    async with old.transaction() as connection:
        await connection.execute(
            "INSERT INTO model_role_configs VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            ("chat", "demo", "existing-model", "", 30, 16384, 1, datetime.now(UTC).isoformat()),
        )
    await old.close()
    database = Database(path, runtime_settings.storage)
    await database.open()
    models = ModelConfigurationService(database, runtime_settings)
    try:
        await models.start()
        current = models.get("chat")
        assert current.model == "existing-model" and current.context_window == 16384
        assert current.budget == ModelContextBudget()
        await models.update(
            current.model_copy(update={"context_window": 32768, "budget": expanded_budget()})
        )
        original_summary = models.get("memory_summary")
    finally:
        await database.close()
    await database.open()
    try:
        reloaded = ModelConfigurationService(database, runtime_settings)
        await reloaded.start()
        assert reloaded.get("chat").budget == expanded_budget()
        assert reloaded.get("chat").context_window == 32768
        assert reloaded.get("memory_summary") == original_summary
    finally:
        await database.close()


def test_budget_changes_route_identity_without_exposing_secrets() -> None:
    config = ModelRoleConfig(
        role="chat", provider="demo", model="same", updated_at=datetime.now(UTC)
    )
    first = extract_nonsecret_route(config)
    second = extract_nonsecret_route(config.model_copy(update={"budget": expanded_budget()}))
    identities = [
        PromptContextIdentity.create(
            character_id="default",
            character_package_hash="c" * 64,
            presentation_profile="instant_message",
            tools_digest="d" * 64,
            chat_route=route,
            memory_summary_route=first,
        )
        for route in (first, second)
    ]
    a, b = identities
    assert a.identity_hash != b.identity_hash
    assert "base_url" not in b.model_dump_json()


@pytest.mark.asyncio
async def test_streaming_output_limit_reaches_actual_http_request() -> None:
    payloads: list[dict[str, object]] = []

    async def send(request: httpx2.Request) -> httpx2.Response:
        payloads.append(json.loads(request.content))
        return httpx2.Response(
            200,
            text=(
                'data: {"choices":[{"delta":{"content":"ok"},"finish_reason":"stop"}]}\n\n'
                "data: [DONE]\n\n"
            ),
        )

    provider = OpenAiCompatibleLlmProvider(
        base_url="https://example.test/v1",
        model="configured",
        api_key=None,
        timeout_seconds=5,
        transport=httpx2.MockTransport(send),
    )
    for cap in (None, 8192):
        events = [
            event
            async for event in provider.stream(
                LlmRequest(uuid4(), "hello", "system", max_output_tokens=cap)
            )
        ]
        assert any(isinstance(event, LlmTextDelta) for event in events)
    assert "max_tokens" not in payloads[0]
    assert payloads[1]["max_tokens"] == 8192


@pytest.mark.asyncio
async def test_summary_route_uses_its_own_budget_and_rejects_unsent_overflow(
    runtime_settings: Settings, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    database = Database(tmp_path / "summary.sqlite", runtime_settings.storage)
    await database.open()
    models = ModelConfigurationService(database, runtime_settings)
    await models.start()
    payloads: list[dict[str, object]] = []
    original_client = httpx2.AsyncClient

    async def send(request: httpx2.Request) -> httpx2.Response:
        payloads.append(json.loads(request.content))
        return httpx2.Response(200, json={"choices": [{"message": {"content": "summary"}}]})

    def client(*args: object, **kwargs: object) -> httpx2.AsyncClient:
        return original_client(transport=httpx2.MockTransport(send))

    monkeypatch.setattr(httpx2, "AsyncClient", client)
    config = models.get("memory_summary").model_copy(
        update={
            "provider": "openai_compatible",
            "base_url": "https://example.test/v1",
            "context_window": 4096,
            "budget": ModelContextBudget(output_reserve_tokens=2048, max_output_tokens=2048),
        }
    )
    try:
        assert (
            await models.complete("memory_summary", "summarize", "material", config=config)
            == "summary"
        )
        assert payloads[0]["max_tokens"] == 2048
        with pytest.raises(ValueError, match="mandatory input exceeds"):
            await models.complete("memory_summary", "summarize", "长资料。" * 5000, config=config)
        assert len(payloads) == 1
    finally:
        await database.close()


@pytest.mark.asyncio
async def test_scaled_sections_keep_facts_that_window_only_still_discards() -> None:
    characters = CharacterService(CHARACTERS_ROOT)
    characters.start()
    character = characters.get("default")
    assert character is not None
    character = character.model_copy(update={"system_prompt": "角色设定。" * 900 + "CANON-END"})
    now = datetime.now(UTC)
    kernel = CharacterKernelSnapshot(
        character_id="default",
        user_scope="local",
        revision=1,
        affect=AffectState(updated_at=now),
        relationship=RelationshipState(updated_at=now),
    )
    memory = MemoryContextPacket(
        token_budget_used=1800,
        relevant_memories=[
            MemoryExcerpt(
                memory_id=uuid4(),
                text="记忆背景。" * 300 + "MEMORY-END",
                relevance=1,
                source_event_ids=[uuid4()],
            )
        ],
    )
    history = (("user", "历史资料。" * 1600 + "HISTORY-END"), ("assistant", "收到。"))

    class Models:
        def __init__(self, budget: ModelContextBudget) -> None:
            self.config = ModelRoleConfig(
                role="chat",
                provider="demo",
                model="test",
                context_window=32768,
                budget=budget,
                updated_at=now,
            )

        def get(self, role: str) -> ModelRoleConfig:
            return self.config

        async def complete(self, role: str, system: str, user: str) -> str:
            return "较早内容没有可引用的原文。"

    outputs: list[PromptCompilation] = []
    for budget in (ModelContextBudget(), expanded_budget()):
        outputs.append(
            await PromptCompiler(cast(ModelConfigurationService, Models(budget))).compile(
                character=character,
                kernel=kernel,
                plan=ResponsePlan(
                    intent="answer", tone="serious", expression="neutral", rationale="test"
                ),
                memory=memory,
                history=history,
                user_text="提供三个编号",
                as_of=now,
            )
        )
    small, large = outputs
    assert "CANON-END" not in small.system_prompt and small.report.persona_omitted_characters > 0
    assert small.report.omitted_memory_ids == [memory.relevant_memories[0].memory_id]
    assert small.report.dropped_history_turns == 1
    assert "CANON-END" in large.system_prompt and large.report.persona_omitted_characters == 0
    assert "MEMORY-END" in " ".join(text for _, text in large.context)
    assert large.history == history
    assert large.report.omitted_memory_ids == []
    assert large.report.estimator == "character_half_v1"


@pytest.mark.asyncio
async def test_admitted_budget_survives_config_change_through_retrieval_and_send(
    runtime_settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    container = RuntimeContainer(runtime_settings)
    await container.start()
    entered, release = asyncio.Event(), asyncio.Event()
    requests: list[LlmRequest] = []
    retrievals: list[dict[str, int]] = []
    history_limits: list[int] = []

    class Provider:
        kind = "demo"
        supports_tool_calling = False

        async def stream(self, request: LlmRequest) -> AsyncIterator[LlmStreamEvent]:
            requests.append(request)
            yield LlmTextDelta("收到。")
            yield LlmResponseCompleted("stop")

    def create(config: ModelRoleConfig) -> LlmProvider:
        return Provider()

    async def retrieve(
        session_id: UUID, turn_id: UUID, character_id: str, query: str, **kwargs: int
    ) -> MemoryContextPacket:
        retrievals.append(kwargs)
        entered.set()
        await release.wait()
        return MemoryContextPacket(token_budget_used=0)

    async def recent(
        session_id: UUID, turn_id: UUID, *, limit: int
    ) -> tuple[ConversationHistoryEntry, ...]:
        history_limits.append(limit)
        return ()

    try:
        initial = container.model_configurations.get("chat").model_copy(
            update={"context_window": 32768, "budget": expanded_budget()}
        )
        await container.model_configurations.update(initial)
        monkeypatch.setattr(container.model_configurations, "create_chat_provider", create)
        monkeypatch.setattr(container.memory, "retrieve_context", retrieve)
        monkeypatch.setattr(container.conversation._repository, "recent_history", recent)
        session = await container.sessions.create_session("default")
        options = ConversationTurnOptions(output_modes=frozenset({"text"}), allow_tools=False)
        task = asyncio.create_task(
            container.conversation.submit_text(session.session_id, "第一问", options=options)
        )
        await asyncio.wait_for(entered.wait(), 5)
        changed = initial.model_copy(
            update={
                "context_window": 16384,
                "budget": ModelContextBudget(output_reserve_tokens=4096, max_output_tokens=4096),
            }
        )
        await container.model_configurations.update(changed)
        release.set()
        await asyncio.wait_for(task, 5)
        active = container.conversation._active[session.session_id].task
        assert active is not None
        await asyncio.wait_for(active, 5)
        await container.conversation.submit_text(session.session_id, "第二问", options=options)
        active = container.conversation._active[session.session_id].task
        assert active is not None
        await asyncio.wait_for(active, 5)
        assert [x.max_output_tokens for x in requests] == [8192, 4096]
        assert [x.tool_result_max_bytes for x in requests] == [131072, 32768]
        assert [x.input_budget.estimated_token_limit for x in requests if x.input_budget] == [
            21370,
            12288,
        ]
        assert history_limits == [32, 16]
        assert retrievals == [{"token_budget": 3419, "limit": 24}, {"token_budget": 700}]
    finally:
        release.set()
        await container.stop()
