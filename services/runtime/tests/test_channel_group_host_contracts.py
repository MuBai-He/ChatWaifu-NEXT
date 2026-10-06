"""Trusted transport admission and the pre-reset revocation boundary."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from chatwaifu_protocol.channel_groups import ChannelGroupPauseReason
from chatwaifu_protocol.channels import ChannelConnectionConfiguration, ChannelConnectionStatus
from chatwaifu_protocol.session import GenerationState, SessionSnapshot
from chatwaifu_runtime.bootstrap.container import RuntimeContainer
from chatwaifu_runtime.config.settings import Settings
from chatwaifu_runtime.external_channels.service import (
    ChannelAuthenticationError,
    ChannelPolicyError,
)

from services.runtime.tests.test_channel_groups import _group  # type: ignore[reportPrivateUsage]
from services.runtime.tests.test_conversation_shared_external import (
    bind_provider,
    join,
    options_for,
    scene_sessions,
)


@pytest.mark.parametrize(
    "mode", ["ready", "wrong_token", "other_provider", "disabled", "degraded", "bad_account"]
)
async def test_group_transport_authenticates_without_granting_owner_or_creating_work(
    runtime_settings: Settings, mode: str
) -> None:
    container = RuntimeContainer(runtime_settings)
    await container.start()
    try:
        connection_id = uuid4()
        created = await container.external_channels.create_connection(
            ChannelConnectionConfiguration(
                connection_id=connection_id,
                provider_id="weixin_ilink" if mode == "other_provider" else "qq_napcat",
                name="fixture",
                character_id="default",
                principal_scope="local",
                account_key="00900" if mode == "bad_account" else "900",
                # The group domain separately grants registered participants.
                allowed_sender_keys=["999"],
                enabled=mode != "disabled",
            )
        )
        await container.external_channel_repository.set_connection_status(
            connection_id,
            status=ChannelConnectionStatus.DEGRADED
            if mode == "degraded"
            else ChannelConnectionStatus.READY,
            last_error=None,
            updated_at=datetime.now(UTC),
        )
        if mode == "ready":
            result = await container.external_channels.authenticate_group_transport(
                connection_id, created.access_token
            )
            assert result.configuration.account_key == "900"
            assert result.configuration.allowed_sender_keys == ["999"]
        else:
            error = ChannelAuthenticationError if mode == "wrong_token" else ChannelPolicyError
            with pytest.raises(error):
                await container.external_channels.authenticate_group_transport(
                    connection_id, "wrong" if mode == "wrong_token" else created.access_token
                )
        assert container.conversation.active_count == 0
        row = await container.database.fetchone("SELECT count(*) AS n FROM channel_turns")
        assert row is not None and row["n"] == 0
        row = await container.database.fetchone("SELECT count(*) AS n FROM sessions")
        assert row is not None and row["n"] == 0
    finally:
        await container.stop()


async def test_scope_reset_awaits_revocation_before_deleting_shared_generation(
    runtime_settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    container = RuntimeContainer(runtime_settings)
    await container.start()
    bind_provider(container, monkeypatch)
    entered, release = asyncio.Event(), asyncio.Event()
    reset_task: asyncio.Task[object] | None = None
    try:
        alice, _ = await scene_sessions(container)
        accepted = await container.conversation.submit_text(
            alice.session_id, "shared fact", options=await options_for(container, alice)
        )
        await join(container, alice.session_id)

        async def fence(session: SessionSnapshot) -> None:
            assert session.scene_id == alice.scene_id
            retained = await container.conversation_repository.generation_result(
                accepted.generation_id
            )
            assert retained is not None and retained.state is GenerationState.COMPLETED
            entered.set()
            await release.wait()

        container.conversation.set_before_scope_reset_hook(fence)
        reset_task = asyncio.create_task(container.conversation.reset(alice.session_id))
        await asyncio.wait_for(entered.wait(), 2)
        assert not reset_task.done()
        assert await container.conversation_repository.generation_result(accepted.generation_id)
        release.set()
        await asyncio.wait_for(reset_task, 2)
        assert (
            await container.conversation_repository.generation_result(accepted.generation_id)
            is None
        )
    finally:
        release.set()
        if reset_task is not None and not reset_task.done():
            reset_task.cancel()
            await asyncio.gather(reset_task, return_exceptions=True)
        await container.stop()


async def test_failed_scope_fence_prevents_reset_and_preserves_durable_history(
    runtime_settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    container = RuntimeContainer(runtime_settings)
    await container.start()
    bind_provider(container, monkeypatch)
    try:
        alice, _ = await scene_sessions(container)
        accepted = await container.conversation.submit_text(
            alice.session_id, "retained fact", options=await options_for(container, alice)
        )
        await join(container, alice.session_id)

        async def failed_fence(_session: SessionSnapshot) -> None:
            raise RuntimeError("revocation did not commit")

        container.conversation.set_before_scope_reset_hook(failed_fence)
        with pytest.raises(RuntimeError, match="revocation did not commit"):
            await container.conversation.reset(alice.session_id)
        retained = await container.conversation_repository.generation_result(accepted.generation_id)
        assert retained is not None and retained.state is GenerationState.COMPLETED
        row = await container.database.fetchone(
            "SELECT count(*) AS n FROM events WHERE event_type='session.data_reset'"
        )
        assert row is not None and row["n"] == 0
    finally:
        await container.stop()


@pytest.mark.parametrize("mode", ["enabled", "disabled", "scene_reset", "deleted"])
async def test_group_scene_excludes_desktop_proactive_before_any_member_binding(
    runtime_settings: Settings, mode: str
) -> None:
    container = RuntimeContainer(runtime_settings)
    await container.start()
    try:
        group = await _group(container.database)
        if mode != "enabled":
            await container.channel_group_repository.pause_routes(
                connection_id=group.connection_id,
                reason={
                    "disabled": ChannelGroupPauseReason.OPERATOR_DISABLED,
                    "scene_reset": ChannelGroupPauseReason.SCENE_RESET,
                    "deleted": ChannelGroupPauseReason.ROUTE_DELETED,
                }[mode],
                updated_at=datetime.now(UTC),
            )
        row = await container.database.fetchone("SELECT count(*) AS n FROM channel_bindings")
        assert row is not None and row["n"] == 0
        for session_id in group.sessions.values():
            assert not await container._desktop_proactive_session_allowed(session_id)  # type: ignore[reportPrivateUsage]
        private = await container.sessions.create_session("default")
        assert await container._desktop_proactive_session_allowed(private.session_id)  # type: ignore[reportPrivateUsage]
        # Ordinary desktop shared scenes do not inherit a group route's exclusion.
        alice, bob = await scene_sessions(container)
        assert await container._desktop_proactive_session_allowed(alice.session_id)  # type: ignore[reportPrivateUsage]
        assert await container._desktop_proactive_session_allowed(bob.session_id)  # type: ignore[reportPrivateUsage]
    finally:
        await container.stop()
