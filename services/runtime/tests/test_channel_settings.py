"""Operator policy persistence, live revocation and unchanged QQ boundaries."""

# pyright: reportPrivateUsage=false

import asyncio
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

import pytest
from chatwaifu_protocol.base import JsonObject
from chatwaifu_protocol.channel_settings import (
    ChannelRuntimePolicy,
    ChannelRuntimeSettingsUpdate,
)
from chatwaifu_protocol.channels import ChannelTurnStatus
from chatwaifu_runtime.bootstrap.container import RuntimeContainer
from chatwaifu_runtime.config.settings import Settings, load_settings
from chatwaifu_runtime.conversation.models import ConversationUserInputContext
from chatwaifu_runtime.external_channels.models import ChannelBindingRecord
from chatwaifu_runtime.external_channels.service import ChannelConflictError, ChannelPolicyError
from chatwaifu_runtime.external_channels.settings import ChannelSettingsService
from chatwaifu_runtime.main import create_app
from chatwaifu_runtime.persistence.sqlite_channel_settings import SQLiteChannelSettingsRepository
from chatwaifu_runtime.providers.contracts import SynthesisRequest, SynthesisResult
from chatwaifu_runtime.runtime_skills.adapters import GenerationSkillContext
from chatwaifu_runtime.runtime_skills.errors import SkillExecutionError
from chatwaifu_runtime.runtime_skills.public_web import PublicWebReader
from fastapi.testclient import TestClient
from test_channel_audio_ingress import _Audio, _audio_runtime, _message
from test_channel_group_migration import _snapshot
from test_channel_groups import _group, _open
from test_group_discussion import _message as _discussion_message
from test_group_discussion import _route
from test_qq_channels import (
    SOURCE_URL,
    _ingest,
    _pair,
    _public_source,
    _public_web_settings,
    _runtime,
    _segments,
    _source_model,
    _terminal,
)


def test_operator_api_defaults_validation_cas_and_restart(runtime_settings: Settings) -> None:
    app = create_app(_public_web_settings(runtime_settings))
    container = app.state.container
    with TestClient(
        app, headers={"Authorization": f"Bearer {container.capability_token}"}
    ) as client:
        original = client.get("/v1/channels/settings").json()
        assert original["revision"] == 0 and original["updated_at"] is None
        assert original["policy"]["qq_owner_public_web_enabled"] is True
        assert original["policy"]["group_discussion"]["input_tokens"] == 1536
        policy = {**original["policy"], "qq_owner_voice_reply_enabled": False}
        changed = client.put(
            "/v1/channels/settings", json={"expected_revision": 0, "policy": policy}
        )
        assert changed.status_code == 200
        assert changed.json()["revision"] == 1
        assert changed.json()["policy"] == policy
        assert changed.json()["updated_at"] is not None
        assert (
            client.put(
                "/v1/channels/settings", json={"expected_revision": 0, "policy": original["policy"]}
            ).status_code
            == 409
        )
        for invalid in (
            {**policy, "qq_owner_voice_reply_enabled": "false"},
            {**policy, "group_discussion": {"cache_messages": 32, "member_messages": 33}},
            {**policy, "group_discussion": {"input_tokens": 8193}},
            {**policy, "group_discussion": {"enabled": "false"}},
            {**policy, "account_key": "unauthorized"},
        ):
            assert (
                client.put(
                    "/v1/channels/settings", json={"expected_revision": 1, "policy": invalid}
                ).status_code
                == 422
            )
        assert client.get("/v1/channels/settings").json() == changed.json()
        # These settings do not create sessions, model generations or skill runs.
        assert client.portal is not None
        counts = client.portal.call(
            container.database.fetchone,
            "SELECT (SELECT COUNT(*) FROM sessions), (SELECT COUNT(*) FROM generations),"
            "(SELECT COUNT(*) FROM skill_runs)",
        )
        assert counts is not None and tuple(counts) == (0, 0, 0)
        assert not any(word in changed.text for word in ("api_key", "access_token", "base_url"))

    restarted = create_app(runtime_settings)  # TOML defaults now disagree with the saved policy.
    with TestClient(
        restarted,
        headers={"Authorization": f"Bearer {restarted.state.container.capability_token}"},
    ) as client:
        assert client.get("/v1/channels/settings").json()["policy"] == policy


def test_operator_api_rejects_channel_token_and_untrusted_origin(client: TestClient) -> None:
    policy = client.get("/v1/channels/settings").json()["policy"]
    for method in ("GET", "PUT"):
        body = {"expected_revision": 0, "policy": policy} if method == "PUT" else None
        assert (
            client.request(
                method,
                "/v1/channels/settings",
                json=body,
                headers={"Authorization": "Bearer ingress-only"},
            ).status_code
            == 401
        )
        assert (
            client.request(
                method,
                "/v1/channels/settings",
                json=body,
                headers={"Origin": "https://untrusted.example"},
            ).status_code
            == 403
        )
    assert client.get("/v1/channels/settings").json()["revision"] == 0


def test_group_policy_environment_keeps_typed_overrides(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = tmp_path / "settings.toml"
    config.write_text("[group_discussion]\ninput_tokens = 2048\n", encoding="utf-8")
    monkeypatch.setenv("CHATWAIFU_GROUP_DISCUSSION__INPUT_TOKENS", "4096")
    monkeypatch.setenv("CHATWAIFU_GROUP_DISCUSSION__ENABLED", "false")
    settings = load_settings(config, env_path=tmp_path / "missing.env")
    assert settings.group_discussion.input_tokens == 4096
    assert settings.group_discussion.enabled is False


async def test_cas_winner_refresh_and_cancelled_commit_publish_under_writer_lock(
    runtime_settings: Settings,
) -> None:
    container = RuntimeContainer(runtime_settings)
    await container.start()
    try:
        repository = SQLiteChannelSettingsRepository(container.database)
        stale = ChannelSettingsService(repository, ChannelRuntimePolicy())
        await stale.start()
        first = await container.channel_settings.update(
            ChannelRuntimeSettingsUpdate(expected_revision=0, policy=ChannelRuntimePolicy())
        )
        with pytest.raises(ChannelConflictError):
            await stale.update(
                ChannelRuntimeSettingsUpdate(expected_revision=0, policy=ChannelRuntimePolicy())
            )
        assert stale.get() == first

        entered, release, followed = asyncio.Event(), asyncio.Event(), asyncio.Event()

        async def publish(_old: ChannelRuntimePolicy, current: ChannelRuntimePolicy) -> None:
            if not current.qq_owner_voice_reply_enabled:
                entered.set()
                await release.wait()
            else:
                followed.set()

        stale.set_apply_callback(publish)
        revoke = asyncio.create_task(
            stale.update(
                ChannelRuntimeSettingsUpdate(
                    expected_revision=1,
                    policy=ChannelRuntimePolicy(qq_owner_voice_reply_enabled=False),
                )
            )
        )
        await asyncio.wait_for(entered.wait(), 3)
        revoke.cancel()
        assert stale.get().policy.qq_owner_voice_reply_enabled is False
        next_save = asyncio.create_task(
            stale.update(
                ChannelRuntimeSettingsUpdate(expected_revision=2, policy=ChannelRuntimePolicy())
            )
        )
        await asyncio.sleep(0)  # Scheduler yield; the next writer cannot overtake publication.
        assert not followed.is_set() and not next_save.done()
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await revoke
        assert (await asyncio.wait_for(next_save, 3)).revision == 3
        assert followed.is_set()
        assert await repository.get() == stale.get()
    finally:
        await container.stop()


async def test_budget_edit_clears_cache_only_when_policy_changes(
    runtime_settings: Settings,
) -> None:
    container = RuntimeContainer(runtime_settings)
    await container.start()
    try:
        route, now = _route(), datetime.now(UTC)
        groups = container.channel_groups
        old = groups._discussion
        old.observe(_discussion_message(route, 1, "old scope sentinel", now), route, 1, now)
        assert old.snapshot(route, 1, "2", now) is not None
        policy = container.channel_settings.get().policy
        await container.channel_settings.update(
            ChannelRuntimeSettingsUpdate(
                expected_revision=0,
                policy=policy.model_copy(update={"qq_native_favorites_enabled": False}),
            )
        )
        assert groups._discussion is old
        changed = ChannelRuntimePolicy.model_validate(
            {
                **policy.model_dump(),
                "group_discussion": {"input_tokens": 4096, "message_characters": 80},
            }
        )
        await container.channel_settings.update(
            ChannelRuntimeSettingsUpdate(expected_revision=1, policy=changed)
        )
        assert groups._discussion.snapshot(route, 1, "2", now) is None
        assert old.snapshot(route, 1, "2", now) is not None  # Admitted snapshot remains frozen.
        groups._discussion.observe(_discussion_message(route, 3, "长" * 200, now), route, 1, now)
        fresh = groups._discussion.snapshot(route, 1, "4", now)
        assert fresh is not None and len(fresh.messages[0].text) == 80
        assert fresh.policy.input_tokens == 4096
    finally:
        await container.stop()


async def test_voice_reply_toggle_updates_next_model_tools_and_actual_delivery(
    runtime_settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    async with _runtime(runtime_settings, monkeypatch) as harness:
        cid = await _pair(harness)
        harness.model.voice_decision = True
        await harness.container.channel_settings.update(
            ChannelRuntimeSettingsUpdate(
                expected_revision=0, policy=ChannelRuntimePolicy(qq_owner_voice_reply_enabled=False)
            )
        )
        first = await _ingest(harness, cid, "请用语音回复我", 501)
        sent = await asyncio.wait_for(harness.peer.sends.get(), 5)
        assert _segments(sent)[0]["type"] == "text"
        assert (
            await _terminal(harness, cid, first.channel_turn_id)
        ).status is ChannelTurnStatus.COMPLETED
        assert not harness.model.requests[0].tools and not harness.synthesis
        await harness.container.channel_settings.update(
            ChannelRuntimeSettingsUpdate(expected_revision=1, policy=ChannelRuntimePolicy())
        )
        second = await _ingest(harness, cid, "请用语音回复我", 502)
        sent = await asyncio.wait_for(harness.peer.sends.get(), 5)
        assert _segments(sent)[0]["type"] == "record"
        assert (
            await _terminal(harness, cid, second.channel_turn_id)
        ).status is ChannelTurnStatus.COMPLETED
        assert len(harness.synthesis) == 1


async def test_voice_revocation_during_synthesis_prevents_late_audio_delivery(
    runtime_settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    async with _runtime(runtime_settings, monkeypatch) as harness:
        cid = await _pair(harness)
        entered, release = asyncio.Event(), asyncio.Event()
        original = harness.container.providers.tts.synthesize

        async def synthesize(request: SynthesisRequest) -> SynthesisResult:
            entered.set()
            await release.wait()
            return await original(request)

        monkeypatch.setattr(harness.container.providers.tts, "synthesize", synthesize)
        harness.model.voice_decision = True
        receipt = await _ingest(harness, cid, "请用语音回复我", 508)
        await asyncio.wait_for(entered.wait(), 3)
        await harness.container.channel_settings.update(
            ChannelRuntimeSettingsUpdate(
                expected_revision=0, policy=ChannelRuntimePolicy(qq_owner_voice_reply_enabled=False)
            )
        )
        release.set()
        sent = await asyncio.wait_for(harness.peer.sends.get(), 5)
        assert all(part["type"] == "text" for part in _segments(sent))
        result = await _terminal(harness, cid, receipt.channel_turn_id)
        assert result.status is ChannelTurnStatus.COMPLETED
        assert harness.peer.sends.empty()
        assert len(harness.model.requests) == 2


async def test_voice_revocation_during_source_read_denies_new_synthesis(
    runtime_settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    async with _runtime(runtime_settings, monkeypatch) as harness:
        cid = await _pair(harness)
        harness.model.release = asyncio.Event()
        harness.model.voice_decision = False
        receipt = await _ingest(harness, cid, "请用语音回复我", 510)
        await asyncio.wait_for(harness.model.received.get(), 5)
        turn = await harness.container.external_channel_repository.get_turn(receipt.channel_turn_id)
        assert turn is not None
        context = GenerationSkillContext(turn.session_id, turn.turn_id, turn.generation_id, "agent")
        voice = harness.container.channel_voice
        assert await voice.authorize(context)
        entered, release = asyncio.Event(), asyncio.Event()
        repository = harness.container.conversation_repository
        original = repository.generation_user_input_context

        async def source(generation: UUID) -> ConversationUserInputContext | None:
            result = await original(generation)
            entered.set()
            await release.wait()
            return result

        monkeypatch.setattr(repository, "generation_user_input_context", source)
        task = asyncio.create_task(voice(context, {"text": "晚安"}))
        await asyncio.wait_for(entered.wait(), 5)
        await harness.container.channel_settings.update(
            ChannelRuntimeSettingsUpdate(
                expected_revision=0, policy=ChannelRuntimePolicy(qq_owner_voice_reply_enabled=False)
            )
        )
        release.set()
        with pytest.raises(SkillExecutionError) as denied:
            await task
        assert denied.value.structured.code == "voice_not_authorized"
        assert not harness.synthesis
        harness.model.release.set()
        await asyncio.wait_for(harness.peer.sends.get(), 5)
        await _terminal(harness, cid, receipt.channel_turn_id)


@pytest.mark.parametrize("revocation_point", ["before_check", "during_binding_read"])
async def test_web_revocation_between_tool_selection_and_execution_prevents_provider_io(
    runtime_settings: Settings, monkeypatch: pytest.MonkeyPatch, revocation_point: str
) -> None:
    reads: list[JsonObject] = []

    async def read(_self: PublicWebReader, arguments: JsonObject) -> JsonObject:
        reads.append(arguments)
        return _public_source()

    monkeypatch.setattr(PublicWebReader, "read", read)
    async with _runtime(_public_web_settings(runtime_settings), monkeypatch) as harness:
        cid = await _pair(harness)
        _source_model(harness, monkeypatch)
        permission = harness.container.runtime_skills._generation_permission_policy
        assert permission is not None
        checks = 0

        async def disable_web() -> None:
            await harness.container.channel_settings.update(
                ChannelRuntimeSettingsUpdate(expected_revision=0, policy=ChannelRuntimePolicy())
            )

        async def revoke(context: GenerationSkillContext, skill: str) -> bool:
            nonlocal checks
            checks += 1
            if checks == 2:
                if revocation_point == "before_check":
                    await disable_web()
                else:
                    repository = harness.container.external_channel_repository
                    original_binding = repository.find_binding
                    revoked = False

                    async def binding(
                        connection_id: UUID, conversation_key: str
                    ) -> ChannelBindingRecord | None:
                        nonlocal revoked
                        result = await original_binding(connection_id, conversation_key)
                        if not revoked:
                            revoked = True
                            await disable_web()
                        return result

                    monkeypatch.setattr(repository, "find_binding", binding)
            return await permission(context, skill)

        monkeypatch.setattr(
            harness.container.runtime_skills, "_generation_permission_policy", revoke
        )
        receipt = await _ingest(harness, cid, f"请读取 {SOURCE_URL}", 503)
        await asyncio.wait_for(harness.peer.sends.get(), 5)
        await _terminal(harness, cid, receipt.channel_turn_id)
        assert checks == 2 and not reads
        await _ingest(harness, cid, "下一句只需文字", 504)
        await asyncio.wait_for(harness.peer.sends.get(), 5)
        assert not any(
            "url" in str(tool.input_schema.get("properties", {}))
            for tool in harness.model.requests[-1].tools
        )


async def test_audio_revocation_cancels_preprocessing_and_denies_new_audio_before_loader(
    runtime_settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    async with _audio_runtime(runtime_settings, monkeypatch) as (harness, cid, token):
        audio = _Audio()
        receipt = await harness.container.external_channels.ingest(
            _message(cid, 505), access_token=token, audio_input=audio.input()
        )
        await asyncio.wait_for(audio.entered.wait(), 3)
        await harness.container.channel_settings.update(
            ChannelRuntimeSettingsUpdate(
                expected_revision=0, policy=ChannelRuntimePolicy(qq_owner_voice_input_enabled=False)
            )
        )
        audio.release.set()
        result = await _terminal(harness, cid, receipt.channel_turn_id)
        assert result.status is ChannelTurnStatus.CANCELLED and audio.cancelled == 1
        assert not harness.model.requests and harness.peer.sends.empty()
        other = _Audio()
        with pytest.raises(ChannelPolicyError):
            await harness.container.external_channels.ingest(
                _message(cid, 506), access_token=token, audio_input=other.input()
            )
        assert not other.identities
        text = await _ingest(harness, cid, "文字仍然可用", 507)
        await asyncio.wait_for(harness.peer.sends.get(), 5)
        assert (
            await _terminal(harness, cid, text.channel_turn_id)
        ).status is ChannelTurnStatus.COMPLETED


async def test_populated_43_to_44_preserves_channels_history_and_migration_ledger(
    tmp_path: Path,
) -> None:
    path = tmp_path / "settings-migration.db"
    before = await _open(path, through=43)
    try:
        group = await _group(before)
        admitted = await group.repository.admit_group_turn(group.admission("111", "1"))
        columns, facts = await _snapshot(before)
        ledger = [tuple(row) for row in await before.fetchall("SELECT * FROM schema_migrations")]
    finally:
        await before.close()
    after = await _open(path, through=44)
    try:
        assert await _snapshot(after, columns) == (columns, facts)
        assert [
            tuple(row)
            for row in await after.fetchall("SELECT * FROM schema_migrations WHERE version<=43")
        ] == ledger
        assert await after.fetchall("PRAGMA foreign_key_check") == []
        repository = SQLiteChannelSettingsRepository(after)
        assert await repository.get() is None
        assert admitted.turn.channel_turn_id is not None
        assert await repository.save(ChannelRuntimePolicy(), 1, datetime.now(UTC)) is None
        saved = await repository.save(ChannelRuntimePolicy(), 0, datetime.now(UTC))
        assert saved is not None and saved.revision == 1
        assert await repository.get() == saved
    finally:
        await after.close()
