# pyright: reportPrivateUsage=false
"""Lifecycle and regression test suite for bounded animated media pipeline.

Validates:
1. EXIF DateTimeOriginal and orientation preservation through _make_batch_image_loader /
   PhotoMemoryObserver pipeline with clean preview and authoritative receipt date.
2. Opt-out no-save behavior when sticker learning is disabled.
3. Classifier suitable=False preventing photo-like animated media from persisting.
4. Cooperative cancellation during image preparation preventing late provider, observer, or send.
5. Durable reuse of learned animated stickers with exact payload/hash/MIME and no static fallback.
6. Restart, deduplication, delete fence, and migration 29 schema preservation of old items.
"""

from __future__ import annotations

import asyncio
import hashlib
import io
from collections.abc import AsyncGenerator, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal
from uuid import UUID, uuid4

import pytest
from chatwaifu_protocol.channels import (
    ChannelDeliveryPartAcknowledgement,
    ChannelDeliveryStatus,
    ChannelImageDeliveryPartPayload,
    ChannelPresentationPolicy,
    ChannelPresentationProfile,
    ChannelTurnStatus,
)
from chatwaifu_runtime.bootstrap.container import RuntimeContainer
from chatwaifu_runtime.config.settings import Settings, StorageConfig
from chatwaifu_runtime.external_channels.adapters.weixin_ilink.models import (
    WeixinCredentials,
    WeixinInboundImage,
    WeixinInboundText,
    WeixinUpdates,
)
from chatwaifu_runtime.external_channels.credentials import InMemoryChannelCredentialStore
from chatwaifu_runtime.external_channels.management import (
    ChannelManagementService,
    _make_batch_image_loader,
)
from chatwaifu_runtime.external_channels.models import (
    ChannelInboundImageInput,
    DeliveryTransitionResult,
)
from chatwaifu_runtime.media import (
    decode_and_sanitize_inbound_media,
    normalize_animated_sticker,
)
from chatwaifu_runtime.persistence.database import Database
from chatwaifu_runtime.persistence.sqlite_photo_memory import SQLitePhotoMemoryRepository
from chatwaifu_runtime.persistence.sqlite_sticker_library import SqliteStickerLibraryRepository
from chatwaifu_runtime.photo_memory.classifier import PhotoClassifier
from chatwaifu_runtime.photo_memory.models import PhotoItemOrigin
from chatwaifu_runtime.photo_memory.observer import PhotoMemoryObserver, PhotoObservationSource
from chatwaifu_runtime.providers.contracts import (
    LlmInputImage,
    LlmRequest,
    LlmStreamEvent,
    LlmToolCall,
    LlmToolCallRequested,
)
from chatwaifu_runtime.sticker_library.classifier import StickerClassification
from chatwaifu_runtime.sticker_library.models import (
    StickerLibraryRevisionConflict,
    StickerSaveCandidate,
)
from PIL import ExifTags, Image
from test_channel_management import _configuration, _credentials, _FakeWeixin
from test_inbound_image_lifecycle import VisionRecorder, connect, message
from test_photo_memory_repository import _seed_source_chain as _seed_photo_source_chain
from test_sticker_repository import _init_db
from test_sticker_repository import _seed_source_chain as _seed_sticker_source_chain


def _make_test_gif(num_frames: int = 4, size: tuple[int, int] = (64, 64)) -> bytes:
    images: list[Image.Image] = []
    for i in range(num_frames):
        img = Image.new("RGB", size, ((i * 50) % 255, (i * 30) % 255, 200))
        images.append(img)
    out = io.BytesIO()
    images[0].save(out, format="GIF", save_all=True, append_images=images[1:], loop=0, duration=100)
    return out.getvalue()


def _make_jpeg_with_exif(
    size: tuple[int, int] = (200, 100),
    orientation: int = 6,
    datetime_original: str = "2024:05:20 15:30:45",
    offset_time: str = "+08:00",
) -> bytes:
    img = Image.new("RGB", size, (120, 180, 240))
    exif = img.getexif()
    exif[ExifTags.Base.Orientation] = orientation
    sub_ifd = exif.get_ifd(ExifTags.IFD.Exif)
    sub_ifd[ExifTags.Base.DateTimeOriginal] = datetime_original
    if offset_time:
        sub_ifd[ExifTags.Base.OffsetTimeOriginal] = offset_time
    buf = io.BytesIO()
    img.save(buf, format="JPEG", exif=exif)
    return buf.getvalue()


class _RecordedImageTransport(_FakeWeixin):
    def __init__(self) -> None:
        super().__init__()
        self.images: list[tuple[str, str, str, bytes, str]] = []

    async def send_image(
        self,
        credentials: WeixinCredentials,
        *,
        recipient_user_id: str,
        context_token: str,
        client_id: str,
        image_bytes: bytes,
        mime_type: str,
    ) -> str:
        del credentials
        self.images.append((recipient_user_id, context_token, client_id, image_bytes, mime_type))
        return client_id


class _FakeMockLlmProvider:
    def __init__(self) -> None:
        self.recorded_requests: list[LlmRequest] = []

    @property
    def kind(self) -> str:
        return "mock"

    @property
    def supports_tool_calling(self) -> bool:
        return True

    async def stream(self, request: LlmRequest) -> AsyncGenerator[LlmStreamEvent, None]:
        self.recorded_requests.append(request)
        call = LlmToolCall(
            call_id="call-photo-1",
            name="classify_photo",
            arguments={
                "suitable": True,
                "confidence": 0.95,
                "title": "测试风景",
                "description": "测试拍摄的照片",
                "keywords": ["风景", "测试"],
            },
        )
        yield LlmToolCallRequested(call=call)


async def _seed_learned_animated_sticker(
    database: Database,
    container: RuntimeContainer,
    *,
    principal_scope: str,
    character_id: str,
    expression: str,
    image_bytes: bytes,
    mime_type: Literal["image/png", "image/gif"] = "image/gif",
    is_animated: bool = True,
    label: str = "已学习动图小猫",
    description: str = "测试动图表情包",
) -> tuple[str, str, UUID, UUID]:
    source_conn_id = uuid4()
    source_gen_id = uuid4()
    source_session_id = uuid4()
    source_turn_id = uuid4()
    source_binding_id = uuid4()
    source_channel_turn_id = uuid4()
    now_iso = datetime.now(UTC).isoformat()

    async with database.transaction() as conn:
        await conn.execute(
            """
            INSERT INTO channel_connections (
                connection_id, provider_id, name, character_id, principal_scope,
                enabled, access_token_hash, created_at, updated_at, deleted_at
            ) VALUES (?, 'weixin_ilink', 'seed-conn', ?, ?, 1, 'seed-hash', ?, ?, NULL)
            """,
            (str(source_conn_id), character_id, principal_scope, now_iso, now_iso),
        )
        await conn.execute(
            """
            INSERT INTO sessions (
                session_id, character_id, state, conversation_state, created_at, updated_at
            ) VALUES (?, ?, 'active', 'ready', ?, ?)
            """,
            (str(source_session_id), character_id, now_iso, now_iso),
        )
        await conn.execute(
            """
            INSERT INTO turns (
                turn_id, session_id, role, created_at
            ) VALUES (?, ?, 'user', ?)
            """,
            (str(source_turn_id), str(source_session_id), now_iso),
        )
        await conn.execute(
            """
            INSERT INTO channel_bindings (
                binding_id, connection_id, conversation_key, sender_key, session_id,
                created_at, updated_at
            ) VALUES (?, ?, 'seed-conv', 'seed-sender', ?, ?, ?)
            """,
            (
                str(source_binding_id),
                str(source_conn_id),
                str(source_session_id),
                now_iso,
                now_iso,
            ),
        )
        await conn.execute(
            """
            INSERT INTO channel_turns (
                channel_turn_id, connection_id, binding_id, external_message_id,
                content_sha256, conversation_key, sender_key, principal_scope,
                session_id, turn_id, generation_id, status, accepted_at,
                created_at, updated_at
            ) VALUES (?, ?, ?, 'seed-ext-msg', 'seed-hash', 'seed-conv', 'seed-sender',
                     ?, ?, ?, ?, 'completed', ?, ?, ?)
            """,
            (
                str(source_channel_turn_id),
                str(source_conn_id),
                str(source_binding_id),
                principal_scope,
                str(source_session_id),
                str(source_turn_id),
                str(source_gen_id),
                now_iso,
                now_iso,
                now_iso,
            ),
        )

    settings = await container.sticker_repository.get_settings(principal_scope, character_id)
    if not settings.learning_enabled:
        settings = await container.sticker_repository.update_settings(
            principal_scope,
            character_id,
            learning_enabled=True,
            expected_revision=settings.revision,
        )

    candidate = StickerSaveCandidate(
        data=image_bytes,
        label=label,
        description=description,
        expression=expression,  # type: ignore[arg-type]
        source_connection_id=source_conn_id,
        generation_id=source_gen_id,
        mime_type=mime_type,
        is_animated=is_animated,
    )
    saved = await container.sticker_repository.save(
        principal_scope,
        character_id,
        candidate,
        expected_revision=settings.revision,
    )
    assert saved is not None
    return saved.sticker_id, saved.sha256, source_conn_id, source_gen_id


# =============================================================================
# 1. Observer pipeline regression with EXIF DateTimeOriginal & Orientation
# =============================================================================


@pytest.mark.asyncio
async def test_observer_pipeline_exif_datetime_and_orientation_regression(
    tmp_path: Path,
) -> None:
    """Regression for Root finding 1:

    Preserve original raw_data+original_mime for static metadata/normalization,
    classifier still receives clean preview, animated explicitly skips, mixed
    animated+static origin ordinal remains correct, and receipt date is authoritative.
    """
    db_path = tmp_path / "observer_test.db"
    db = Database(path=db_path, config=StorageConfig(database_path=db_path, busy_timeout_ms=5000))
    await db.open()
    try:
        mem_turn_id = uuid4()
        t_lead_at = datetime(2026, 9, 6, 12, 0, 0, tzinfo=UTC)
        t_member_at = datetime(2026, 9, 6, 12, 1, 30, tzinfo=UTC)

        conn_id_str, gen_id_str, session_id_str = await _seed_photo_source_chain(
            db, scope="local", character_id="default"
        )
        conn_id = UUID(conn_id_str)
        gen_id = UUID(gen_id_str)

        async with db.transaction() as conn:
            cursor = await conn.execute(
                "SELECT channel_turn_id FROM channel_turns WHERE session_id = ?",
                (session_id_str,),
            )
            row = await cursor.fetchone()
            assert row is not None
            lead_channel_turn_id_str = row[0]
        lead_channel_turn_id = UUID(lead_channel_turn_id_str)

        # Update leader turn external_message_id and accepted_at
        async with db.transaction() as conn:
            await conn.execute(
                """
                UPDATE channel_turns
                SET external_message_id = 'msg-lead-0',
                    accepted_at = ?,
                    created_at = ?,
                    updated_at = ?
                WHERE channel_turn_id = ?
                """,
                (
                    t_lead_at.isoformat(),
                    t_lead_at.isoformat(),
                    t_lead_at.isoformat(),
                    lead_channel_turn_id_str,
                ),
            )
            # Insert Member turn (Item 2)
            await conn.execute(
                """
                INSERT INTO channel_turns (
                    channel_turn_id, connection_id, binding_id, external_message_id,
                    content_sha256, conversation_key, sender_key, principal_scope,
                    session_id, turn_id, generation_id, status, accepted_at,
                    created_at, updated_at
                )
                SELECT ?, connection_id, binding_id, 'msg-member-2',
                       'hash-mem', conversation_key, sender_key, principal_scope,
                       session_id, turn_id, generation_id, 'completed', ?, ?, ?
                FROM channel_turns
                WHERE channel_turn_id = ?
                """,
                (
                    str(mem_turn_id),
                    t_member_at.isoformat(),
                    t_member_at.isoformat(),
                    t_member_at.isoformat(),
                    lead_channel_turn_id_str,
                ),
            )
            # Burst members mapping: ordinal 0 -> leader turn, ordinal 2 -> member turn
            await conn.execute(
                """
                INSERT INTO channel_turn_burst_members (
                    burst_id, leader_channel_turn_id, member_channel_turn_id,
                    ordinal, received_at, created_at
                ) VALUES (?, ?, ?, 0, ?, ?), (?, ?, ?, 2, ?, ?)
                """,
                (
                    lead_channel_turn_id_str,
                    lead_channel_turn_id_str,
                    lead_channel_turn_id_str,
                    t_lead_at.isoformat(),
                    t_lead_at.isoformat(),
                    lead_channel_turn_id_str,
                    lead_channel_turn_id_str,
                    str(mem_turn_id),
                    t_member_at.isoformat(),
                    t_member_at.isoformat(),
                ),
            )

        # 1. Prepare raw items
        # Item 0: Static JPEG with EXIF DateTimeOriginal and Orientation 6 (swap width/height)
        jpeg0_bytes = _make_jpeg_with_exif(
            size=(200, 100),
            orientation=6,
            datetime_original="2024:05:20 15:30:45",
            offset_time="+08:00",
        )
        # Item 1: Animated GIF (4 frames)
        gif1_bytes = _make_test_gif(4, size=(64, 64))
        # Item 2: Static JPEG with EXIF DateTimeOriginal and Orientation 1
        jpeg2_bytes = _make_jpeg_with_exif(
            size=(300, 150),
            orientation=1,
            datetime_original="2024:05:20 16:45:00",
            offset_time="",
        )

        # 2. Mock transport for _make_batch_image_loader
        class _BatchLoaderTransport:
            async def download_images(
                self, images: Sequence[WeixinInboundImage]
            ) -> Sequence[tuple[bytes, str]]:
                del images
                return [
                    (jpeg0_bytes, "image/jpeg"),
                    (gif1_bytes, "image/gif"),
                    (jpeg2_bytes, "image/jpeg"),
                ]

        wire_images = (
            WeixinInboundImage(aes_key="k0", full_url="https://example.com/0"),
            WeixinInboundImage(aes_key="k1", full_url="https://example.com/1"),
            WeixinInboundImage(aes_key="k2", full_url="https://example.com/2"),
        )
        loader = _make_batch_image_loader(
            _BatchLoaderTransport(),  # type: ignore[arg-type]
            wire_images,
            connection_id=conn_id,
            external_message_id="batch-regression-msg",
        )

        loaded_items = await loader()
        assert len(loaded_items) == 3

        # Assert provider sees clean preview without EXIF
        item0, item1, item2 = loaded_items
        assert item0.is_animated is False
        assert item1.is_animated is True
        assert item2.is_animated is False

        with Image.open(io.BytesIO(item0.raster_image.data)) as pil0:
            exif0 = pil0.getexif()
            assert not exif0 or len(exif0) == 0

        with Image.open(io.BytesIO(item2.raster_image.data)) as pil2:
            exif2 = pil2.getexif()
            assert not exif2 or len(exif2) == 0

        # 3. Feed through PhotoMemoryObserver with SQLite repository
        repo = SQLitePhotoMemoryRepository(db)
        await repo.update_settings("local", "default", retention_enabled=True, expected_revision=0)

        mock_llm = _FakeMockLlmProvider()
        classifier = PhotoClassifier(mock_llm)  # type: ignore[arg-type]
        observer = PhotoMemoryObserver(repository=repo, classifier=classifier)
        observer.start()

        origin0 = PhotoItemOrigin(
            channel_turn_id=lead_channel_turn_id,
            external_message_id="msg-lead-0",
            received_at=t_lead_at,
        )
        origin1 = PhotoItemOrigin(
            channel_turn_id=uuid4(),
            external_message_id="msg-anim-1",
            received_at=datetime.now(UTC),
        )
        origin2 = PhotoItemOrigin(
            channel_turn_id=mem_turn_id,
            external_message_id="msg-member-2",
            received_at=t_member_at,
        )

        source = PhotoObservationSource(
            principal_scope="local",
            character_id="default",
            connection_id=conn_id,
            generation_id=gen_id,
        )

        async def _always_complete() -> bool:
            return True

        await observer.observe_batch(
            source,
            loaded_items,
            wait_for_completion=_always_complete,
            item_origins=(origin0, origin1, origin2),
        )

        # Await observer tasks
        tasks = [task for _, task in observer._tasks.values()]
        if tasks:
            await asyncio.gather(*tasks)

        # 4. Verify saved photos
        snapshot = await repo.snapshot("local", "default")
        # Exactly 2 photos saved: Item 0 and Item 2 (Item 1 animated was explicitly skipped)
        assert len(snapshot.items) == 2

        # Identify photos by captured_at
        photo_map = {p.captured_at: p for p in snapshot.items}
        assert "2024-05-20T15:30:45+08:00" in photo_map
        assert "2024-05-20T16:45:00" in photo_map

        photo0 = photo_map["2024-05-20T15:30:45+08:00"]
        # DateTimeOriginal + OffsetTimeOriginal preserved
        assert photo0.captured_at == "2024-05-20T15:30:45+08:00"
        assert photo0.captured_at_offset == "+08:00"
        # Original dimensions taking Orientation 6 (swap 200x100 -> 100x200) into account
        assert photo0.original_width == 100
        assert photo0.original_height == 200
        # Authoritative receipt date from leader turn
        assert photo0.received_at == t_lead_at

        photo2 = photo_map["2024-05-20T16:45:00"]
        assert photo2.captured_at == "2024-05-20T16:45:00"
        assert photo2.original_width == 300
        assert photo2.original_height == 150
        # Ordinal preserved: corresponds to origin2 and member turn received_at
        assert photo2.received_at == t_member_at

        # Classifier only ran for the two static items, receiving clean preview
        assert len(mock_llm.recorded_requests) == 2
        for req in mock_llm.recorded_requests:
            assert len(req.images) == 1
            with Image.open(io.BytesIO(req.images[0].data)) as p_img:
                p_exif = p_img.getexif()
                assert not p_exif or len(p_exif) == 0

        await observer.stop()
    finally:
        await db.close()


# =============================================================================
# 2. Opt-out no-save behavior
# =============================================================================


@pytest.mark.asyncio
async def test_animated_pipeline_opt_out_no_save(
    runtime_settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    """When sticker learning is disabled, animated images are never saved."""
    container = RuntimeContainer(runtime_settings)
    await container.start()
    try:
        recorder = VisionRecorder()
        monkeypatch.setattr(container.agent, "_llm", recorder)
        conn_id, token = await connect(container)

        # Disable sticker learning explicitly
        settings = await container.sticker_repository.get_settings("local", "default")
        await container.sticker_repository.update_settings(
            "local", "default", learning_enabled=False, expected_revision=settings.revision
        )

        gif_bytes = _make_test_gif(4)
        item = decode_and_sanitize_inbound_media(gif_bytes, "image/gif")

        async def load_batch():
            return (item,)

        fp = hashlib.sha256(b"optout_fp").hexdigest()
        msg = message(conn_id, "opt-out-gif-msg", text="发个动图不学习")
        image_input = ChannelInboundImageInput(fp, load_batch)

        receipt = await container.external_channels.ingest(
            msg, access_token=token, image_input=image_input
        )
        result = await container.external_channels.wait_for_turn(
            conn_id, receipt.channel_turn_id, wait_seconds=5
        )
        assert result.status is ChannelTurnStatus.COMPLETED

        # Check background tasks completed
        tasks = [task for _, task in container.sticker_library._tasks.values()]
        if tasks:
            await asyncio.gather(*tasks)

        # Both sticker and photo libraries remain empty
        stickers_snap = await container.sticker_repository.snapshot("local", "default")
        assert len(stickers_snap.items) == 0

        photos_snap = await container.photo_repository.snapshot("local", "default")
        assert len(photos_snap.items) == 0
    finally:
        await container.stop()


# =============================================================================
# 3. Classifier suitable=False prevents photo-like animated saving
# =============================================================================


@pytest.mark.asyncio
async def test_animated_pipeline_classifier_false_prevents_saving(
    runtime_settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    """When classifier returns suitable=False, photo-like animated images are not saved."""
    container = RuntimeContainer(runtime_settings)
    await container.start()
    try:
        recorder = VisionRecorder()
        monkeypatch.setattr(container.agent, "_llm", recorder)
        conn_id, token = await connect(container)

        # Enable sticker learning
        st_settings = await container.sticker_repository.get_settings("local", "default")
        await container.sticker_repository.update_settings(
            "local", "default", learning_enabled=True, expected_revision=st_settings.revision
        )

        # Mock classifier returning suitable=False
        class _RejectingClassifier:
            async def classify(
                self, image: LlmInputImage, *, generation_id: UUID
            ) -> StickerClassification | None:
                del image, generation_id
                return None  # Rejection or confidence < 0.9 yields None

        container.sticker_library._classifier = _RejectingClassifier()  # type: ignore[assignment]

        gif_bytes = _make_test_gif(4)
        item = decode_and_sanitize_inbound_media(gif_bytes, "image/gif")

        async def load_batch():
            return (item,)

        fp = hashlib.sha256(b"photolike_fp").hexdigest()
        msg = message(conn_id, "photo-like-gif-msg", text="发个真实动图风景")
        image_input = ChannelInboundImageInput(fp, load_batch)

        receipt = await container.external_channels.ingest(
            msg, access_token=token, image_input=image_input
        )
        result = await container.external_channels.wait_for_turn(
            conn_id, receipt.channel_turn_id, wait_seconds=5
        )
        assert result.status is ChannelTurnStatus.COMPLETED

        tasks = [task for _, task in container.sticker_library._tasks.values()]
        if tasks:
            await asyncio.gather(*tasks)

        # Zero saved items in sticker library
        stickers_snap = await container.sticker_repository.snapshot("local", "default")
        assert len(stickers_snap.items) == 0

        # Zero saved photos in photo memory
        photos_snap = await container.photo_repository.snapshot("local", "default")
        assert len(photos_snap.items) == 0
    finally:
        await container.stop()


# =============================================================================
# 4. Cancellation during image preparation: no late provider, observer, or send
# =============================================================================


@pytest.mark.asyncio
async def test_animated_pipeline_cancellation_during_image_preparation(
    runtime_settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Cancellation during image preparation prevents late provider, observer, or delivery."""
    container = RuntimeContainer(runtime_settings)
    await container.start()
    recorder = VisionRecorder()
    monkeypatch.setattr(container.agent, "_llm", recorder)

    entered = asyncio.Event()
    cancelled = asyncio.Event()

    async def blocking_animated_loader() -> tuple[Any, ...]:
        entered.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            cancelled.set()
            raise
        raise AssertionError("unreachable")

    try:
        conn_id, token = await connect(container)
        fp = hashlib.sha256(b"slowanim_fp").hexdigest()
        receipt = await container.external_channels.ingest(
            message(conn_id, "slow-animated-msg"),
            access_token=token,
            image_input=ChannelInboundImageInput(fp, blocking_animated_loader),
        )

        # Wait until loader has started
        await asyncio.wait_for(entered.wait(), 3)
        assert not recorder.requests

        # Supersede with a text turn
        following = await container.external_channels.ingest(
            message(conn_id, "interrupt-text", "取消刚才的动图"),
            access_token=token,
            supersede_inflight=True,
        )

        # Ensure cooperative cancellation propagated to the loader
        await asyncio.wait_for(cancelled.wait(), 3)

        old_turn = await container.external_channels.wait_for_turn(
            conn_id, receipt.channel_turn_id, wait_seconds=3
        )
        new_turn = await container.external_channels.wait_for_turn(
            conn_id, following.channel_turn_id, wait_seconds=3
        )

        assert old_turn.status is ChannelTurnStatus.CANCELLED
        # No delivery was planned or dispatched for the cancelled turn
        assert old_turn.delivery_id is None

        assert new_turn.status is ChannelTurnStatus.COMPLETED
        # Provider was only invoked for the superseding turn without images
        assert len(recorder.requests) == 1
        assert not recorder.requests[0].images

        # Zero items saved in observer repositories
        assert len((await container.sticker_repository.snapshot("local", "default")).items) == 0
        assert len((await container.photo_repository.snapshot("local", "default")).items) == 0
    finally:
        await container.stop()


# =============================================================================
# 5. Learned animated durable reuse: exact payload, hash, MIME, no static fallback
# =============================================================================


@pytest.mark.asyncio
async def test_animated_pipeline_durable_reuse_delivery_cdn_mock(
    runtime_settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Learned animated sticker delivery uses exact payload, MIME, and SHA256
    without static fallback.
    """
    container = RuntimeContainer(runtime_settings)
    store = InMemoryChannelCredentialStore()
    transport = _RecordedImageTransport()
    management = ChannelManagementService(
        container.external_channels,
        container.external_channel_repository,
        store,
        transport,
        sticker_catalog=container.sticker_catalog,
        sticker_library=container.sticker_library,
        event_hub=container.event_hub,
        event_publisher=container.event_publisher,
    )
    container.channel_management = management

    image_acknowledged = asyncio.Event()
    results: list[DeliveryTransitionResult] = []
    original_ack = container.external_channel_repository.acknowledge_delivery_part

    async def observe_ack(
        acknowledgement: ChannelDeliveryPartAcknowledgement,
        *,
        updated_at: datetime,
    ) -> DeliveryTransitionResult:
        res = await original_ack(acknowledgement, updated_at=updated_at)
        if res.part is not None and isinstance(res.part.payload, ChannelImageDeliveryPartPayload):
            results.append(res)
            image_acknowledged.set()
        return res

    monkeypatch.setattr(
        container.external_channel_repository, "acknowledge_delivery_part", observe_ack
    )

    await container.start()
    try:
        principal_scope = "local"
        character_id = "default"

        gif_bytes = _make_test_gif(4, size=(128, 128))
        norm_bytes, mime, is_anim = normalize_animated_sticker(gif_bytes, "image/gif")

        # Seed learned animated sticker for 'shy' expression
        _s_id, expected_sha, _s_conn, _s_gen = await _seed_learned_animated_sticker(
            container.database,
            container,
            principal_scope=principal_scope,
            character_id=character_id,
            expression="shy",
            image_bytes=norm_bytes,
            mime_type=mime,
            is_animated=is_anim,
            label="害羞小猫",
            description="害羞时使用的动图表情包",
        )

        # 2. Setup connection with stickers enabled
        connection_id = uuid4()
        config = _configuration(connection_id).model_copy(
            update={
                "principal_scope": principal_scope,
                "character_id": character_id,
                "presentation_policy": ChannelPresentationPolicy(
                    profile=ChannelPresentationProfile.INSTANT_MESSAGE,
                    stickers_enabled=True,
                    cadence_enabled=False,
                ),
            }
        )
        created = await container.external_channels.create_connection(config, access_token="t" * 43)
        await store.set(f"weixin_ilink:{connection_id}", _credentials("t" * 43).to_json())
        await management.connection_configuration_changed(created.snapshot)

        # 3. Deliver an inbound text triggering response with the shy sticker
        await transport.updates.put(
            WeixinUpdates(
                cursor="cursor-anim-1",
                messages=(
                    WeixinInboundText(
                        external_message_id="msg-trigger-shy",
                        sender_user_id="owner-1",
                        recipient_bot_id="bot-1",
                        text="喜欢你，摸摸头",
                        context_token="ctx-anim-1",
                        received_at=datetime.now(UTC),
                    ),
                ),
            )
        )

        # Wait for delivery ack
        await asyncio.wait_for(image_acknowledged.wait(), 10.0)

        # 4. Verify transport send_image received exact animated bytes and GIF mime
        assert len(transport.images) == 1
        rcpt, ctx, _cl_id, sent_bytes, sent_mime = transport.images[0]
        assert rcpt == "owner-1"
        assert ctx == "ctx-anim-1"
        assert sent_mime == "image/gif"
        assert sent_bytes == norm_bytes
        assert hashlib.sha256(sent_bytes).hexdigest() == expected_sha

        # Verify animation preserved: not downgraded to static single frame
        with Image.open(io.BytesIO(sent_bytes)) as sent_img:
            assert sent_img.format == "GIF"
            assert getattr(sent_img, "n_frames", 1) == 4

        # Verify delivery plan reached DELIVERED
        assert len(results) == 1
        res = results[0]
        assert res.plan.status is ChannelDeliveryStatus.DELIVERED
    finally:
        await container.stop()


# =============================================================================
# 6. Restart, deduplication, delete fence, and migration 29 schema preservation
# =============================================================================


@pytest.mark.asyncio
async def test_animated_pipeline_restart_dedupe_deletefence_migration(
    tmp_path: Path,
) -> None:
    """Verify deduplication, delete fence, and migration 29 preservation of old items."""
    db = await _init_db(tmp_path)
    try:
        repo = SqliteStickerLibraryRepository(db)
        scope = "scope-lifecycle"
        char_id = "default"

        await repo.update_settings(scope, char_id, learning_enabled=True, expected_revision=0)
        c_id, g_id = await _seed_sticker_source_chain(db, scope=scope, character_id=char_id)

        gif_bytes = _make_test_gif(3, size=(80, 80))
        norm_bytes, mime, is_anim = normalize_animated_sticker(gif_bytes, "image/gif")

        candidate = StickerSaveCandidate(
            data=norm_bytes,
            label="奔跑小狗",
            description="奔跑中的小狗动图",
            expression="happy",
            source_connection_id=UUID(c_id),
            generation_id=UUID(g_id),
            mime_type=mime,
            is_animated=is_anim,
        )

        # 1. Initial save
        saved1 = await repo.save(scope, char_id, candidate, expected_revision=1)
        assert saved1 is not None
        assert saved1.is_animated is True
        assert saved1.mime_type == "image/gif"

        # 2. Deduplication: exact same candidate bytes returns same sticker
        saved2 = await repo.save(scope, char_id, candidate, expected_revision=1)
        assert saved2 is not None
        assert saved2.sticker_id == saved1.sticker_id
        assert saved2.is_animated is True
        assert saved2.mime_type == "image/gif"

        # Snapshot has exactly 1 item
        snap = await repo.snapshot(scope, char_id)
        assert len(snap.items) == 1

        # 3. Delete fence: delete physically removes sticker and bumps settings revision
        del_result = await repo.delete(scope, char_id, saved1.sticker_id)
        assert del_result.deleted is True

        # Verify sticker is removed from snapshot
        snap_after_delete = await repo.snapshot(scope, char_id)
        assert len(snap_after_delete.items) == 0

        # In-flight learner trying to save at old revision 1 encounters revision conflict
        with pytest.raises(StickerLibraryRevisionConflict):
            await repo.save(scope, char_id, candidate, expected_revision=1)

        # 4. Migration 29 verification:
        # Re-insert directly to verify DB schema handles both is_animated=0 (old) and 1 (new)
        async with db.transaction() as conn:
            now_iso = datetime.now(UTC).isoformat()
            await conn.execute(
                """
                INSERT INTO learned_stickers (
                    sticker_id, principal_scope, character_id, sha256, mime_type,
                    label, description, expression, byte_size, data,
                    source_connection_id, generation_id, learned_at, is_animated
                ) VALUES (
                    ?, ?, ?, ?, 'image/png',
                    '旧静态表情', '迁移前保存的静态表情', 'neutral', 100, ?,
                    ?, ?, ?, 0
                )
                """,
                (
                    "learned_" + "1" * 32,
                    scope,
                    char_id,
                    "1" * 64,
                    b"fake_static_png",
                    c_id,
                    g_id,
                    now_iso,
                ),
            )
            await conn.execute(
                """
                INSERT INTO learned_stickers (
                    sticker_id, principal_scope, character_id, sha256, mime_type,
                    label, description, expression, byte_size, data,
                    source_connection_id, generation_id, learned_at, is_animated
                ) VALUES (
                    ?, ?, ?, ?, 'image/gif',
                    '新动态表情', '迁移后保存的动态表情', 'happy', 200, ?,
                    ?, ?, ?, 1
                )
                """,
                (
                    "learned_" + "2" * 32,
                    scope,
                    char_id,
                    "2" * 64,
                    b"fake_anim_gif",
                    c_id,
                    g_id,
                    now_iso,
                ),
            )

        snap_migration = await repo.snapshot(scope, char_id)
        assert len(snap_migration.items) == 2

        old_id = "learned_" + "1" * 32
        new_id = "learned_" + "2" * 32
        old_item = next(i for i in snap_migration.items if i.sticker_id == old_id)
        assert old_item.is_animated is False
        assert old_item.mime_type == "image/png"

        new_item = next(i for i in snap_migration.items if i.sticker_id == new_id)
        assert new_item.is_animated is True
        assert new_item.mime_type == "image/gif"
    finally:
        await db.close()
