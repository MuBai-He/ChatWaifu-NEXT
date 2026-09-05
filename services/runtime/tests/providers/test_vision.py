"""Tests for provider-neutral vision contracts and provider adapters."""

import base64
from typing import cast
from uuid import uuid4

import pytest
from chatwaifu_runtime.providers.contracts import (
    LlmImageInputUnavailableError,
    LlmInputImage,
    LlmRequest,
)
from chatwaifu_runtime.providers.demo_llm import DemoLlmProvider
from chatwaifu_runtime.providers.openai_compatible import (
    _is_retryable_error,  # pyright: ignore[reportPrivateUsage]
    build_messages,
)


def test_llm_input_image_validation() -> None:
    img = LlmInputImage(data=b"\x89PNG\r\n\x1a\n123", mime_type="image/png")
    assert img.mime_type == "image/png"
    assert img.data == b"\x89PNG\r\n\x1a\n123"
    assert "123" not in repr(img)

    with pytest.raises(ValueError, match="unsupported image mime type"):
        LlmInputImage(data=b"123", mime_type="image/gif")  # type: ignore[arg-type]

    with pytest.raises(ValueError, match="must be non-empty bytes"):
        LlmInputImage(data=b"", mime_type="image/png")


def test_llm_request_images_validation() -> None:
    img1 = LlmInputImage(data=b"png1", mime_type="image/png")
    img2 = LlmInputImage(data=b"jpeg2", mime_type="image/jpeg")

    req = LlmRequest(
        generation_id=uuid4(),
        user_text="describe",
        system_prompt="sys",
        images=(img1,),
    )
    assert len(req.images) == 1
    assert "png1" not in repr(req)

    with pytest.raises(ValueError, match="at most one image"):
        LlmRequest(
            generation_id=uuid4(),
            user_text="describe",
            system_prompt="sys",
            images=(img1, img2),
        )


def test_openai_build_messages_with_image() -> None:
    raw_bytes = b"fakepngbytes"
    img = LlmInputImage(data=raw_bytes, mime_type="image/png")
    req = LlmRequest(
        generation_id=uuid4(),
        user_text="what is this?",
        system_prompt="system instructions",
        history=(("user", "prior question"), ("assistant", "prior answer")),
        images=(img,),
    )
    messages = build_messages(req)
    assert messages[0] == {"role": "system", "content": "system instructions"}
    assert messages[1] == {"role": "user", "content": "prior question"}
    assert messages[2] == {"role": "assistant", "content": "prior answer"}

    current_user = messages[3]
    assert current_user["role"] == "user"
    content = cast(list[dict[str, object]], current_user["content"])
    assert isinstance(content, list)
    assert len(content) == 2
    assert content[0] == {"type": "text", "text": "what is this?"}

    image_part = content[1]
    assert image_part["type"] == "image_url"
    expected_b64 = base64.b64encode(raw_bytes).decode("ascii")
    assert image_part["image_url"] == {"url": f"data:image/png;base64,{expected_b64}"}


def test_openai_build_messages_without_image_preserves_plain_text() -> None:
    req = LlmRequest(
        generation_id=uuid4(),
        user_text="plain text turn",
        system_prompt="sys",
        history=(("user", "prior text"),),
        images=(),
    )
    messages = build_messages(req)
    assert messages[-1] == {"role": "user", "content": "plain text turn"}


@pytest.mark.asyncio
async def test_demo_llm_rejects_image() -> None:
    provider = DemoLlmProvider(chunk_delay_ms=0)
    img = LlmInputImage(data=b"jpegbytes", mime_type="image/jpeg")
    req = LlmRequest(
        generation_id=uuid4(),
        user_text="hello demo",
        system_prompt="sys",
        images=(img,),
    )
    with pytest.raises(LlmImageInputUnavailableError, match="does not support image inputs"):
        async for _ in provider.stream(req):
            pass


def test_error_retryability() -> None:
    assert not _is_retryable_error(LlmImageInputUnavailableError("unsupported"))
