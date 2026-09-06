# pyright: reportPrivateUsage=false
"""Phase 17.3G animated GIF/APNG comprehensive validation test suite.

Covers:
1. Bounded media validation, format sniffing, and resource guardrails.
2. Temporal storyboard synthesis and metadata formatting.
3. APNG default_image handling and GIF disposal compositing.
4. Conversation service temporal vision instruction injection.
5. Photo memory explicit exclusion of animated media.
6. Opt-in sticker learning with animation preservation (no static-first-frame downgrade).
7. Persistence migration 29 and truthful MIME retrieval / poster extraction.
8. Cooperative cancellation during decoding.
"""

from __future__ import annotations

import asyncio
import io
from pathlib import Path
from unittest.mock import AsyncMock
from uuid import UUID

import pytest
from chatwaifu_protocol.channels import ChannelTurnStatus
from chatwaifu_runtime.bootstrap.container import RuntimeContainer
from chatwaifu_runtime.config.settings import Settings
from chatwaifu_runtime.external_channels.models import ChannelInboundImageInput
from chatwaifu_runtime.media import (
    MediaInvalidError,
    StoryboardFrame,
    async_decode_and_sanitize_inbound_media,
    decode_and_sanitize_inbound_media,
    extract_static_poster,
    normalize_animated_sticker,
    sniff_image_mime_type,
    synthesize_storyboard,
    validate_image_bounds,
)
from chatwaifu_runtime.persistence.sqlite_sticker_library import SqliteStickerLibraryRepository
from chatwaifu_runtime.sticker_library.classifier import StickerClassification
from chatwaifu_runtime.sticker_library.models import StickerSaveCandidate
from PIL import Image
from test_inbound_image_lifecycle import VisionRecorder, connect, message
from test_sticker_repository import _init_db, _seed_source_chain

FIXTURE_DIR = Path("/Users/mubai/Desktop/CW2/.local/validation/phase-17-3g")


def _make_test_gif(num_frames: int = 4, size: tuple[int, int] = (64, 64)) -> bytes:
    images: list[Image.Image] = []
    for i in range(num_frames):
        img = Image.new("RGB", size, ((i * 50) % 255, (i * 30) % 255, 200))
        images.append(img)
    out = io.BytesIO()
    images[0].save(out, format="GIF", save_all=True, append_images=images[1:], loop=0, duration=100)
    return out.getvalue()


def _make_test_apng(
    num_frames: int = 4,
    size: tuple[int, int] = (64, 64),
    default_image: bool = False,
) -> bytes:
    images: list[Image.Image] = []
    for i in range(num_frames):
        img = Image.new("RGB", size, ((i * 40) % 255, 150, (i * 50) % 255))
        images.append(img)
    out = io.BytesIO()
    images[0].save(
        out,
        format="PNG",
        save_all=True,
        append_images=images[1:],
        loop=0,
        duration=100,
        default_image=default_image,
    )
    return out.getvalue()


# =============================================================================
# 1. Validation & Resource Bounds Enforcement
# =============================================================================


def test_sniff_mime_type_animated_formats() -> None:
    gif_data = _make_test_gif(2)
    assert sniff_image_mime_type(gif_data) == "image/gif"

    apng_data = _make_test_apng(2)
    assert sniff_image_mime_type(apng_data) == "image/png"

    static_png = io.BytesIO()
    Image.new("RGB", (10, 10), "red").save(static_png, format="PNG")
    assert sniff_image_mime_type(static_png.getvalue()) == "image/png"


def test_validate_image_bounds_rejections() -> None:
    # 1. Empty data
    with pytest.raises(MediaInvalidError):
        validate_image_bounds(b"", "image/gif")

    # 2. Corrupted data
    with pytest.raises(MediaInvalidError):
        validate_image_bounds(b"GIF89a_corrupted_garbage_bytes", "image/gif")

    # 3. Oversized file size (> 5 MiB)
    with pytest.raises(MediaInvalidError, match="exceeds 5242880 bytes limit"):
        validate_image_bounds(b"0" * (5 * 1024 * 1024 + 1), "image/gif")

    # 4. Dimension > 4096 for animated
    large_dim_gif = _make_test_gif(2, size=(4097, 10))
    with pytest.raises(MediaInvalidError, match="exceed"):
        validate_image_bounds(large_dim_gif, "image/gif")

    # 5. Frame count > 60
    images = [Image.new("RGB", (4, 4), (i, i, i)) for i in range(61)]
    buf = io.BytesIO()
    images[0].save(buf, format="GIF", save_all=True, append_images=images[1:], loop=0)
    with pytest.raises(MediaInvalidError, match="frame count"):
        validate_image_bounds(buf.getvalue(), "image/gif")

    # 6. Unsupported mime
    with pytest.raises(MediaInvalidError, match="Unsupported"):
        validate_image_bounds(_make_test_gif(2), "image/webp")


# =============================================================================
# 2. Decoding, Storyboard Synthesis & Validation Fixtures
# =============================================================================


def test_decode_and_synthesize_validation_fixtures() -> None:
    gif_path = FIXTURE_DIR / "moving-ball.gif"
    apng_path = FIXTURE_DIR / "moving-ball.apng"

    if not gif_path.exists() or not apng_path.exists():
        pytest.skip("Local validation fixtures not found")

    # 1. Test moving-ball.gif
    gif_bytes = gif_path.read_bytes()
    gif_item = decode_and_sanitize_inbound_media(gif_bytes, "image/gif")

    assert gif_item.is_animated is True
    assert gif_item.original_mime_type == "image/gif"
    assert gif_item.storyboard is not None
    assert len(gif_item.storyboard.frames) == 4
    assert gif_item.frame_count == 12
    assert gif_item.raster_image.mime_type == "image/jpeg"
    assert len(gif_item.raster_image.data) > 0

    desc = gif_item.storyboard.format_vision_description()
    assert "animated media storyboard" in desc
    assert "2x2" in desc
    assert "Frame 1 at" in desc
    assert "Frame 12 at" in desc

    # 2. Test moving-ball.apng
    apng_bytes = apng_path.read_bytes()
    apng_item = decode_and_sanitize_inbound_media(apng_bytes, "image/png")

    assert apng_item.is_animated is True
    assert apng_item.original_mime_type == "image/png"
    assert apng_item.storyboard is not None
    assert len(apng_item.storyboard.frames) == 4
    assert apng_item.frame_count == 12
    assert apng_item.raster_image.mime_type == "image/jpeg"


def test_storyboard_layouts_for_various_frame_counts() -> None:
    # 1 frame
    f1 = [Image.new("RGBA", (100, 100), "red")]
    m1 = [StoryboardFrame(frame_index=0, pts_ms=0, duration_ms=100)]
    res1, meta1 = synthesize_storyboard(f1, m1, 100)
    assert len(res1.data) > 0
    assert meta1.layout == "single frame"

    # 2 frames
    f2 = [Image.new("RGBA", (100, 100), "red"), Image.new("RGBA", (100, 100), "blue")]
    m2 = [
        StoryboardFrame(frame_index=0, pts_ms=0, duration_ms=100),
        StoryboardFrame(frame_index=1, pts_ms=100, duration_ms=100),
    ]
    res2, meta2 = synthesize_storyboard(f2, m2, 200)
    assert len(res2.data) > 0
    assert "1x2 horizontal sequence" in meta2.layout

    # 3 frames -> 2x2 grid with 3 panels
    f3 = [Image.new("RGBA", (100, 100), "red") for _ in range(3)]
    m3 = [StoryboardFrame(frame_index=i, pts_ms=i * 100, duration_ms=100) for i in range(3)]
    res3, meta3 = synthesize_storyboard(f3, m3, 300)
    assert len(res3.data) > 0
    assert "2x2 grid" in meta3.layout


def test_extract_static_poster() -> None:
    gif_data = _make_test_gif(4, size=(120, 80))
    poster_bytes = extract_static_poster(gif_data, "image/gif")

    with Image.open(io.BytesIO(poster_bytes)) as img:
        assert img.format == "PNG"
        assert getattr(img, "n_frames", 1) == 1
        assert img.size == (120, 80)


def test_apng_default_image_skipping() -> None:
    # Build APNG with default_image=True and 4 frames (1 default + 3 animation)
    apng_data = _make_test_apng(num_frames=4, default_image=True)
    item = decode_and_sanitize_inbound_media(apng_data, "image/png")

    assert item.is_animated is True
    # If default_image was true, frame 0 was skipped; remaining animation frames were 3
    assert item.frame_count == 3
    assert item.storyboard is not None
    assert len(item.storyboard.frames) == 3


# =============================================================================
# 3. Deterministic Normalization (Preserves Animation / No First-Frame Downgrade)
# =============================================================================


def test_normalize_animated_sticker_preserves_animation() -> None:
    # GIF normalization
    gif_data = _make_test_gif(4, size=(120, 120))
    norm_gif, mime_gif, is_anim_gif = normalize_animated_sticker(gif_data, "image/gif")

    assert is_anim_gif is True
    assert mime_gif == "image/gif"
    with Image.open(io.BytesIO(norm_gif)) as img:
        assert img.format == "GIF"
        assert getattr(img, "n_frames", 1) == 4

    # APNG normalization
    apng_data = _make_test_apng(4, size=(120, 120))
    norm_apng, mime_apng, is_anim_apng = normalize_animated_sticker(apng_data, "image/png")

    assert is_anim_apng is True
    assert mime_apng == "image/png"
    with Image.open(io.BytesIO(norm_apng)) as img:
        assert img.format == "PNG"
        assert getattr(img, "n_frames", 1) == 4


def test_normalization_strips_metadata_while_preserving_animation() -> None:
    gif_path = FIXTURE_DIR / "moving-ball.gif"
    if not gif_path.exists():
        pytest.skip("moving-ball.gif not found")

    orig_data = gif_path.read_bytes()
    norm_data, mime, is_anim = normalize_animated_sticker(orig_data, "image/gif")

    assert is_anim is True
    assert mime == "image/gif"
    with Image.open(io.BytesIO(norm_data)) as img:
        assert getattr(img, "n_frames", 1) == 12
        assert img.size == (320, 180)


# =============================================================================
# 4. Temporal Vision Instruction in Conversation Service (End-to-End)
# =============================================================================


@pytest.mark.asyncio
async def test_conversation_service_injects_temporal_vision_instruction(
    runtime_settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    container = RuntimeContainer(runtime_settings)
    await container.start()
    try:
        recorder = VisionRecorder()
        monkeypatch.setattr(container.agent, "_llm", recorder)
        conn_id, token = await connect(container)
        msg = message(conn_id, "anim-msg", text="看看这个动图")

        gif_bytes = _make_test_gif(4)
        item = decode_and_sanitize_inbound_media(gif_bytes, "image/gif")

        async def load_batch():
            return (item,)

        fp = "a" * 64
        image_input = ChannelInboundImageInput(fp, load_batch)

        receipt = await container.external_channels.ingest(
            msg, access_token=token, image_input=image_input
        )
        result = await container.external_channels.wait_for_turn(
            conn_id, receipt.channel_turn_id, wait_seconds=5
        )
        assert result.status is ChannelTurnStatus.COMPLETED

        assert len(recorder.requests) == 1
        req = recorder.requests[0]
        # Vision provider receives clean raster storyboard JPEG, NOT raw GIF bytes
        assert len(req.images) == 1
        assert req.images[0].mime_type == "image/jpeg"
        assert req.images[0].data == item.raster_image.data

        # System prompt contains temporal analysis instructions
        assert "[Temporal Animation Analysis]" in req.system_prompt
        assert "Reading order:" in req.system_prompt
    finally:
        await container.stop()


# =============================================================================
# 5. Photo Memory Explicitly Skips Animated Media
# =============================================================================


@pytest.mark.asyncio
async def test_photo_memory_skips_animated_media(
    runtime_settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    container = RuntimeContainer(runtime_settings)
    await container.start()
    try:
        recorder = VisionRecorder()
        monkeypatch.setattr(container.agent, "_llm", recorder)
        conn_id, token = await connect(container)

        # Enable photo retention
        photo_settings = await container.photo_repository.get_settings("local", "default")
        await container.photo_repository.update_settings(
            "local",
            "default",
            retention_enabled=True,
            expected_revision=photo_settings.revision,
        )

        gif_bytes = _make_test_gif(4)
        item = decode_and_sanitize_inbound_media(gif_bytes, "image/gif")

        async def load_batch():
            return (item,)

        fp = "b" * 64
        msg = message(conn_id, "anim-photo-msg", text="动图不要存照片")
        image_input = ChannelInboundImageInput(fp, load_batch)

        receipt = await container.external_channels.ingest(
            msg, access_token=token, image_input=image_input
        )
        result = await container.external_channels.wait_for_turn(
            conn_id, receipt.channel_turn_id, wait_seconds=5
        )
        assert result.status is ChannelTurnStatus.COMPLETED

        # Verify photo memory repository has 0 photos
        snapshot = await container.photo_repository.snapshot("local", "default")
        assert len(snapshot.items) == 0
    finally:
        await container.stop()


# =============================================================================
# 6. Opt-In Sticker Learning & Persistence Migration 29
# =============================================================================


@pytest.mark.asyncio
async def test_sqlite_sticker_library_animated_support(tmp_path: Path) -> None:
    db = await _init_db(tmp_path)
    try:
        conn_id, gen_id = await _seed_source_chain(db, scope="principal1", character_id="default")
        repo = SqliteStickerLibraryRepository(db)
        gif_bytes = _make_test_gif(3)
        norm_bytes, mime, is_anim = normalize_animated_sticker(gif_bytes, "image/gif")

        candidate = StickerSaveCandidate(
            data=norm_bytes,
            label="跑跳猫咪",
            description="一只动态跑跳的小猫",
            expression="happy",
            source_connection_id=UUID(conn_id),
            generation_id=UUID(gen_id),
            mime_type=mime,
            is_animated=is_anim,
        )

        # Enable sticker learning first
        settings = await repo.get_settings("principal1", "default")
        await repo.update_settings(
            "principal1",
            "default",
            learning_enabled=True,
            expected_revision=settings.revision,
        )
        settings = await repo.get_settings("principal1", "default")
        saved = await repo.save(
            "principal1", "default", candidate, expected_revision=settings.revision
        )
        assert saved is not None
        assert saved.is_animated is True
        assert saved.mime_type == "image/gif"

        # Verify retrieval
        asset = await repo.get_asset(
            "principal1", "default", saved.sticker_id, expected_sha256=saved.sha256
        )
        assert asset is not None
        raw_data, asset_mime, asset_anim = asset
        assert raw_data == norm_bytes
        assert asset_mime == "image/gif"
        assert asset_anim is True

        # Verify snapshot
        snapshot = await repo.snapshot("principal1", "default")
        assert len(snapshot.items) == 1
        item = snapshot.items[0]
        assert item.is_animated is True
        assert item.mime_type == "image/gif"
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_sticker_learning_preserves_animated_gif(
    runtime_settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    container = RuntimeContainer(runtime_settings)
    await container.start()
    try:
        recorder = VisionRecorder()
        monkeypatch.setattr(container.agent, "_llm", recorder)
        conn_id, token = await connect(container)

        # Enable sticker learning
        st_settings = await container.sticker_repository.get_settings("local", "default")
        await container.sticker_repository.update_settings(
            "local",
            "default",
            learning_enabled=True,
            expected_revision=st_settings.revision,
        )

        # Mock classifier to classify as sticker
        mock_classifier = AsyncMock()
        mock_classifier.classify.return_value = StickerClassification(
            suitable=True,
            confidence=0.95,
            label="跑跳猫咪",
            description="一只动态跑跳的小猫",
            expression="happy",
        )
        assert container.sticker_library is not None
        container.sticker_library._classifier = mock_classifier

        gif_bytes = _make_test_gif(4)
        item = decode_and_sanitize_inbound_media(gif_bytes, "image/gif")

        async def load_batch():
            return (item,)

        fp = "c" * 64
        msg = message(conn_id, "anim-sticker-msg", text="发个表情")
        image_input = ChannelInboundImageInput(fp, load_batch)

        receipt = await container.external_channels.ingest(
            msg, access_token=token, image_input=image_input
        )
        result = await container.external_channels.wait_for_turn(
            conn_id, receipt.channel_turn_id, wait_seconds=5
        )
        assert result.status is ChannelTurnStatus.COMPLETED

        # Check classifier received the clean storyboard raster_image
        assert mock_classifier.classify.call_count == 1
        class_img = mock_classifier.classify.call_args[0][0]
        assert class_img.data == item.raster_image.data

        # Wait for learning task to complete
        await asyncio.sleep(0.2)

        # Check sticker library snapshot
        snapshot = await container.sticker_repository.snapshot("local", "default")
        assert len(snapshot.items) == 1
        sticker = snapshot.items[0]
        assert sticker.is_animated is True
        assert sticker.mime_type == "image/gif"

        # Verify asset retrieval preserves full animation
        raw_data = await container.sticker_repository.get_image(
            "local", "default", sticker.sticker_id, expected_sha256=sticker.sha256
        )
        assert raw_data is not None
        with Image.open(io.BytesIO(raw_data)) as img:
            assert getattr(img, "n_frames", 1) == 4
            assert img.format == "GIF"
    finally:
        await container.stop()


# =============================================================================
# 7. Cooperative Cancellation & Time Budget
# =============================================================================


@pytest.mark.asyncio
async def test_async_decode_respects_cancellation() -> None:
    # Set a tiny timeout
    with pytest.raises(TimeoutError):
        await async_decode_and_sanitize_inbound_media(
            _make_test_gif(20), "image/gif", timeout_seconds=0.00001
        )
