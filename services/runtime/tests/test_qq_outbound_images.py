"""Character-planned QQ stickers through the real Runtime and local OneBot socket."""

# pyright: reportPrivateUsage=false

from __future__ import annotations

import asyncio
import base64
import hashlib
from typing import cast
from uuid import UUID

import pytest
from chatwaifu_protocol.base import JsonObject
from chatwaifu_protocol.channels import (
    ChannelDeliveryPartKind,
    ChannelDeliveryPartStatus,
    ChannelDeliveryStatus,
    ChannelPresentationPolicy,
    ChannelPresentationProfile,
)
from chatwaifu_protocol.character import ResponsePlan
from chatwaifu_runtime.config.settings import Settings
from test_qq_channels import TEXT_REPLY, _ingest, _pair, _runtime, _segments


@pytest.mark.asyncio
@pytest.mark.parametrize("scenario", ["enabled", "disabled", "missing_image"])
async def test_character_planned_optional_image_preserves_qq_text_reply(
    runtime_settings: Settings, monkeypatch: pytest.MonkeyPatch, scenario: str
) -> None:
    async with _runtime(runtime_settings, monkeypatch, max_size=8 * 1024 * 1024) as harness:
        container = harness.container
        assert container.qq_channels._sticker_catalog is container.sticker_catalog
        assert container.qq_channels._sticker_library is container.sticker_library
        entry = next(
            item
            for item in container.sticker_catalog.load_manifest()
            if "happy" in item.expressions
        )
        original_bytes = container.sticker_catalog.load_sticker_bytes(
            entry.sticker_id, entry.sha256
        )
        assert original_bytes is not None
        connection_id = await _pair(harness)
        current = await container.external_channels.get_connection(connection_id)
        await container.external_channels.update_connection(
            current.configuration.model_copy(
                update={
                    "presentation_policy": ChannelPresentationPolicy(
                        profile=ChannelPresentationProfile.INSTANT_MESSAGE,
                        stickers_enabled=scenario != "disabled",
                        cadence_enabled=False,
                    )
                }
            ),
            expected_revision=current.revision,
            rotate_access_token=False,
        )

        async def response_plan(_generation_id: UUID) -> ResponsePlan:
            return ResponsePlan(
                intent="celebrate", tone="bright", expression="happy", rationale="fixture"
            )

        monkeypatch.setattr(
            container.conversation_repository, "generation_response_plan", response_plan
        )
        if scenario == "missing_image":

            def missing(_id: str, _sha: str) -> None:
                return None

            monkeypatch.setattr(container.sticker_catalog, "load_sticker_bytes", missing)
        completed = container.event_hub.subscribe(
            lambda event: event.get("event_type") == "channel.delivery_plan_completed", queue_size=8
        )
        try:
            receipt = await _ingest(harness, connection_id, "今天很开心！", 71)
            text = await asyncio.wait_for(harness.peer.sends.get(), timeout=5)
            assert _segments(text) == [{"type": "text", "data": {"text": TEXT_REPLY}}]
            if scenario == "enabled":
                params = await asyncio.wait_for(harness.peer.sends.get(), timeout=5)
                segments = _segments(params)
                assert len(segments) == 1 and segments[0]["type"] == "image"
                file = cast(JsonObject, segments[0]["data"])["file"]
                assert isinstance(file, str) and file.startswith("base64://")
                sent = base64.b64decode(file.removeprefix("base64://"), validate=True)
                assert sent == original_bytes and hashlib.sha256(sent).hexdigest() == entry.sha256
            event = await asyncio.wait_for(completed.receive(), timeout=5)
            payload = cast(JsonObject, event["payload"])
            assert payload["channel_turn_id"] == str(receipt.channel_turn_id)
            turn = await container.external_channel_repository.get_turn(receipt.channel_turn_id)
            assert (
                turn is not None and turn.reply_text == TEXT_REPLY and turn.delivery_id is not None
            )
            plan = await container.external_channel_repository.get_delivery_plan(turn.delivery_id)
            assert plan is not None and plan.status is ChannelDeliveryStatus.DELIVERED
            assert plan.parts[0].kind is ChannelDeliveryPartKind.TEXT
            assert plan.parts[0].status is ChannelDeliveryPartStatus.DELIVERED
            if scenario == "disabled":
                assert len(plan.parts) == 1
            else:
                assert len(plan.parts) == 2
                image = plan.parts[1]
                assert image.kind is ChannelDeliveryPartKind.IMAGE and not image.required
                expected = (
                    ChannelDeliveryPartStatus.DELIVERED
                    if scenario == "enabled"
                    else ChannelDeliveryPartStatus.FAILED
                )
                assert image.status is expected
                if scenario == "missing_image":
                    assert image.last_error is not None
                    assert image.last_error.code == "qq_image_unavailable"
            assert harness.peer.sends.empty()
            assert not harness.synthesis
            assert len(harness.model.requests) == 1
            assert not harness.model.requests[0].tools
        finally:
            container.event_hub.unsubscribe(completed)
