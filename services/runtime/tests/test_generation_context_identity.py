# pyright: reportPrivateUsage=false
"""An admitted generation keeps its prompt and model route across config edits."""

import asyncio
import json
import shutil
from collections.abc import AsyncIterator
from datetime import UTC, datetime, tzinfo
from pathlib import Path
from uuid import UUID

import pytest
from chatwaifu_protocol.character import CharacterPromptCompiledPayload
from chatwaifu_protocol.memory import MemoryContextPacket
from chatwaifu_runtime.bootstrap.container import RuntimeContainer
from chatwaifu_runtime.characters.service import CharacterService
from chatwaifu_runtime.config.settings import Settings
from chatwaifu_runtime.conversation.models import ConversationTurnOptions
from chatwaifu_runtime.providers.contracts import (
    LlmProvider,
    LlmRequest,
    LlmResponseCompleted,
    LlmStreamEvent,
    LlmTextDelta,
)
from chatwaifu_runtime.providers.model_config import ModelRoleConfig, extract_nonsecret_route


class _RecordingProvider:
    kind = "demo"
    supports_tool_calling = False

    def __init__(self, model: str, requests: list[tuple[str, LlmRequest]]) -> None:
        self.model = model
        self.requests = requests

    async def stream(self, request: LlmRequest) -> AsyncIterator[LlmStreamEvent]:
        self.requests.append((self.model, request))
        yield LlmTextDelta(f"reply from {self.model}")
        yield LlmResponseCompleted("stop")


@pytest.mark.asyncio
async def test_admission_snapshot_survives_live_route_edit(
    runtime_settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    class Clock(datetime):
        current = datetime(2026, 9, 30, 23, 59, tzinfo=UTC)

        @classmethod
        def now(cls, tz: tzinfo | None = None) -> datetime:
            return cls.current.astimezone(tz)

    monkeypatch.setattr("chatwaifu_runtime.conversation.service.datetime", Clock)
    admitted_at = Clock.current
    container = RuntimeContainer(runtime_settings)
    await container.start()
    try:
        session = await container.sessions.create_session("default")
        initial = container.model_configurations.get("chat")
        requests: list[tuple[str, LlmRequest]] = []

        def create_provider(config: ModelRoleConfig) -> LlmProvider:
            return _RecordingProvider(config.model, requests)

        monkeypatch.setattr(
            container.model_configurations,
            "create_chat_provider",
            create_provider,
        )

        entered = asyncio.Event()
        release = asyncio.Event()
        original_retrieve = container.memory.retrieve_context

        async def paused_retrieve(
            session_id: UUID,
            turn_id: UUID,
            character_id: str,
            query: str,
            *,
            token_budget: int = 700,
        ) -> MemoryContextPacket:
            entered.set()
            await release.wait()
            return await original_retrieve(
                session_id,
                turn_id,
                character_id,
                query,
                token_budget=token_budget,
            )

        monkeypatch.setattr(container.memory, "retrieve_context", paused_retrieve)
        first_task = asyncio.create_task(
            container.conversation.submit_text(
                session.session_id,
                "first",
                options=ConversationTurnOptions(
                    output_modes=frozenset({"text"}), allow_tools=False
                ),
            )
        )
        await asyncio.wait_for(entered.wait(), 3)
        Clock.current = datetime(2026, 10, 1, 0, 1, tzinfo=UTC)
        changed = initial.model_copy(
            update={"model": "route-after-admission", "context_window": 2048}
        )
        await container.model_configurations.update(changed)
        release.set()
        first = await first_task
        active = container.conversation._active[session.session_id]
        assert active.task is not None
        await asyncio.wait_for(active.task, 5)

        assert [name for name, _request in requests] == [initial.model]
        assert admitted_at.isoformat(timespec="seconds") in requests[0][1].system_prompt
        assert Clock.current.isoformat(timespec="seconds") not in requests[0][1].system_prompt
        initial_decision_prompt = requests[0][1].tool_decision_system_prompt
        assert initial_decision_prompt is not None
        assert admitted_at.isoformat(timespec="seconds") in initial_decision_prompt
        assert Clock.current.isoformat(timespec="seconds") not in initial_decision_prompt
        rows = await container.event_store.read_stream(session.session_id, limit=500)
        first_payload = CharacterPromptCompiledPayload.model_validate(
            next(
                row["payload"]
                for row in rows
                if row["event_type"] == "character.prompt_compiled"
                and row["generation_id"] == str(first.generation_id)
            )
        )
        assert first_payload.identity is not None
        assert first_payload.identity.chat_route.model == initial.model
        assert first_payload.identity.chat_route.context_window == initial.context_window
        assert first_payload.report.budget == initial.context_window - 900

        second = await container.conversation.submit_text(
            session.session_id,
            "second",
            options=ConversationTurnOptions(output_modes=frozenset({"text"}), allow_tools=False),
        )
        active = container.conversation._active[session.session_id]
        assert active.task is not None
        await asyncio.wait_for(active.task, 5)
        assert [name for name, _request in requests] == [initial.model, "route-after-admission"]
        rows = await container.event_store.read_stream(session.session_id, limit=500)
        second_payload = CharacterPromptCompiledPayload.model_validate(
            next(
                row["payload"]
                for row in rows
                if row["event_type"] == "character.prompt_compiled"
                and row["generation_id"] == str(second.generation_id)
            )
        )
        assert second_payload.identity is not None
        assert second_payload.identity.chat_route.model == "route-after-admission"
        assert second_payload.identity.identity_hash != first_payload.identity.identity_hash
        assert second_payload.report.budget == 2048 - 900
        assert Clock.current.isoformat(timespec="seconds") in requests[1][1].system_prompt
        next_decision_prompt = requests[1][1].tool_decision_system_prompt
        assert next_decision_prompt is not None
        assert Clock.current.isoformat(timespec="seconds") in next_decision_prompt

        # A new date changes dynamic context, not the static configuration identity.
        Clock.current = datetime(2026, 10, 2, 0, 1, tzinfo=UTC)
        third = await container.conversation.submit_text(
            session.session_id,
            "third",
            options=ConversationTurnOptions(output_modes=frozenset({"text"}), allow_tools=False),
        )
        active = container.conversation._active[session.session_id]
        assert active.task is not None
        await asyncio.wait_for(active.task, 5)
        rows = await container.event_store.read_stream(session.session_id, limit=500)
        third_payload = CharacterPromptCompiledPayload.model_validate(
            next(
                row["payload"]
                for row in rows
                if row["event_type"] == "character.prompt_compiled"
                and row["generation_id"] == str(third.generation_id)
            )
        )
        assert third_payload.identity is not None
        assert third_payload.identity == second_payload.identity
        assert Clock.current.isoformat(timespec="seconds") in requests[2][1].system_prompt
    finally:
        await container.stop()

    recovered = RuntimeContainer(runtime_settings)
    await recovered.start()
    try:
        persisted = await recovered.event_store.read_stream(session.session_id, limit=500)
        identities = [
            CharacterPromptCompiledPayload.model_validate(row["payload"]).identity
            for row in persisted
            if row["event_type"] == "character.prompt_compiled"
        ]
        assert [identity.identity_hash for identity in identities if identity is not None] == [
            first_payload.identity.identity_hash,
            second_payload.identity.identity_hash,
            third_payload.identity.identity_hash,
        ]
    finally:
        await recovered.stop()


@pytest.mark.asyncio
async def test_key_rotation_keeps_identity_and_redacts_endpoint(
    runtime_settings: Settings,
) -> None:
    container = RuntimeContainer(runtime_settings)
    await container.start()
    try:
        before = container.model_configurations.get("chat")
        route = before.model_copy(
            update={
                "provider": "openai_compatible",
                "base_url": "https://user:secret@private.example:8443/v1?token=hidden",
            }
        )
        configured = await container.model_configurations.update(route, api_key="secret-one")
        first = extract_nonsecret_route(configured)
        rotated = await container.model_configurations.update(configured, api_key="secret-two")
        second = extract_nonsecret_route(rotated)
        assert first == second
        serialized = json.dumps(second.model_dump(mode="json"))
        assert "secret" not in serialized
        assert "private.example" not in serialized
        assert "hidden" not in serialized
        assert second.endpoint_digest is not None
    finally:
        await container.stop()


def test_character_package_fingerprint_changes_only_after_restart(tmp_path: Path) -> None:
    root = tmp_path / "characters"
    shutil.copytree(Path(__file__).resolve().parents[3] / "characters", root)
    characters = CharacterService(root)
    characters.start()
    original = characters.get("default")
    assert original is not None
    persona = root / "default" / "persona.md"
    persona.write_text(persona.read_text(encoding="utf-8") + "\n测试修订。\n", encoding="utf-8")
    assert characters.get("default") == original
    characters.start()
    changed = characters.get("default")
    assert changed is not None
    assert changed.package_hash != original.package_hash
