"""Real SQLite/Conversation/photo observer restart, recall and deletion boundaries."""
# pyright: reportPrivateUsage=false

from __future__ import annotations

import asyncio
import hashlib
import io
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import cast
from uuid import UUID, uuid4

import pytest
from chatwaifu_protocol.photo_memory import SavedPhoto
from chatwaifu_protocol.session import GenerationState
from chatwaifu_runtime.bootstrap.container import RuntimeContainer
from chatwaifu_runtime.config.settings import Settings
from chatwaifu_runtime.conversation.models import ConversationTurnOptions
from chatwaifu_runtime.external_channels.models import ChannelInboundImageInput
from chatwaifu_runtime.photo_memory.classifier import PhotoClassification
from chatwaifu_runtime.photo_memory.models import PhotoSaveCandidate
from chatwaifu_runtime.providers.contracts import (
    LlmInputImage,
    LlmRequest,
    LlmStreamEvent,
    LlmTextDelta,
)
from fastapi.testclient import TestClient
from PIL import Image
from test_inbound_image_lifecycle import connect, message

_VISIBLE = "远处的红色灯塔，前景有两只蓝色小船"


class PhotoRecorder:
    kind = "photo_recorder"
    supports_tool_calling = False

    def __init__(self) -> None:
        self.requests: list[LlmRequest] = []
        self.entered = asyncio.Event()
        self.release = asyncio.Event()
        self.block = False

    async def stream(self, request: LlmRequest) -> AsyncIterator[LlmStreamEvent]:
        self.requests.append(request)
        self.entered.set()
        if self.block:
            await self.release.wait()
        yield LlmTextDelta(_VISIBLE)


@dataclass(frozen=True)
class Retained:
    photo: SavedPhoto
    session_id: UUID
    connection_id: UUID
    token: str


async def _retain(container: RuntimeContainer, monkeypatch: pytest.MonkeyPatch) -> Retained:
    observed = asyncio.Event()
    original_save = container.photo_repository.save

    async def save(
        scope: str, character: str, candidate: PhotoSaveCandidate, *, expected_revision: int
    ) -> SavedPhoto | None:
        result = await original_save(
            scope, character, candidate, expected_revision=expected_revision
        )
        if result is not None:
            observed.set()
        return result

    async def classify(image: LlmInputImage, *, generation_id: UUID) -> PhotoClassification:
        assert image.data and generation_id
        return PhotoClassification(
            suitable=True,
            confidence=0.96,
            title="海边灯塔",
            description=_VISIBLE,
            keywords=["灯塔", "海边", "蓝色小船"],
        )

    monkeypatch.setattr(container.photo_repository, "save", save)
    monkeypatch.setattr(container.photo_observer._classifier, "classify", classify)
    await container.photo_repository.update_settings(
        "local", "default", retention_enabled=True, expected_revision=0
    )
    connection_id, token = await connect(container)
    buffer = io.BytesIO()
    Image.new("RGB", (60, 40), "navy").save(buffer, "JPEG")
    data = buffer.getvalue()

    async def load() -> LlmInputImage:
        return LlmInputImage(data=data, mime_type="image/jpeg")

    receipt = await container.external_channels.ingest(
        message(connection_id, "photo-memory-source", "今天发给你看看"),
        access_token=token,
        image_input=ChannelInboundImageInput(hashlib.sha256(data).hexdigest(), load),
    )
    await asyncio.wait_for(observed.wait(), 10)
    snapshot = await container.photo_repository.snapshot("local", "default")
    assert len(snapshot.items) == 1
    assert snapshot.items[0].source_generation_id == receipt.generation_id
    return Retained(snapshot.items[0], receipt.session_id, connection_id, token)


async def _submit(container: RuntimeContainer, session_id: UUID, text: str) -> UUID:
    accepted = await container.conversation.submit_text(
        session_id, text, options=ConversationTurnOptions(output_modes=frozenset({"text"}))
    )
    async with asyncio.timeout(10):
        while True:
            result = await container.conversation_repository.generation_result(
                accepted.generation_id
            )
            if result is not None and result.state is GenerationState.COMPLETED:
                return accepted.generation_id
            if result is not None and result.state in {
                GenerationState.FAILED,
                GenerationState.CANCELLED,
            }:
                pytest.fail(f"generation terminated: {result}")
            await asyncio.sleep(0.01)  # bounded polling of the durable terminal fact


@pytest.mark.asyncio
async def test_photo_restart_cross_surface_recall_and_transitive_deletion(
    runtime_settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    initial = RuntimeContainer(runtime_settings)
    await initial.start()
    monkeypatch.setattr(initial.agent, "_llm", PhotoRecorder())
    try:
        retained = await _retain(initial, monkeypatch)
        assert retained.photo.caption == "今天发给你看看"
        assert retained.photo.description == _VISIBLE
        memory_rows = await initial.database.fetchall("SELECT text FROM memory_records")
        assert all(_VISIBLE not in str(row[0]) for row in memory_rows)
    finally:
        await initial.stop()

    restarted = RuntimeContainer(runtime_settings)
    await restarted.start()
    recorder = PhotoRecorder()
    monkeypatch.setattr(restarted.agent, "_llm", recorder)
    try:
        await restarted.photo_repository.update_settings(
            "local", "default", retention_enabled=False, expected_revision=1
        )
        desktop = await restarted.sessions.create_session("default")
        recalled = await _submit(restarted, desktop.session_id, "之前那张海边照片里有什么？")
        request = recorder.requests[-1]
        assert request.images and len(request.images) == 1
        assert _VISIBLE in str(request.context)
        assert "user shared through WeChat" in str(request.context)
        inherited = await _submit(restarted, desktop.session_id, "嗯，再说一句")
        old_history = await restarted.conversation_repository.recent_history(
            desktop.session_id, uuid4(), limit=20
        )
        deletion = await restarted.photo_repository.delete(
            "local", "default", retained.photo.photo_id
        )
        assert {retained.photo.source_generation_id, recalled, inherited} <= {
            ref.generation_id for ref in deletion.affected_generations
        }
        assert (
            await restarted.photo_repository.get_image("local", "default", retained.photo.photo_id)
            is None
        )
        assert not await restarted.photo_repository.search("local", "default", "灯塔")
        assert not await restarted.database.fetchall("SELECT * FROM photo_assets_fts")
        # Fence a history snapshot acquired before deletion, as well as new reads.
        filtered = await restarted.conversation_repository.prepare_history(uuid4(), old_history)
        assert all(_VISIBLE not in entry.text for entry in filtered)
        new_history = await restarted.conversation_repository.recent_history(
            desktop.session_id, uuid4(), limit=20
        )
        assert all(_VISIBLE not in entry.text for entry in new_history)
        # The owner's visible chat transcript is retained.
        assert any(
            _VISIBLE in str(row)
            for row in await restarted.conversation_repository.list_messages(
                desktop.session_id, limit=20
            )
        )
    finally:
        await restarted.stop()

    after_delete = RuntimeContainer(runtime_settings)
    await after_delete.start()
    after_recorder = PhotoRecorder()
    monkeypatch.setattr(after_delete.agent, "_llm", after_recorder)
    try:
        await _submit(after_delete, desktop.session_id, "之前那张海边照片里有什么？")
        request = after_recorder.requests[-1]
        assert not request.images
        assert _VISIBLE not in str(request.history) + str(request.context)
        assert "No saved photo matches" in str(request.context)
    finally:
        await after_delete.stop()


def test_photo_api_auth_preview_delete_cancels_exact_active_recall(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    route = "/v1/photo-memory"
    assert client.get(route, headers={"Authorization": "Bearer wrong"}).status_code == 401
    assert client.get(route).json()["settings"]["retention_enabled"] is False
    assert client.get(route + "?character_id=foreign").status_code == 400
    assert client.delete(route + "/not-a-uuid").status_code == 422
    container = cast(RuntimeContainer, client.app.state.container)  # type: ignore[union-attr]
    recorder = PhotoRecorder()
    monkeypatch.setattr(container.agent, "_llm", recorder)
    assert client.portal is not None
    retained = client.portal.call(_retain, container, monkeypatch)
    photo_route = route + "/" + str(retained.photo.photo_id)
    response = client.get(photo_route + "/image")
    assert response.status_code == 200 and response.content
    assert response.headers["content-type"] == retained.photo.mime_type
    assert response.headers["cache-control"] == "no-store"
    assert (
        client.put(
            route + "/settings", json={"retention_enabled": False, "expected_revision": 0}
        ).status_code
        == 409
    )

    async def begin_recall() -> UUID:
        recorder.entered.clear()
        recorder.block = True
        accepted = await container.conversation.submit_text(
            retained.session_id,
            "刚才那张照片是什么？",
            options=ConversationTurnOptions(output_modes=frozenset({"text"})),
        )
        await asyncio.wait_for(recorder.entered.wait(), 10)
        return accepted.generation_id

    generation_id = client.portal.call(begin_recall)
    assert recorder.requests[-1].images

    async def unrelated_cancellation() -> bool:
        return await container.conversation.cancel(
            retained.session_id, "photo_deleted", expected_generation_id=uuid4()
        )

    assert client.portal.call(unrelated_cancellation) is False
    assert (
        client.portal.call(container.conversation.active_generation_id, retained.session_id)
        == generation_id
    )
    deletion = client.delete(photo_route)
    assert deletion.status_code == 200 and deletion.json()["deleted"] is True
    result = client.portal.call(container.conversation_repository.generation_result, generation_id)
    assert result is not None and result.state is GenerationState.CANCELLED
    assert client.get(photo_route + "/image").status_code == 404
    assert not client.get(route).json()["items"]


@pytest.mark.asyncio
async def test_experience_reset_removes_photos_and_fences_pending_save(
    runtime_settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    container = RuntimeContainer(runtime_settings)
    await container.start()
    monkeypatch.setattr(container.agent, "_llm", PhotoRecorder())
    try:
        retained = await _retain(container, monkeypatch)
        desktop = await container.sessions.create_session("default")
        await _submit(container, desktop.session_id, "记得那张灯塔照片吗？")
        await container.conversation.reset(desktop.session_id)
        assert not (await container.photo_repository.snapshot("local", "default")).items
        assert not await container.database.fetchall("SELECT * FROM photo_assets_fts")
        assert (await container.photo_repository.get_settings("local", "default")).revision > 1
        history = await container.conversation_repository.recent_history(
            retained.session_id, uuid4(), limit=20
        )
        assert all(_VISIBLE not in item.text for item in history)
    finally:
        await container.stop()
