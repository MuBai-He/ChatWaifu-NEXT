"""Real LLM adapter context regression tests."""

from uuid import uuid4

import pytest
from chatwaifu_runtime.providers.contracts import LlmRequest
from chatwaifu_runtime.providers.openai_compatible import (
    build_messages,
    classify_stream_delivery,
    openai_compatible_endpoint,
)


def test_openai_messages_keep_memory_history_and_current_turn_in_order() -> None:
    request = LlmRequest(
        generation_id=uuid4(),
        system_prompt="你是绫地宁宁。",
        user_text="那今天呢?",
        context=(("system", "记忆: 用户喜欢红茶"),),
        history=(("user", "昨天聊了什么?"), ("assistant", "聊了放学后的事。")),
    )

    assert build_messages(request) == [
        {"role": "system", "content": "你是绫地宁宁。"},
        {"role": "system", "content": "记忆: 用户喜欢红茶"},
        {"role": "user", "content": "昨天聊了什么?"},
        {"role": "assistant", "content": "聊了放学后的事。"},
        {"role": "user", "content": "那今天呢?"},
    ]


@pytest.mark.parametrize(
    ("base_url", "operation", "expected"),
    [
        (
            "http://127.0.0.1:1234",
            "embeddings",
            "http://127.0.0.1:1234/v1/embeddings",
        ),
        (
            "http://127.0.0.1:1234/",
            "/chat/completions",
            "http://127.0.0.1:1234/v1/chat/completions",
        ),
        (
            "http://127.0.0.1:1234/v1",
            "embeddings",
            "http://127.0.0.1:1234/v1/embeddings",
        ),
        (
            "https://example.test/openai/v1/",
            "embeddings",
            "https://example.test/openai/v1/embeddings",
        ),
    ],
)
def test_openai_compatible_endpoint_accepts_host_or_explicit_api_base(
    base_url: str, operation: str, expected: str
) -> None:
    assert openai_compatible_endpoint(base_url, operation) == expected


@pytest.mark.parametrize(
    ("metrics", "expected"),
    [
        (
            {
                "chunk_count": 90,
                "character_count": 110,
                "max_chunk_characters": 3,
                "delivery_span_ms": 1_400,
            },
            "token_stream",
        ),
        (
            {
                "chunk_count": 4,
                "character_count": 99,
                "max_chunk_characters": 38,
                "delivery_span_ms": 224,
            },
            "sentence_batched",
        ),
        (
            {
                "chunk_count": 3,
                "character_count": 97,
                "max_chunk_characters": 35,
                "delivery_span_ms": 7,
            },
            "burst_buffered",
        ),
    ],
)
def test_stream_delivery_classification_uses_only_timing_and_chunk_sizes(
    metrics: dict[str, int], expected: str
) -> None:
    assert classify_stream_delivery(**metrics) == expected
