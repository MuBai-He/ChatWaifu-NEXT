"""Account operations require explicit policy, identity, valid inputs and real results."""

from pathlib import Path
from typing import Any, cast
from uuid import uuid4

import pytest
from chatwaifu_protocol.agent import CapabilityStatus, TaskChannelBinding
from chatwaifu_protocol.base import JsonObject
from chatwaifu_protocol.channel_settings import ChannelRuntimeSettingsUpdate
from chatwaifu_protocol.skills import SkillInvocation
from chatwaifu_runtime.bootstrap.container import RuntimeContainer
from chatwaifu_runtime.config.settings import Settings
from chatwaifu_runtime.external_channels.adapters.qq_napcat.client import NapCatError
from chatwaifu_runtime.external_channels.qq_account import QQAccountCapabilities
from chatwaifu_runtime.runtime_skills.adapters import GenerationSkillContext
from chatwaifu_runtime.runtime_skills.agent_router import project_capability_candidate


def _account(group: bool = True) -> tuple[QQAccountCapabilities, Any, list[JsonObject]]:
    class Scene:
        enabled = True
        binding_value = TaskChannelBinding(
            connection_id=uuid4(),
            account_key="10001",
            conversation_key="group:20001" if group else "direct:30001",
            sender_key="30001",
            source_ref="message-1",
            route_id=uuid4() if group else None,
        )

        async def authorize(self, context: GenerationSkillContext) -> bool:
            return self.enabled

        async def binding(self, context: GenerationSkillContext) -> TaskChannelBinding | None:
            return self.binding_value if self.enabled else None

    scene = Scene()
    calls: list[JsonObject] = []

    async def call(
        connection: Any, account: str, action: str, params: JsonObject, guard: Any, version: str
    ) -> JsonObject:
        assert await guard()
        assert account == "10001" and version == "4.18.28"
        calls.append({"action": action, "params": params})
        return {"accepted": True}

    api = Path(__file__).resolve().parents[3] / "skills/builtin/qq-account/openapi.json"
    handler = QQAccountCapabilities(cast(Any, scene), lambda: scene.enabled, call, api)
    return handler, scene, calls


@pytest.mark.parametrize("group", [True, False])
async def test_poke_uses_real_current_sender_and_group(group: bool) -> None:
    handler, _, calls = _account(group)
    result = await handler(
        GenerationSkillContext(uuid4(), uuid4(), uuid4(), "agent"),
        {"action": "send_poke", "params": {}},
    )
    assert result["executed"] is True
    expected: JsonObject = {"user_id": "30001"}
    if group:
        expected["group_id"] = "20001"
    assert calls == [{"action": "send_poke", "params": expected}]


async def test_account_scope_accepts_other_qq_targets_and_rejects_invalid_inputs() -> None:
    handler, _, calls = _account()
    context = GenerationSkillContext(uuid4(), uuid4(), uuid4(), "agent")
    await handler(
        context, {"action": "send_poke", "params": {"group_id": "40001", "user_id": "50001"}}
    )
    assert calls[0]["params"] == {"group_id": "40001", "user_id": "50001"}
    for arguments in [
        {"action": "send_poke", "params": {"user_id": False}},
        {"action": "send_poke", "params": {"account_key": "fake"}},
        {"action": "get_credentials", "params": {"domain": "qq.com"}},
        {"action": "set_qq_avatar", "params": {"file": "/etc/passwd"}},
        {"action": "send_msg", "params": {"message": "[CQ:image,file=/etc/passwd]"}},
    ]:
        with pytest.raises((ValueError, PermissionError)):
            await handler(context, cast(JsonObject, arguments))
    assert len(calls) == 1


async def test_revoked_account_never_calls_and_provider_failure_never_becomes_success() -> None:
    handler, scene, calls = _account()
    context = GenerationSkillContext(uuid4(), uuid4(), uuid4(), "agent")
    scene.enabled = False
    with pytest.raises(PermissionError):
        await handler(context, {"action": "send_poke", "params": {}})
    assert calls == []
    scene.enabled = True

    async def failed(*args: Any) -> JsonObject:
        raise NapCatError("provider rejected")

    handler.call = failed
    with pytest.raises(NapCatError, match="provider rejected"):
        await handler(context, {"action": "send_poke", "params": {}})


async def test_policy_survives_restart_and_controls_discovery(runtime_settings: Settings) -> None:
    container = RuntimeContainer(runtime_settings)
    await container.start()
    try:
        assert (
            container.capabilities.inspect("qq.account/send_poke").descriptor.status
            is CapabilityStatus.NOT_CONFIGURED
        )
        before = container.channel_settings.get()
        changed = await container.channel_settings.update(
            ChannelRuntimeSettingsUpdate(
                expected_revision=before.revision,
                policy=before.policy.model_copy(update={"qq_account_enabled": True}),
            )
        )
        assert changed.policy.qq_account_enabled
        assert not changed.policy.qq_owner_agent_enabled
        skill = next(s for s in container.runtime_skills.list() if s.skill_id == "qq.account")
        assert len(skill.capabilities) == 158
        for capability in skill.capabilities:
            projection = project_capability_candidate(
                skill, capability, query="", require_relevance=False
            )
            assert projection is not None, capability.name
            assert "no separate desktop confirmation" in projection.description
        assert not changed.policy.qq_owner_public_web_enabled
    finally:
        await container.stop()
    restarted = RuntimeContainer(runtime_settings)
    await restarted.start()
    try:
        assert restarted.channel_settings.get().policy.qq_account_enabled
    finally:
        await restarted.stop()


async def test_runtime_records_actual_account_execution_without_desktop_confirmation(
    runtime_settings: Settings,
) -> None:
    container = RuntimeContainer(runtime_settings)
    await container.start()
    handler, _, calls = _account()
    container.qq_account_capabilities.scene = handler.scene
    container.qq_account_capabilities.call = handler.call
    try:
        before = container.channel_settings.get()
        await container.channel_settings.update(
            ChannelRuntimeSettingsUpdate(
                expected_revision=before.revision,
                policy=before.policy.model_copy(update={"qq_account_enabled": True}),
            )
        )
        session = await container.sessions.create_session("default")
        run = await container.runtime_skills.invoke(
            session.session_id,
            SkillInvocation(
                skill_id="qq.account",
                capability="send_poke",
                arguments={"action": "send_poke", "params": {}},
            ),
            origin="agent",
            turn_id=uuid4(),
            generation_id=uuid4(),
            allow_confirmation=False,
        )
        run = await container.runtime_skills.wait_for_terminal(run.skill_run_id)
        assert run.state.value == "succeeded"
        assert run.confirmation_request_id is None
        assert calls == [
            {"action": "send_poke", "params": {"user_id": "30001", "group_id": "20001"}}
        ]
    finally:
        await container.stop()
