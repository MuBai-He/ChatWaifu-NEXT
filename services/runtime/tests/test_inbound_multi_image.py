# pyright: reportPrivateUsage=false
"""Comprehensive integration tests for bounded inbound multi-image handling (Phase 17.3F).

Verifies:
1. Reordered / changed fingerprint and duplicate receipt & conflict detection.
2. Provider payload contains all images in sequential order + vision prompt explanation.
3. Partial download failure aborts all-or-nothing (0 provider, 0 observer, durable recovery notice).
4. Cancellation during batch download produces no late delivery or recovery notice.
5. Photo observer saves multiple qualifying photos for the same generation (no key collision drops).
6. One photo classification failure in batch does not abort subsequent photos.
7. Sticker learning persists all qualifying stickers in the batch.
8. Observer batch cancellation on cancel_generation and stop fence.
9. Ambiguous multi-photo annotation attribution ("这张照片") returns null.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import io
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import cast
from uuid import UUID, uuid4

import httpx
import pytest
from chatwaifu_protocol.channels import ChannelTurnStatus
from chatwaifu_protocol.photo_memory import SavedPhoto
from chatwaifu_runtime.bootstrap.container import RuntimeContainer
from chatwaifu_runtime.config.settings import Settings, StorageConfig
from chatwaifu_runtime.external_channels.adapters.weixin_ilink.client import WeixinILinkClient
from chatwaifu_runtime.external_channels.adapters.weixin_ilink.models import WeixinInboundImage
from chatwaifu_runtime.external_channels.management import (
    _compute_image_fingerprint,
    _compute_images_fingerprint,
    _make_batch_image_loader,
)
from chatwaifu_runtime.external_channels.models import ChannelInboundImageInput
from chatwaifu_runtime.external_channels.service import ChannelConflictError
from chatwaifu_runtime.persistence.database import Database
from chatwaifu_runtime.persistence.sqlite_photo_memory import SQLitePhotoMemoryRepository
from chatwaifu_runtime.photo_memory.annotations import PhotoAnnotationService
from chatwaifu_runtime.photo_memory.classifier import PhotoClassification
from chatwaifu_runtime.photo_memory.metadata import strip_image_exif
from chatwaifu_runtime.photo_memory.models import PhotoSaveCandidate
from chatwaifu_runtime.photo_memory.observer import PhotoObservationSource
from chatwaifu_runtime.providers.contracts import LlmInputImage
from chatwaifu_runtime.providers.model_config import ModelConfigurationService
from chatwaifu_runtime.providers.openai_compatible import build_messages
from chatwaifu_runtime.sticker_library.classifier import StickerClassification
from chatwaifu_runtime.sticker_library.models import StickerSaveCandidate
from PIL import ExifTags, Image
from test_inbound_image_lifecycle import VisionRecorder, connect, message
from test_photo_memory_repository import _seed_source_chain


def _make_test_image(
    color: str = "red",
    format: str = "PNG",
    width: int = 32,
    height: int = 32,
    exif_date: str | None = None,
) -> bytes:
    img = Image.new("RGB", (width, height), color=color)
    buf = io.BytesIO()
    if exif_date and format.upper() == "JPEG":
        exif = img.getexif()
        exif[ExifTags.Base.Orientation] = 1
        exif_ifd = exif.get_ifd(ExifTags.IFD.Exif)
        exif_ifd[ExifTags.Base.DateTimeOriginal] = exif_date
        exif_ifd[ExifTags.Base.OffsetTimeOriginal] = "+08:00"
        img.save(buf, format=format, exif=exif)
    else:
        img.save(buf, format=format)
    return buf.getvalue()


@pytest.mark.asyncio
async def test_multi_image_fingerprint_computation_and_duplicate_or_conflict(
    runtime_settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Batch fingerprint is deterministic and ordered; reordering or changing detects conflict."""
    img1_bytes = _make_test_image(color="red")
    img2_bytes = _make_test_image(color="blue")

    w_img1 = WeixinInboundImage(encrypt_query_param="enc1", full_url="url1", aes_key="aes1")
    w_img2 = WeixinInboundImage(encrypt_query_param="enc2", full_url="url2", aes_key="aes2")

    # 1. Single-image fingerprint equality with legacy
    assert _compute_images_fingerprint((w_img1,)) == _compute_image_fingerprint(w_img1)

    # 2. Multi-image fingerprint is deterministic SHA-256 of JSON payload
    batch_fp = _compute_images_fingerprint((w_img1, w_img2))
    assert isinstance(batch_fp, str) and len(batch_fp) == 64

    # 3. Reordering produces a different fingerprint
    reordered_fp = _compute_images_fingerprint((w_img2, w_img1))
    assert reordered_fp != batch_fp

    # 4. Runtime container ingest duplicate and conflict behavior
    container = RuntimeContainer(runtime_settings)
    await container.start()
    recorder = VisionRecorder()
    monkeypatch.setattr(container.agent, "_llm", recorder)

    conn_id, token = await connect(container)
    msg = message(conn_id, "multi-img-msg")

    async def load_batch() -> tuple[LlmInputImage, ...]:
        return (
            LlmInputImage(data=img1_bytes, mime_type="image/png"),
            LlmInputImage(data=img2_bytes, mime_type="image/png"),
        )

    image_input = ChannelInboundImageInput(batch_fp, load_batch)

    try:
        receipt = await container.external_channels.ingest(
            msg, access_token=token, image_input=image_input
        )
        assert receipt.duplicate is False

        # Duplicate ingest with identical fingerprint returns duplicate receipt
        dup_receipt = await container.external_channels.ingest(
            msg, access_token=token, image_input=image_input
        )
        assert dup_receipt.duplicate is True
        assert dup_receipt.channel_turn_id == receipt.channel_turn_id

        # Ingest with same external_message_id but different fingerprint raises ChannelConflictError
        conflict_input = ChannelInboundImageInput(reordered_fp, load_batch)
        with pytest.raises(ChannelConflictError):
            await container.external_channels.ingest(
                msg, access_token=token, image_input=conflict_input
            )

        result = await container.external_channels.wait_for_turn(
            conn_id, receipt.channel_turn_id, wait_seconds=5
        )
        assert result.status is ChannelTurnStatus.COMPLETED
    finally:
        await container.stop()


@pytest.mark.asyncio
async def test_multi_image_provider_dispatch_order_and_vision_prompt(
    runtime_settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Provider receives all images in exact order, with EXIF stripped and vision prompt."""
    # Create 3 distinct images with EXIF
    img1 = _make_test_image("red", format="JPEG", exif_date="2026:01:01 10:00:00")
    img2 = _make_test_image("green", format="JPEG", exif_date="2026:01:01 11:00:00")
    img3 = _make_test_image("blue", format="JPEG", exif_date="2026:01:01 12:00:00")

    container = RuntimeContainer(runtime_settings)
    await container.start()
    recorder = VisionRecorder()
    monkeypatch.setattr(container.agent, "_llm", recorder)
    conn_id, token = await connect(container)
    msg = message(conn_id, "batch-3-msg", text="按顺序看这三张图")

    async def load_3() -> tuple[LlmInputImage, ...]:
        return (
            LlmInputImage(data=img1, mime_type="image/jpeg"),
            LlmInputImage(data=img2, mime_type="image/jpeg"),
            LlmInputImage(data=img3, mime_type="image/jpeg"),
        )

    fp = hashlib.sha256(b"batch-3-fp").hexdigest()
    image_input = ChannelInboundImageInput(fp, load_3)

    try:
        receipt = await container.external_channels.ingest(
            msg, access_token=token, image_input=image_input
        )
        result = await container.external_channels.wait_for_turn(
            conn_id, receipt.channel_turn_id, wait_seconds=5
        )
        assert result.status is ChannelTurnStatus.COMPLETED

        assert len(recorder.requests) == 1
        req = recorder.requests[0]
        assert len(req.images) == 3

        # Sequential order is preserved: compare stripped bytes and color pixels
        for idx, (expected_raw, expected_color_name) in enumerate(
            [
                (img1, "red"),
                (img2, "green"),
                (img3, "blue"),
            ]
        ):
            delivered = req.images[idx]
            assert delivered.mime_type == "image/jpeg"
            expected_stripped = strip_image_exif(
                LlmInputImage(data=expected_raw, mime_type="image/jpeg")
            ).data
            assert delivered.data == expected_stripped

            # Delivered image EXIF must be stripped
            pil_delivered = Image.open(io.BytesIO(delivered.data))
            assert not pil_delivered.getexif()

            # Verify color pixels match real order
            px = pil_delivered.getpixel((0, 0))
            assert isinstance(px, tuple)
            if expected_color_name == "red":
                assert px[0] > 200 and px[1] < 50 and px[2] < 50
            elif expected_color_name == "green":
                assert px[0] < 50 and px[1] > 100 and px[2] < 50
            elif expected_color_name == "blue":
                assert px[0] < 50 and px[1] < 50 and px[2] > 200

        # Multi-image vision prompt is included
        assert "[Vision Instruction]" in req.system_prompt
        assert "3 images are attached to the current user turn" in req.system_prompt
        assert "Respond to the actual visual content" in req.system_prompt

        # Actual OpenAI payload assertion for all images in order
        messages = build_messages(req)
        user_msg = messages[-1]
        assert user_msg["role"] == "user"
        content = cast(list[dict[str, object]], user_msg["content"])
        assert isinstance(content, list)
        assert len(content) == 4
        assert content[0] == {"type": "text", "text": "按顺序看这三张图"}
        for idx, (expected_raw, _) in enumerate(
            [
                (img1, "red"),
                (img2, "green"),
                (img3, "blue"),
            ]
        ):
            part = content[idx + 1]
            assert part["type"] == "image_url"
            expected_data = strip_image_exif(
                LlmInputImage(data=expected_raw, mime_type="image/jpeg")
            ).data
            expected_b64 = base64.b64encode(expected_data).decode("ascii")
            assert part["image_url"] == {"url": f"data:image/jpeg;base64,{expected_b64}"}
    finally:
        await container.stop()


@pytest.mark.asyncio
async def test_multi_image_partial_download_failure_aborts_all_or_nothing(
    runtime_settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Failure on 2nd image in real WeixinILinkClient aborts fail-closed
    (0 provider/observers, no img3 fetch, restart no redownload).
    """
    container = RuntimeContainer(runtime_settings)
    await container.start()
    recorder = VisionRecorder()
    monkeypatch.setattr(container.agent, "_llm", recorder)
    conn_id, token = await connect(container)
    msg = message(conn_id, "fail-batch-msg")

    valid_png = _make_test_image(color="red", format="PNG")
    w1 = WeixinInboundImage(encrypt_query_param="enc1")
    w2 = WeixinInboundImage(encrypt_query_param="enc2")
    w3 = WeixinInboundImage(encrypt_query_param="enc3")
    images = (w1, w2, w3)

    fetched_urls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        url_str = str(request.url)
        fetched_urls.append(url_str)
        if "enc1" in url_str:
            return httpx.Response(200, content=valid_png)
        elif "enc2" in url_str:
            # Corrupted payload on image 2!
            return httpx.Response(200, content=b"corrupted_garbage_bytes")
        elif "enc3" in url_str:
            return httpx.Response(200, content=valid_png)
        return httpx.Response(404)

    mock_transport = httpx.MockTransport(handler)
    http_client = httpx.AsyncClient(transport=mock_transport)
    real_client = WeixinILinkClient(http_client)

    loader = _make_batch_image_loader(
        real_client,
        images,
        connection_id=conn_id,
        external_message_id="fail-batch-msg",
    )
    batch_fp = _compute_images_fingerprint(images)
    image_input = ChannelInboundImageInput(batch_fp, loader)

    try:
        receipt = await container.external_channels.ingest(
            msg, access_token=token, image_input=image_input
        )
        result = await container.external_channels.wait_for_turn(
            conn_id, receipt.channel_turn_id, wait_seconds=5
        )
        assert result.status is ChannelTurnStatus.FAILED
        assert result.delivery_id is not None

        # Image 1 was fetched, image 2 was fetched, but Image 3 was NEVER fetched!
        assert any("enc1" in u for u in fetched_urls)
        assert any("enc2" in u for u in fetched_urls)
        assert not any("enc3" in u for u in fetched_urls)

        # Zero provider calls
        assert len(recorder.requests) == 0

        # Zero observer items saved
        photos_snap = await container.photo_repository.snapshot("local", "default")
        assert len(photos_snap.items) == 0
        stickers_snap = await container.sticker_repository.snapshot("local", "default")
        assert len(stickers_snap.items) == 0

        # Durable friendly failure recovery notice
        plan = await container.external_channel_repository.get_delivery_plan(result.delivery_id)
        assert plan is not None and len(plan.parts) == 1
        notice = "刚才发来的图片我没看清，能再发一次吗？"
        assert plan.parts[0].payload.model_dump()["text"] == notice

        # History records the assistant failure notice
        history = await container.conversation_repository.recent_history(
            result.session_id, uuid4(), limit=20
        )
        assert sum(entry.role == "assistant" and entry.text == notice for entry in history) == 1

        # Replay duplicate does not redownload
        fetches_before = len(fetched_urls)
        dup = await container.external_channels.ingest(
            msg, access_token=token, image_input=image_input
        )
        assert dup.duplicate is True
        assert len(fetched_urls) == fetches_before
    finally:
        await container.stop()

    # Runtime restart: duplicate still causes no redownload
    restarted = RuntimeContainer(runtime_settings)
    await restarted.start()
    try:
        dup2 = await restarted.external_channels.ingest(
            msg, access_token=token, image_input=image_input
        )
        assert dup2.duplicate is True
        assert len(fetched_urls) == fetches_before
    finally:
        await restarted.stop()
        await http_client.aclose()


@pytest.mark.asyncio
async def test_multi_image_cancellation_during_batch_download(
    runtime_settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Cancellation during 2nd image download aborts whole loader cleanly
    without fetching 3rd image.
    """
    container = RuntimeContainer(runtime_settings)
    await container.start()
    recorder = VisionRecorder()
    monkeypatch.setattr(container.agent, "_llm", recorder)

    valid_png = _make_test_image(color="red", format="PNG")
    w1 = WeixinInboundImage(encrypt_query_param="enc1")
    w2 = WeixinInboundImage(encrypt_query_param="enc2")
    w3 = WeixinInboundImage(encrypt_query_param="enc3")
    images = (w1, w2, w3)

    fetched_urls: list[str] = []
    img2_entered = asyncio.Event()

    async def hanging_handler(request: httpx.Request) -> httpx.Response:
        url_str = str(request.url)
        fetched_urls.append(url_str)
        if "enc1" in url_str:
            return httpx.Response(200, content=valid_png)
        elif "enc2" in url_str:
            img2_entered.set()
            await asyncio.Event().wait()  # Hangs until cancellation
            return httpx.Response(200, content=valid_png)
        elif "enc3" in url_str:
            return httpx.Response(200, content=valid_png)
        return httpx.Response(404)

    mock_transport = httpx.MockTransport(hanging_handler)
    http_client = httpx.AsyncClient(transport=mock_transport)
    real_client = WeixinILinkClient(http_client)

    conn_id, token = await connect(container)
    loader = _make_batch_image_loader(
        real_client,
        images,
        connection_id=conn_id,
        external_message_id="slow-batch",
    )
    batch_fp = _compute_images_fingerprint(images)
    image_input = ChannelInboundImageInput(batch_fp, loader)

    try:
        receipt = await container.external_channels.ingest(
            message(conn_id, "slow-batch"),
            access_token=token,
            image_input=image_input,
        )
        await asyncio.wait_for(img2_entered.wait(), 5)

        # Image 1 was fetched, image 2 started
        assert any("enc1" in u for u in fetched_urls)
        assert any("enc2" in u for u in fetched_urls)

        following = await container.external_channels.ingest(
            message(conn_id, "superseding-text", "不用看了"),
            access_token=token,
            supersede_inflight=True,
        )

        old = await container.external_channels.wait_for_turn(
            conn_id, receipt.channel_turn_id, wait_seconds=5
        )
        new = await container.external_channels.wait_for_turn(
            conn_id, following.channel_turn_id, wait_seconds=5
        )

        assert old.status is ChannelTurnStatus.CANCELLED
        assert old.delivery_id is None  # No failure recovery text on cancellation!
        assert new.status is ChannelTurnStatus.COMPLETED
        # Image 3 was NEVER fetched!
        assert not any("enc3" in u for u in fetched_urls)
        # Observers never called for cancelled generation
        photos_snap = await container.photo_repository.snapshot("local", "default")
        assert len(photos_snap.items) == 0
        assert len(recorder.requests) == 1
        assert not recorder.requests[0].images
    finally:
        await container.stop()
        await http_client.aclose()


@pytest.mark.asyncio
async def test_multi_image_photo_observer_saves_all_photos_same_generation(
    runtime_settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Photo observer processes all photos sequentially in one batch task without key drops."""
    container = RuntimeContainer(runtime_settings)
    await container.start()
    recorder = VisionRecorder()
    monkeypatch.setattr(container.agent, "_llm", recorder)

    saved_photos: list[PhotoSaveCandidate] = []
    original_save = container.photo_repository.save
    all_saved = asyncio.Event()

    async def save(
        scope: str, character: str, candidate: PhotoSaveCandidate, *, expected_revision: int
    ) -> SavedPhoto | None:
        result = await original_save(
            scope, character, candidate, expected_revision=expected_revision
        )
        if result is not None:
            saved_photos.append(candidate)
            if len(saved_photos) == 2:
                all_saved.set()
        return result

    classified_count = 0

    async def classify(image: LlmInputImage, *, generation_id: UUID) -> PhotoClassification:
        nonlocal classified_count
        classified_count += 1
        return PhotoClassification(
            suitable=True,
            confidence=0.95,
            title=f"测试照片 {classified_count}",
            description=f"第 {classified_count} 张照片的内容",
            keywords=[f"照片{classified_count}"],
        )

    monkeypatch.setattr(container.photo_repository, "save", save)
    monkeypatch.setattr(container.photo_observer._classifier, "classify", classify)

    await container.photo_repository.update_settings(
        "local", "default", retention_enabled=True, expected_revision=0
    )

    conn_id, token = await connect(container)
    img1 = _make_test_image("red")
    img2 = _make_test_image("blue")

    async def load_2() -> tuple[LlmInputImage, ...]:
        return (
            LlmInputImage(data=img1, mime_type="image/png"),
            LlmInputImage(data=img2, mime_type="image/png"),
        )

    msg = message(conn_id, "multi-photo-save", "给你发两张照片")
    image_input = ChannelInboundImageInput(hashlib.sha256(b"photos-2").hexdigest(), load_2)

    try:
        receipt = await container.external_channels.ingest(
            msg, access_token=token, image_input=image_input
        )
        result = await container.external_channels.wait_for_turn(
            conn_id, receipt.channel_turn_id, wait_seconds=5
        )
        assert result.status is ChannelTurnStatus.COMPLETED

        await asyncio.wait_for(all_saved.wait(), 10)
        assert len(saved_photos) == 2

        # Both photos share the exact same generation_id
        assert saved_photos[0].generation_id == receipt.generation_id
        assert saved_photos[1].generation_id == receipt.generation_id

        # In SQLite photo_references, both photo_ids are mapped under the same generation_id
        rows = await container.database.fetchall(
            "SELECT photo_id, generation_id FROM photo_references WHERE generation_id=?",
            (str(receipt.generation_id),),
        )
        assert len(rows) == 2
        ref_photo_ids = {row[0] for row in rows}
        snapshot = await container.photo_repository.snapshot("local", "default")
        assert len(snapshot.items) == 2
        assert {str(p.photo_id) for p in snapshot.items} == ref_photo_ids
    finally:
        await container.stop()


@pytest.mark.asyncio
async def test_multi_image_photo_observer_classification_error_isolation(
    runtime_settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    """One photo classification failure in batch does not prevent later valid photos from saving."""
    container = RuntimeContainer(runtime_settings)
    await container.start()
    recorder = VisionRecorder()
    monkeypatch.setattr(container.agent, "_llm", recorder)

    saved_photos: list[PhotoSaveCandidate] = []
    original_save = container.photo_repository.save
    photo2_saved = asyncio.Event()

    async def save(
        scope: str, character: str, candidate: PhotoSaveCandidate, *, expected_revision: int
    ) -> SavedPhoto | None:
        result = await original_save(
            scope, character, candidate, expected_revision=expected_revision
        )
        if result is not None:
            saved_photos.append(candidate)
            photo2_saved.set()
        return result

    call_index = 0

    async def classify_with_failure(
        image: LlmInputImage, *, generation_id: UUID
    ) -> PhotoClassification:
        nonlocal call_index
        call_index += 1
        if call_index == 1:
            raise RuntimeError("classification provider timeout or error on photo 1")
        return PhotoClassification(
            suitable=True,
            confidence=0.92,
            title="成功保存的照片2",
            description="第2张照片描述",
            keywords=["照片2"],
        )

    monkeypatch.setattr(container.photo_repository, "save", save)
    monkeypatch.setattr(container.photo_observer._classifier, "classify", classify_with_failure)

    await container.photo_repository.update_settings(
        "local", "default", retention_enabled=True, expected_revision=0
    )

    conn_id, token = await connect(container)
    img1 = _make_test_image("red")
    img2 = _make_test_image("blue")

    async def load_2() -> tuple[LlmInputImage, ...]:
        return (
            LlmInputImage(data=img1, mime_type="image/png"),
            LlmInputImage(data=img2, mime_type="image/png"),
        )

    msg = message(conn_id, "partial-classifier-failure")
    image_input = ChannelInboundImageInput(hashlib.sha256(b"pcf").hexdigest(), load_2)

    try:
        receipt = await container.external_channels.ingest(
            msg, access_token=token, image_input=image_input
        )
        result = await container.external_channels.wait_for_turn(
            conn_id, receipt.channel_turn_id, wait_seconds=5
        )
        assert result.status is ChannelTurnStatus.COMPLETED

        await asyncio.wait_for(photo2_saved.wait(), 10)
        assert len(saved_photos) == 1
        assert saved_photos[0].title == "成功保存的照片2"
    finally:
        await container.stop()


@pytest.mark.asyncio
async def test_multi_image_sticker_learning_saves_all_qualifying_stickers(
    runtime_settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Sticker library processes multiple stickers in batch and persists all qualifying items."""
    container = RuntimeContainer(runtime_settings)
    await container.start()
    recorder = VisionRecorder()
    monkeypatch.setattr(container.agent, "_llm", recorder)

    await container.sticker_repository.update_settings(
        "local", "default", learning_enabled=True, expected_revision=0
    )

    call_index = 0

    async def classify_sticker(
        image: LlmInputImage, *, generation_id: UUID
    ) -> StickerClassification:
        nonlocal call_index
        call_index += 1
        return StickerClassification(
            suitable=True,
            confidence=0.98,
            label=f"表情包{call_index}",
            description=f"测试表情{call_index}",
            expression="happy",
        )

    monkeypatch.setattr(container.sticker_library._classifier, "classify", classify_sticker)

    all_stickers_saved = asyncio.Event()
    original_sticker_save = container.sticker_repository.save

    async def record_sticker_save(
        scope: str, character: str, candidate: StickerSaveCandidate, *, expected_revision: int
    ):
        res = await original_sticker_save(
            scope, character, candidate, expected_revision=expected_revision
        )
        snap = await container.sticker_repository.snapshot(scope, character)
        if len(snap.items) >= 2:
            all_stickers_saved.set()
        return res

    monkeypatch.setattr(container.sticker_repository, "save", record_sticker_save)

    conn_id, token = await connect(container)
    s1 = _make_test_image("green", width=48, height=48)
    s2 = _make_test_image("yellow", width=48, height=48)

    async def load_stickers() -> tuple[LlmInputImage, ...]:
        return (
            LlmInputImage(data=s1, mime_type="image/png"),
            LlmInputImage(data=s2, mime_type="image/png"),
        )

    msg = message(conn_id, "multi-sticker-learn")
    image_input = ChannelInboundImageInput(hashlib.sha256(b"stk-2").hexdigest(), load_stickers)

    try:
        receipt = await container.external_channels.ingest(
            msg, access_token=token, image_input=image_input
        )
        result = await container.external_channels.wait_for_turn(
            conn_id, receipt.channel_turn_id, wait_seconds=5
        )
        assert result.status is ChannelTurnStatus.COMPLETED

        await asyncio.wait_for(all_stickers_saved.wait(), 10)
        snapshot = await container.sticker_repository.snapshot("local", "default")
        assert len(snapshot.items) == 2
        labels = {item.label for item in snapshot.items}
        assert labels == {"表情包1", "表情包2"}
    finally:
        await container.stop()


@pytest.mark.asyncio
async def test_multi_image_observer_batch_cancellation_and_stop_fence(
    runtime_settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Observer batch task responds cleanly to cancel_generation and stop fence."""
    container = RuntimeContainer(runtime_settings)
    await container.start()
    recorder = VisionRecorder()
    monkeypatch.setattr(container.agent, "_llm", recorder)

    await container.photo_repository.update_settings(
        "local", "default", retention_enabled=True, expected_revision=0
    )

    entered = asyncio.Event()
    cancelled = asyncio.Event()

    async def hanging_classify(image: LlmInputImage, *, generation_id: UUID) -> PhotoClassification:
        entered.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            cancelled.set()
            raise
        raise AssertionError("unreachable")

    monkeypatch.setattr(container.photo_observer._classifier, "classify", hanging_classify)

    conn_id, token = await connect(container)
    img1 = _make_test_image("red")
    img2 = _make_test_image("blue")

    async def load_2() -> tuple[LlmInputImage, ...]:
        return (
            LlmInputImage(data=img1, mime_type="image/png"),
            LlmInputImage(data=img2, mime_type="image/png"),
        )

    msg = message(conn_id, "cancel-observer")
    image_input = ChannelInboundImageInput(hashlib.sha256(b"canc-obs").hexdigest(), load_2)

    try:
        receipt = await container.external_channels.ingest(
            msg, access_token=token, image_input=image_input
        )
        await asyncio.wait_for(entered.wait(), 5)
        assert receipt.generation_id in container.photo_observer._tasks

        # Cancel generation in observer
        await container.photo_observer.cancel_generation(receipt.generation_id)
        await asyncio.wait_for(cancelled.wait(), 3)
        assert receipt.generation_id not in container.photo_observer._tasks
    finally:
        await container.stop()


@pytest.mark.asyncio
async def test_multi_image_ambiguous_annotation_attribution(tmp_path: Path) -> None:
    """When multiple candidate photos are in context, ambiguous references return null."""
    db_path = tmp_path / "multi_photo_annot.db"
    database = Database(db_path, StorageConfig(database_path=db_path))
    await database.open()

    try:
        repo = SQLitePhotoMemoryRepository(database)
        await repo.update_settings("user-1", "char-1", retention_enabled=True, expected_revision=0)

        cid, gid, sid = await _seed_source_chain(database)

        # Seed 2 distinct photos under the same generation_id (multi-photo turn)
        img1 = _make_test_image("red")
        img2 = _make_test_image("blue")

        p1 = await repo.save(
            "user-1",
            "char-1",
            PhotoSaveCandidate(
                data=img1,
                mime_type="image/png",
                width=32,
                height=32,
                title="红色气球",
                description="草地上飘着一个红色气球",
                confidence=0.95,
                keywords=("气球", "红色"),
                source_connection_id=UUID(cid),
                generation_id=UUID(gid),
            ),
            expected_revision=1,
        )
        p2 = await repo.save(
            "user-1",
            "char-1",
            PhotoSaveCandidate(
                data=img2,
                mime_type="image/png",
                width=32,
                height=32,
                title="蓝色小船",
                description="湖面上停着一只蓝色小船",
                confidence=0.95,
                keywords=("小船", "蓝色"),
                source_connection_id=UUID(cid),
                generation_id=UUID(gid),
            ),
            expected_revision=1,
        )
        assert p1 is not None and p2 is not None

        # User followup turn in the same session: "这张照片是我买的" (ambiguous reference)
        followup_gid, followup_tid = uuid4(), uuid4()
        now_iso = (datetime.now(UTC) + timedelta(seconds=2)).isoformat()
        async with database.transaction() as conn:
            await conn.execute(
                """INSERT INTO turns (
                       turn_id, session_id, role, committed_text, created_at, source_context_json
                   ) VALUES (?, ?, 'user', ?, ?, ?)""",
                (
                    str(followup_tid),
                    sid,
                    "这张照片是我买的",
                    now_iso,
                    json.dumps({"principal_scope": "user-1", "chat_type": "direct"}),
                ),
            )
            await conn.execute(
                """INSERT INTO generations (
                       generation_id, session_id, turn_id, state, backend_kind, started_at
                   ) VALUES (?, ?, ?, 'completed', 'local', ?)""",
                (str(followup_gid), sid, str(followup_tid), now_iso),
            )

        context = await repo.annotation_context(followup_gid)
        assert context is not None
        # Candidate context contains BOTH photos from the multi-photo turn
        assert len(context.photos) == 2
        photo_ids = {p.photo_id for p in context.photos}
        assert photo_ids == {p1.photo_id, p2.photo_id}

        # Capture actual calls without assertions inside swallowed provider callback
        class Config:
            enabled = True
            provider = "demo"

        captured_calls: list[dict[str, object]] = []

        class RecordingAmbiguousModel:
            def get(self, role: str):
                return Config()

            async def complete(self, role: str, **kwargs: object):
                captured_calls.append({"role": role, **kwargs})
                return "null"

        service = PhotoAnnotationService(
            repo, cast(ModelConfigurationService, RecordingAmbiguousModel())
        )
        service.start()
        await service._run(followup_gid)

        # Assert outside service._run that user JSON has expected IDs and system says ambiguity
        assert len(captured_calls) == 1
        call = captured_calls[0]
        assert call["role"] == "memory_extraction"
        system_prompt = str(call.get("system", ""))
        assert "When multiple candidate photos are present, an ambiguous reference" in system_prompt
        assert "return null" in system_prompt
        user_json = json.loads(str(call.get("user", "{}")))
        assert user_json["new_user_text"] == "这张照片是我买的"
        candidate_ids = {p["photo_id"] for p in user_json["photos"]}
        assert candidate_ids == {str(p1.photo_id), str(p2.photo_id)}

        # Nullcase: zero annotations
        p1_fresh = (await repo.get_photos("user-1", "char-1", [p1.photo_id]))[0]
        p2_fresh = (await repo.get_photos("user-1", "char-1", [p2.photo_id]))[0]
        assert len(p1_fresh.user_annotations) == 0
        assert len(p2_fresh.user_annotations) == 0

        # Positivecase: must use NEW explicitly unambiguous user text
        cid2, gid2, sid2 = await _seed_source_chain(database)
        p3 = await repo.save(
            "user-1",
            "char-1",
            PhotoSaveCandidate(
                data=img1,
                mime_type="image/png",
                width=32,
                height=32,
                title="红色气球",
                description="草地上飘着一个红色气球",
                confidence=0.95,
                keywords=("气球", "红色"),
                source_connection_id=UUID(cid2),
                generation_id=UUID(gid2),
            ),
            expected_revision=1,
        )
        p4 = await repo.save(
            "user-1",
            "char-1",
            PhotoSaveCandidate(
                data=img2,
                mime_type="image/png",
                width=32,
                height=32,
                title="蓝色小船",
                description="湖面上停着一只蓝色小船",
                confidence=0.95,
                keywords=("小船", "蓝色"),
                source_connection_id=UUID(cid2),
                generation_id=UUID(gid2),
            ),
            expected_revision=1,
        )
        assert p3 is not None and p4 is not None

        pos_gid, pos_tid = uuid4(), uuid4()
        pos_now_iso = (datetime.now(UTC) + timedelta(seconds=4)).isoformat()
        async with database.transaction() as conn:
            await conn.execute(
                """INSERT INTO turns (
                       turn_id, session_id, role, committed_text, created_at, source_context_json
                   ) VALUES (?, ?, 'user', ?, ?, ?)""",
                (
                    str(pos_tid),
                    sid2,
                    "红色气球那张是我买的",
                    pos_now_iso,
                    json.dumps({"principal_scope": "user-1", "chat_type": "direct"}),
                ),
            )
            await conn.execute(
                """INSERT INTO generations (
                       generation_id, session_id, turn_id, state, backend_kind, started_at
                   ) VALUES (?, ?, ?, 'completed', 'local', ?)""",
                (str(pos_gid), sid2, str(pos_tid), pos_now_iso),
            )

        pos_captured: list[dict[str, object]] = []

        class SpecificModel:
            def get(self, role: str):
                return Config()

            async def complete(self, role: str, **kwargs: object):
                pos_captured.append({"role": role, **kwargs})
                return json.dumps(
                    {
                        "photo_id": str(p3.photo_id),
                        "quote": "红色气球那张是我买的",
                        "kind": "context",
                        "confidence": 0.95,
                        "replaces_id": None,
                    }
                )

        service2 = PhotoAnnotationService(repo, cast(ModelConfigurationService, SpecificModel()))
        service2.start()
        await service2._run(pos_gid)

        assert len(pos_captured) == 1
        pos_user_json = json.loads(str(pos_captured[0].get("user", "{}")))
        assert pos_user_json["new_user_text"] == "红色气球那张是我买的"

        # Now photo 3 receives the annotation, and photo 4 remains clean
        p3_updated = (await repo.get_photos("user-1", "char-1", [p3.photo_id]))[0]
        p4_updated = (await repo.get_photos("user-1", "char-1", [p4.photo_id]))[0]
        assert len(p3_updated.user_annotations) == 1
        assert p3_updated.user_annotations[0].quote == "红色气球那张是我买的"
        assert len(p4_updated.user_annotations) == 0
    finally:
        await database.close()


@pytest.mark.asyncio
async def test_multi_image_photo_observer_annotation_scheduling_batch_boundary(
    runtime_settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Annotation is scheduled once AFTER entire batch completes,
    not while subsequent photos pending.
    """
    container = RuntimeContainer(runtime_settings)
    await container.start()
    recorder = VisionRecorder()
    monkeypatch.setattr(container.agent, "_llm", recorder)

    await container.photo_repository.update_settings(
        "local", "default", retention_enabled=True, expected_revision=0
    )

    # 4 distinct images
    img1 = _make_test_image("red")
    img2 = _make_test_image("blue")
    img3 = _make_test_image("green")
    img4 = _make_test_image("yellow")
    images = (
        LlmInputImage(data=img1, mime_type="image/png"),
        LlmInputImage(data=img2, mime_type="image/png"),
        LlmInputImage(data=img3, mime_type="image/png"),
        LlmInputImage(data=img4, mime_type="image/png"),
    )

    second_classify_entered = asyncio.Event()
    second_classify_proceed = asyncio.Event()
    classify_count = 0

    async def controlled_classify(
        image: LlmInputImage, *, generation_id: UUID
    ) -> PhotoClassification:
        nonlocal classify_count
        classify_count += 1
        idx = classify_count
        if idx == 2:
            second_classify_entered.set()
            await second_classify_proceed.wait()
        return PhotoClassification(
            suitable=True,
            confidence=0.95,
            title=f"照片 {idx}",
            description=f"第 {idx} 张照片",
            keywords=[f"photo{idx}"],
        )

    monkeypatch.setattr(container.photo_observer._classifier, "classify", controlled_classify)

    annotation_observations: list[UUID] = []
    annotation_scheduled_event = asyncio.Event()
    assert container.photo_observer._annotations is not None

    def record_observe(gid: UUID) -> None:
        annotation_observations.append(gid)
        annotation_scheduled_event.set()

    monkeypatch.setattr(container.photo_observer._annotations, "observe", record_observe)

    all_four_saved = asyncio.Event()
    original_save = container.photo_repository.save
    save_count = 0

    async def counting_save(
        scope: str, character: str, candidate: PhotoSaveCandidate, *, expected_revision: int
    ):
        nonlocal save_count
        res = await original_save(scope, character, candidate, expected_revision=expected_revision)
        if res is not None:
            save_count += 1
            if save_count == 4:
                all_four_saved.set()
        return res

    monkeypatch.setattr(container.photo_repository, "save", counting_save)

    conn_id, token = await connect(container)
    msg = message(conn_id, "batch-4-sched", "发4张照片")
    fp = hashlib.sha256(b"batch-4-sched").hexdigest()

    async def load_4() -> tuple[LlmInputImage, ...]:
        return images

    image_input = ChannelInboundImageInput(fp, load_4)

    try:
        receipt = await container.external_channels.ingest(
            msg, access_token=token, image_input=image_input
        )
        result = await container.external_channels.wait_for_turn(
            conn_id, receipt.channel_turn_id, wait_seconds=5
        )
        assert result.status is ChannelTurnStatus.COMPLETED

        # 1. Wait until photo 2 classification is blocked
        await asyncio.wait_for(second_classify_entered.wait(), 5)

        # Photo 1 is already saved in repository!
        snap_mid = await container.photo_repository.snapshot("local", "default")
        assert len(snap_mid.items) >= 1

        # CRITICAL ASSERTION: annotation task is NOT scheduled while second is pending!
        assert len(annotation_observations) == 0

        # 2. Release second classification so all 4 photos can finish
        second_classify_proceed.set()
        await asyncio.wait_for(all_four_saved.wait(), 10)
        await asyncio.wait_for(annotation_scheduled_event.wait(), 10)

        # Scheduled exactly once after all photos persisted!
        assert len(annotation_observations) == 1
        assert annotation_observations[0] == receipt.generation_id

        # Check candidate count in annotation_context: exactly 4!
        followup_gid, followup_tid = uuid4(), uuid4()
        now_iso = (datetime.now(UTC) + timedelta(seconds=5)).isoformat()
        async with container.database.transaction() as conn:
            await conn.execute(
                """INSERT INTO turns (
                       turn_id, session_id, role, committed_text, created_at, source_context_json
                   ) VALUES (?, ?, 'user', '这张照片是谁', ?, ?)""",
                (
                    str(followup_tid),
                    str(result.session_id),
                    now_iso,
                    json.dumps({"principal_scope": "local", "chat_type": "direct"}),
                ),
            )
            await conn.execute(
                """INSERT INTO generations (
                       generation_id, session_id, turn_id, state, backend_kind, started_at
                   ) VALUES (?, ?, ?, 'completed', 'local', ?)""",
                (str(followup_gid), str(result.session_id), str(followup_tid), now_iso),
            )

        context = await container.photo_repository.annotation_context(followup_gid)
        assert context is not None
        assert len(context.photos) == 4

        # 3. Failure/cancel should not leave premature annotation
        annotation_observations.clear()
        cancel_classify_entered = asyncio.Event()

        async def failing_classify(
            image: LlmInputImage, *, generation_id: UUID
        ) -> PhotoClassification:
            cancel_classify_entered.set()
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                raise
            raise AssertionError("unreachable")

        monkeypatch.setattr(container.photo_observer._classifier, "classify", failing_classify)

        cancel_gid = uuid4()

        async def wait_true():
            return True

        source = PhotoObservationSource(
            principal_scope="local",
            character_id="default",
            connection_id=conn_id,
            generation_id=cancel_gid,
        )
        await container.photo_observer.observe_batch(
            source,
            images[:2],
            wait_for_completion=wait_true,
        )
        await asyncio.wait_for(cancel_classify_entered.wait(), 5)
        # Cancel the generation in observer
        await container.photo_observer.cancel_generation(cancel_gid)
        # Assert annotation was NEVER scheduled
        assert len(annotation_observations) == 0
    finally:
        await container.stop()


@pytest.mark.asyncio
async def test_multi_image_photo_observer_batch_metadata(
    runtime_settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Batch metadata maps distinct EXIF dates and original dimensions to correct saved images."""
    container = RuntimeContainer(runtime_settings)
    await container.start()
    recorder = VisionRecorder()
    monkeypatch.setattr(container.agent, "_llm", recorder)

    await container.photo_repository.update_settings(
        "local", "default", retention_enabled=True, expected_revision=0
    )

    # Distinct images with different dimensions and EXIF dates
    img1 = _make_test_image(
        "red", format="JPEG", width=120, height=80, exif_date="2025:06:01 09:15:00"
    )
    img2 = _make_test_image(
        "blue", format="JPEG", width=90, height=160, exif_date="2025:07:04 14:30:00"
    )

    conn_id, token = await connect(container)

    saved_photos: list[SavedPhoto] = []
    both_saved = asyncio.Event()
    original_save = container.photo_repository.save

    async def save_recorder(
        scope: str, character: str, candidate: PhotoSaveCandidate, *, expected_revision: int
    ):
        res = await original_save(scope, character, candidate, expected_revision=expected_revision)
        if res is not None:
            saved_photos.append(res)
            if len(saved_photos) == 2:
                both_saved.set()
        return res

    monkeypatch.setattr(container.photo_repository, "save", save_recorder)

    call_count = 0

    async def classify_photo(image: LlmInputImage, *, generation_id: UUID) -> PhotoClassification:
        nonlocal call_count
        call_count += 1
        return PhotoClassification(
            suitable=True,
            confidence=0.95,
            title=f"测试照片{call_count}",
            description=f"描述{call_count}",
            keywords=[f"k{call_count}"],
        )

    monkeypatch.setattr(container.photo_observer._classifier, "classify", classify_photo)

    async def load_batch() -> tuple[LlmInputImage, ...]:
        return (
            LlmInputImage(data=img1, mime_type="image/jpeg"),
            LlmInputImage(data=img2, mime_type="image/jpeg"),
        )

    try:
        receipt = await container.external_channels.ingest(
            message(conn_id, "multi-photo-metadata"),
            access_token=token,
            image_input=ChannelInboundImageInput("e" * 64, load_batch),
        )
        result = await container.external_channels.wait_for_turn(
            conn_id, receipt.channel_turn_id, wait_seconds=5
        )
        assert result.status is ChannelTurnStatus.COMPLETED
        assert len(recorder.requests) == 1
        assert len(recorder.requests[0].images) == 2
        for image in recorder.requests[0].images:
            with Image.open(io.BytesIO(image.data)) as decoded:
                assert not decoded.getexif()
        await asyncio.wait_for(both_saved.wait(), 10)
        assert len(saved_photos) == 2

        # Sort or map by title to verify correct mapping
        p1 = next(p for p in saved_photos if p.title == "测试照片1")
        p2 = next(p for p in saved_photos if p.title == "测试照片2")

        # Distinct EXIF dates and dimensions correctly mapped to each image
        assert p1.captured_at == "2025-06-01T09:15:00+08:00"
        assert p1.captured_at_offset == "+08:00"
        assert p1.original_width == 120
        assert p1.original_height == 80
        assert p1.original_mime_type == "image/jpeg"

        assert p2.captured_at == "2025-07-04T14:30:00+08:00"
        assert p2.captured_at_offset == "+08:00"
        assert p2.original_width == 90
        assert p2.original_height == 160
        assert p2.original_mime_type == "image/jpeg"
    finally:
        await container.stop()
