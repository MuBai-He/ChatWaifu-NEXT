"""Actual owner/group admission, scoped image learning and receipt-based reuse.

The classifier and native favorite acceptance are controlled here; model quality
and the real QQ account remain separate gates. No external messages are sent.
"""
# pyright: reportPrivateUsage=false

from __future__ import annotations

import asyncio
import base64
import hashlib
import io
import json
from collections.abc import Awaitable, Callable
from datetime import timedelta
from typing import cast
from uuid import UUID

import pytest
from chatwaifu_protocol.base import JsonObject, JsonValue
from chatwaifu_protocol.channel_groups import ChannelGroupPauseReason, ChannelGroupRouteSnapshot
from chatwaifu_protocol.channels import (
    ChannelConnectionSnapshot,
    ChannelDeliveryPartStatus,
    ChannelImageDeliveryPartPayload,
    ChannelPresentationPolicy,
    ChannelPresentationProfile,
    ChannelTurnStatus,
)
from chatwaifu_runtime.config.settings import Settings
from chatwaifu_runtime.external_channels.adapters.qq_napcat.client import NapCatClient
from chatwaifu_runtime.external_channels.group_models import ChannelGroupInboundDescriptor
from chatwaifu_runtime.external_channels.models import ChannelInboundImageInput
from chatwaifu_runtime.providers.contracts import LlmInputImage
from chatwaifu_runtime.sticker_library.classifier import StickerClassification
from PIL import Image
from test_qq_channels import _image_event, _ingest, _pair, _picture, _runtime, _segments, _terminal
from test_qq_group_runtime import GROUP, OTHER_GROUP, _group_event, _notice, _Runtime
from test_qq_group_runtime import runtime as runtime


def _classification() -> StickerClassification:
    return StickerClassification(
        suitable=True, confidence=0.99, label="开心猫", description="开心猫表情", expression="happy"
    )


@pytest.mark.asyncio
async def test_private_animated_gif_reaches_model_without_learning(
    runtime_settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async with _runtime(runtime_settings, monkeypatch) as harness:
        container = harness.container
        connection_id = await _pair(harness)
        await container.sticker_repository.update_settings(
            "local", "default", learning_enabled=True, expected_revision=0
        )
        output = io.BytesIO()
        Image.new("RGB", (44, 44), "red").save(
            output,
            format="GIF",
            save_all=True,
            append_images=[Image.new("RGB", (44, 44), "blue")],
            duration=100,
        )

        async def download(_client: NapCatClient, _ref: str, *, max_bytes: int) -> bytes:
            return output.getvalue()

        async def classify(*_args: object, **_kwargs: object) -> None:
            raise AssertionError("Animated source must never reach the sticker classifier")

        monkeypatch.setattr(NapCatClient, "download_image", download)
        monkeypatch.setattr(container.sticker_library._classifier, "classify", classify)
        await harness.peer.peers[-1].send(json.dumps(_image_event("看看表情", 1801)))
        request = await asyncio.wait_for(harness.model.received.get(), 5)
        assert len(request.images) == 1 and request.images[0].data.startswith(b"\x89PNG")
        await asyncio.wait_for(harness.peer.sends.get(), 5)
        turn = await container.external_channel_repository.find_turn_by_external_message(
            connection_id, "1801"
        )
        assert turn is not None
        assert (
            await _terminal(harness, connection_id, turn.channel_turn_id)
        ).status is ChannelTurnStatus.COMPLETED
        assert not container.sticker_library._tasks
        assert not (await container.sticker_repository.snapshot("local", "default")).items


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "case", ["same_sender", "other_sender", "expired", "fenced", "policy_changed", "rapid"]
)
async def test_group_separate_image_reference_is_lazy_scoped_bounded_and_fenced(
    runtime: _Runtime,
    monkeypatch: pytest.MonkeyPatch,
    case: str,
) -> None:
    from test_qq_group_runtime import ALICE, BOB

    harness = runtime
    container = harness.container
    route = await harness.route()
    await harness.request(
        "PUT",
        "/v1/sticker-library/settings" + _scope_query(route),
        {"learning_enabled": True, "expected_revision": 0},
        status=200,
    )
    image = _picture()
    calls: list[str] = []

    async def download(_client: NapCatClient, ref: str, *, max_bytes: int) -> bytes:
        calls.append(ref)
        return image

    async def classify(*_args: object, **_kwargs: object) -> None:
        return None

    monkeypatch.setattr(NapCatClient, "download_image", download)
    monkeypatch.setattr(container.sticker_library._classifier, "classify", classify)
    event = _group_image(1802, image)
    event["message"] = [
        segment for segment in cast(list[JsonObject], event["message"]) if segment["type"] != "at"
    ]
    entered, release = asyncio.Event(), asyncio.Event()
    if case == "rapid":
        observe = container.channel_groups.observe_group_image_reference

        async def held_observe(
            descriptor: ChannelGroupInboundDescriptor,
            *,
            access_token: str,
            image_input: ChannelInboundImageInput,
        ) -> None:
            entered.set()
            await release.wait()
            await observe(descriptor, access_token=access_token, image_input=image_input)

        monkeypatch.setattr(container.channel_groups, "observe_group_image_reference", held_observe)
        await harness.send(event, join_admission=False)
        await asyncio.wait_for(entered.wait(), 5)
    else:
        await harness.send(event)
    assert not calls and not harness.model.requests
    assert (
        await container.channel_group_repository.find_group_turn(
            harness.connection_id, GROUP, "1802"
        )
        is None
    )
    assert len(container.channel_groups._image_context) == (0 if case == "rapid" else 1)
    if case == "expired":
        clock = container.channel_groups._clock
        monkeypatch.setattr(
            container.channel_groups, "_clock", lambda: clock() + timedelta(seconds=61)
        )
    elif case == "fenced":
        # A real route-update fence synchronously removes cached references.
        container.channel_groups.fence_connection(
            harness.connection_id, "test", group_id=OTHER_GROUP
        )
        assert (
            container.channel_groups._image_context
        )  # Another group cannot delete this group's reference.
        container.channel_groups.fence_connection(harness.connection_id, "test", group_id=GROUP)
        assert not container.channel_groups._image_context
        return
    elif case == "policy_changed":
        await harness.request(
            "PUT",
            "/v1/sticker-library/settings" + _scope_query(route),
            {"learning_enabled": False, "expected_revision": 1},
            status=200,
        )
        await harness.request(
            "PUT",
            "/v1/sticker-library/settings" + _scope_query(route),
            {"learning_enabled": True, "expected_revision": 2},
            status=200,
        )
    mention = _group_event(1803, "看看刚才的图片", sender=BOB if case == "other_sender" else ALICE)
    if case == "rapid":
        await harness.send(mention, join_admission=False)
        assert not harness.model.requests
        release.set()
        await asyncio.wait_for(
            asyncio.gather(*tuple(container.qq_channels._group_ingress_tasks)), 5
        )
    else:
        await harness.send(mention)
    if case == "policy_changed":
        assert not calls and not harness.model.requests
        return
    request = await asyncio.wait_for(harness.model.started.get(), 5)
    assert len(request.images) == (1 if case in {"same_sender", "rapid"} else 0)
    assert len(calls) == (1 if case in {"same_sender", "rapid"} else 0)
    await harness.terminal(route, 1803)
    if case == "same_sender":
        await harness.send(mention)
        await harness.send(_group_event(1804, "另一个话题"))
        followup = await asyncio.wait_for(harness.model.started.get(), 5)
        assert (
            not followup.images and len(calls) == 1
        )  # Neither duplicate nor next turn re-downloads.


def _scope_query(route: ChannelGroupRouteSnapshot) -> str:
    return f"?group_route_id={route.route_id}&group_scene_id={route.scene_id}"


def _group_image(raw_id: int, image: bytes, *, group: str = GROUP) -> JsonObject:
    event = _group_event(raw_id, "记住这张表情", group=group)
    segments = cast(list[JsonValue], event["message"])
    segments.append(
        {
            "type": "image",
            "data": {
                "file": hashlib.md5(image, usedforsecurity=False).hexdigest() + ".png",
                "file_size": len(image),
            },
        }
    )
    return event


async def _no_favorites(_client: NapCatClient) -> frozenset[str]:
    return frozenset()


async def _enable_stickers(harness: _Runtime) -> None:
    container = harness.container
    current = await container.external_channels.get_connection(harness.connection_id)
    updated = await container.external_channels.update_connection(
        current.configuration.model_copy(
            update={
                "presentation_policy": ChannelPresentationPolicy(
                    profile=ChannelPresentationProfile.INSTANT_MESSAGE,
                    stickers_enabled=True,
                    cadence_enabled=False,
                )
            }
        ),
        expected_revision=current.revision,
        rotate_access_token=False,
    )
    assert isinstance(updated, ChannelConnectionSnapshot)
    await container.qq_channels.configuration_changed(updated)
    await asyncio.wait_for(harness.peer.connected.get(), 5)
    ready = await asyncio.wait_for(harness.base.health.get(), 5)
    assert ready[0] == harness.connection_id and ready[1].value == "ready"


@pytest.mark.asyncio
@pytest.mark.parametrize("accept", [False, True])
async def test_private_qq_learning_is_opt_in_and_keeps_photos_off(
    runtime_settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
    accept: bool,
) -> None:
    async with _runtime(runtime_settings, monkeypatch) as harness:
        container = harness.container
        connection_id = await _pair(harness)
        if accept:
            await container.sticker_repository.update_settings(
                "local", "default", learning_enabled=True, expected_revision=0
            )
        entered, release = asyncio.Event(), asyncio.Event()
        data = _picture()

        async def download(_client: NapCatClient, _ref: str, *, max_bytes: int) -> bytes:
            assert max_bytes == 5 * 1024 * 1024
            return data

        async def classify(image: LlmInputImage, *, generation_id: UUID) -> StickerClassification:
            assert image.data == data
            entered.set()
            await release.wait()
            return _classification()

        async def photos(*_args: object, **_kwargs: object) -> None:
            raise AssertionError("QQ sticker learning must never retain photographs")

        monkeypatch.setattr(NapCatClient, "download_image", download)
        monkeypatch.setattr(container.sticker_library._classifier, "classify", classify)
        monkeypatch.setattr(container.photo_observer, "observe_batch", photos)
        event = _image_event("记住表情", 1101)
        await harness.peer.peers[-1].send(json.dumps(event))
        request = await asyncio.wait_for(harness.model.received.get(), 5)
        assert len(request.images) == 1
        await asyncio.wait_for(harness.peer.sends.get(), 5)
        turn = await container.external_channel_repository.find_turn_by_external_message(
            connection_id, "1101"
        )
        assert turn is not None
        assert (
            await _terminal(harness, connection_id, turn.channel_turn_id)
        ).status is ChannelTurnStatus.COMPLETED
        tasks = [task for _, task in container.sticker_library._tasks.values()]
        if accept:
            await asyncio.wait_for(entered.wait(), 5)
            assert tasks
            assert not (await container.sticker_repository.snapshot("local", "default")).items
            release.set()
            await asyncio.wait_for(asyncio.gather(*tasks), 5)
        else:
            assert not entered.is_set() and not tasks
        saved = await container.sticker_repository.snapshot("local", "default")
        assert len(saved.items) == int(accept)
        assert not (await container.sticker_repository.snapshot("another-owner", "default")).items
        assert not harness.synthesis
        # A repeated provider event must not classify or send the image again.
        await harness.peer.peers[-1].send(json.dumps(event))
        receipt = await _ingest(harness, connection_id, "继续文字", 1102)
        await asyncio.wait_for(harness.peer.sends.get(), 5)
        await _terminal(harness, connection_id, receipt.channel_turn_id)
        assert len(harness.model.requests) == 2
        assert len((await container.sticker_repository.snapshot("local", "default")).items) == int(
            accept
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("native_confirmed", [False, True])
async def test_group_sticker_learns_in_its_scene_and_reuses_with_two_receipts(
    runtime: _Runtime,
    monkeypatch: pytest.MonkeyPatch,
    native_confirmed: bool,
) -> None:
    harness = runtime
    container = harness.container
    await _enable_stickers(harness)
    route = await harness.route()
    other = await harness.route(OTHER_GROUP)
    query = _scope_query(route)
    await harness.request(
        "PUT",
        "/v1/sticker-library/settings" + query,
        {
            "learning_enabled": True,
            "expected_revision": 0,
        },
    )
    assert (await harness.request("GET", "/v1/sticker-library" + _scope_query(other))).json()[
        "settings"
    ]["learning_enabled"] is False
    data = _picture()
    entered, release = asyncio.Event(), asyncio.Event()

    async def download(_client: NapCatClient, ref: str, *, max_bytes: int) -> bytes:
        assert ref == hashlib.md5(data, usedforsecurity=False).hexdigest() + ".png"
        return data

    async def classify(image: LlmInputImage, *, generation_id: UUID) -> StickerClassification:
        assert image.data == data
        entered.set()
        await release.wait()
        return _classification()

    monkeypatch.setattr(NapCatClient, "download_image", download)
    monkeypatch.setattr(NapCatClient, "favorite_hashes", _no_favorites)
    favorite_calls: list[bytes] = []

    async def native_favorite(
        _client: NapCatClient,
        _image: bytes,
        *,
        before_add: Callable[[], Awaitable[bool]],
        checkpoint: Callable[[], Awaitable[None]],
    ) -> bool:
        assert await before_add()
        await checkpoint()
        favorite_calls.append(_image)
        return native_confirmed

    monkeypatch.setattr(NapCatClient, "add_sticker_favorite", native_favorite)
    monkeypatch.setattr(container.sticker_library._classifier, "classify", classify)

    # Group-only opt-in never invokes the photo observer or private learned selection.
    def prohibited(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("Private preparation or photos reached group media")

    monkeypatch.setattr(container.photo_observer, "observe_batch", prohibited)
    await harness.send(_group_image(1201, data))
    request = await asyncio.wait_for(harness.model.started.get(), 5)
    assert request.user_text == "记住这张表情" and len(request.images) == 1
    assert not request.tools
    await asyncio.wait_for(harness.peer.group_sends.get(), 5)
    first = await harness.terminal(route, 1201)
    assert first.turn.status is ChannelTurnStatus.COMPLETED
    first_record = await container.external_channel_repository.get_turn(first.turn.channel_turn_id)
    assert first_record is not None and first_record.input_kind.value == "image"
    await asyncio.wait_for(entered.wait(), 5)
    tasks = [task for _, task in container.sticker_library._tasks.values()]
    release.set()
    await asyncio.wait_for(asyncio.gather(*tasks), 5)
    saved = (await harness.request("GET", "/v1/sticker-library" + query)).json()["items"]
    assert len(saved) == 1
    image_url = "/v1/sticker-library/" + saved[0]["sticker_id"] + "/image"
    image = await harness.request("GET", image_url + query)
    await harness.request("GET", image_url, status=404)
    await harness.request("GET", image_url + _scope_query(other), status=404)
    assert not (await container.sticker_repository.snapshot("local", "default")).items
    # Duplicate learning reuses the same scoped asset. An uncertain native add is
    # retained as uncertain and must not be replayed on the next admitted image.
    await harness.send(_group_image(1203, data))
    await asyncio.wait_for(harness.model.started.get(), 5)
    await asyncio.wait_for(harness.peer.group_sends.get(), 5)
    assert (await harness.terminal(route, 1203)).turn.status is ChannelTurnStatus.COMPLETED
    tasks = [task for _, task in container.sticker_library._tasks.values()]
    await asyncio.wait_for(asyncio.gather(*tasks), 5)
    assert len(favorite_calls) == 1
    assert len((await harness.request("GET", "/v1/sticker-library" + query)).json()["items"]) == 1

    # Use an explicit celebration with the normal response planner, not a forced image payload.
    await harness.send(_group_event(1202, "特别开心，一起庆祝一下吧！"))
    await asyncio.wait_for(harness.model.started.get(), 5)
    second = await harness.terminal(route, 1202)
    assert second.turn.status is ChannelTurnStatus.COMPLETED, second
    assert second.turn.delivery_status is not None, second
    text = await asyncio.wait_for(harness.peer.group_sends.get(), 5)
    assert _segments(text)[0]["type"] == "text"
    expression = await asyncio.wait_for(harness.peer.group_sends.get(), 5)
    segment = _segments(expression)[0]
    assert segment["type"] == "image"
    payload = cast(JsonObject, segment["data"])
    assert payload["sub_type"] == 1
    assert base64.b64decode(cast(str, payload["file"])[9:], validate=True) == image.content
    turn = await container.external_channel_repository.get_turn(second.turn.channel_turn_id)
    assert turn is not None and turn.delivery_id is not None
    plan = await container.external_channel_repository.get_delivery_plan(turn.delivery_id)
    assert plan is not None and len(plan.parts) == 2
    assert isinstance(plan.parts[-1].payload, ChannelImageDeliveryPartPayload)
    assert plan.parts[-1].payload.sticker_id == saved[0]["sticker_id"]
    assert not plan.parts[-1].required
    assert all(
        part.status is ChannelDeliveryPartStatus.DELIVERED and part.provider_message_id
        for part in plan.parts
    )
    usage = (await harness.request("GET", "/v1/sticker-library/usage" + query)).json()["items"]
    assert len(usage) == 1 and usage[0]["status"] == "delivered"
    cursor = await container.external_channel_repository.get_adapter_cursor(harness.connection_id)
    assert cursor is not None
    assert json.loads(cursor)["qq-favorite:" + saved[0]["sha256"]] == (
        "confirmed" if native_confirmed else "unknown"
    )
    deleted = (
        await harness.request("DELETE", "/v1/sticker-library/" + saved[0]["sticker_id"] + query)
    ).json()
    assert deleted["deleted"]
    await harness.request("GET", image_url + query, status=404)
    assert not (await harness.request("GET", "/v1/sticker-library/usage" + query)).json()["items"]

    # Changing a scene can never turn an old management request into a new scene write.
    await harness.request(
        "GET",
        "/v1/sticker-library?group_route_id=" + str(route.route_id) + "&group_scene_id=old-scene",
        status=409,
    )
    await harness.request(
        "PUT",
        "/v1/sticker-library/settings?group_route_id=" + str(route.route_id),
        {"learning_enabled": False, "expected_revision": 1},
        status=400,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("case", ["off", "no_mention", "ungranted", "opaque", "wrong_bytes"])
async def test_group_images_fail_closed_without_authority_or_matching_source(
    runtime: _Runtime,
    monkeypatch: pytest.MonkeyPatch,
    case: str,
) -> None:
    harness = runtime
    await _enable_stickers(harness)
    route = await harness.route()
    if case != "off":
        await harness.request(
            "PUT",
            "/v1/sticker-library/settings" + _scope_query(route),
            {
                "learning_enabled": True,
                "expected_revision": 0,
            },
        )
    data = _picture()
    event = _group_image(1301, data)
    segments = cast(list[JsonObject], event["message"])
    if case == "no_mention":
        event["message"] = cast(list[JsonValue], segments[1:])
    if case == "ungranted":
        event["user_id"] = 333
        event["sender"] = {"user_id": 333}
    if case == "opaque":
        cast(JsonObject, segments[-1]["data"])["file"] = "some-account-global-name.png"
    downloads: list[str] = []

    async def download(_client: NapCatClient, ref: str, *, max_bytes: int) -> bytes:
        downloads.append(ref)
        return _picture("JPEG") if case == "wrong_bytes" else data

    async def classify(_image: LlmInputImage, *, generation_id: UUID) -> StickerClassification:
        raise AssertionError("Rejected sources must not classify")

    monkeypatch.setattr(NapCatClient, "download_image", download)
    monkeypatch.setattr(harness.container.sticker_library._classifier, "classify", classify)
    await harness.send(event)
    record = await harness.container.channel_group_repository.find_group_turn(
        harness.connection_id, GROUP, "1301"
    )
    if case in {"opaque", "wrong_bytes"}:
        assert record is not None
        result = await harness.terminal(route, 1301)
        # Group delivery continues to require a completed generation. A failed
        # image read is a failed turn, never relabeled as model/learning success.
        assert result.turn.status is ChannelTurnStatus.FAILED
        generation = await harness.container.conversation_repository.generation_result(
            record.turn.generation_id
        )
        assert generation is not None and generation.error_code == "image_input_error"
        assert harness.peer.group_sends.empty()
    else:
        assert record is None and harness.peer.group_sends.empty()
    assert not harness.model.requests
    assert len(downloads) == int(case == "wrong_bytes")
    assert not harness.container.sticker_library._tasks
    assert not (
        await harness.container.sticker_repository.snapshot(f"scene:{route.scene_id}", "default")
    ).items


@pytest.mark.asyncio
@pytest.mark.parametrize("fence", ["disabled", "deleted", "membership"])
async def test_completed_group_source_cannot_learn_after_settings_or_member_revocation(
    runtime: _Runtime,
    monkeypatch: pytest.MonkeyPatch,
    fence: str,
) -> None:
    harness = runtime
    await _enable_stickers(harness)
    route = await harness.route()
    query = _scope_query(route)
    await harness.request(
        "PUT",
        "/v1/sticker-library/settings" + query,
        {
            "learning_enabled": True,
            "expected_revision": 0,
        },
    )
    entered, release = asyncio.Event(), asyncio.Event()

    async def download(_client: NapCatClient, _ref: str, *, max_bytes: int) -> bytes:
        return _picture()

    async def classify(_image: LlmInputImage, *, generation_id: UUID) -> StickerClassification:
        entered.set()
        await release.wait()
        return _classification()

    monkeypatch.setattr(NapCatClient, "download_image", download)
    monkeypatch.setattr(harness.container.sticker_library._classifier, "classify", classify)
    await harness.send(_group_image(1401, _picture()))
    await asyncio.wait_for(entered.wait(), 5)
    await asyncio.wait_for(harness.peer.group_sends.get(), 5)
    result = await harness.terminal(route, 1401)
    assert result.turn.status is ChannelTurnStatus.COMPLETED
    tasks = [task for _, task in harness.container.sticker_library._tasks.values()]
    assert tasks
    if fence == "disabled":
        await harness.request(
            "PUT",
            "/v1/sticker-library/settings" + query,
            {
                "learning_enabled": False,
                "expected_revision": 1,
            },
        )
    elif fence == "deleted":
        await harness.request("DELETE", "/v1/sticker-library/learned_" + "0" * 32 + query)
    else:
        await harness.peer.peers[-1].send(json.dumps(_notice()))
        await harness.pause(ChannelGroupPauseReason.MEMBERSHIP_CHANGED)
    release.set()
    await asyncio.wait_for(asyncio.gather(*tasks, return_exceptions=True), 5)
    assert not (await harness.request("GET", "/v1/sticker-library" + query)).json()["items"]
    assert not (await harness.container.sticker_repository.snapshot("local", "default")).items
    assert not any(call["action"] == "add_custom_face" for call in harness.peer.calls)
    assert harness.peer.group_sends.empty()
