"""Real LLM adapter context and structured tool-call regression tests."""

import json
from typing import cast
from uuid import uuid4

import httpx2
import pytest
from chatwaifu_runtime.providers.contracts import (
    LlmRequest,
    LlmResponseCompleted,
    LlmTextDelta,
    LlmToolCall,
    LlmToolCallingUnavailableError,
    LlmToolCallRequested,
    LlmToolDefinition,
    LlmToolExchange,
    LlmToolResult,
)
from chatwaifu_runtime.providers.demo_llm import DemoLlmProvider
from chatwaifu_runtime.providers.openai_compatible import (
    OpenAiCompatibleLlmProvider,
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


def _sse_response(*payloads: dict[str, object]) -> httpx2.Response:
    body = "".join(
        f"data: {json.dumps(payload, separators=(',', ':'))}\n\n" for payload in payloads
    )
    return httpx2.Response(
        200,
        headers={"content-type": "text/event-stream"},
        content=(body + "data: [DONE]\n\n").encode(),
    )


async def _events(provider: OpenAiCompatibleLlmProvider, request: LlmRequest) -> list[object]:
    return [event async for event in provider.stream(request)]


@pytest.mark.asyncio
async def test_demo_llm_renders_memory_without_runtime_prompt_markup() -> None:
    provider = DemoLlmProvider(chunk_delay_ms=0)
    request = LlmRequest(
        generation_id=uuid4(),
        system_prompt="internal system prompt",
        character_name="绫地宁宁",
        user_text="你还记得吗？",
        context=(
            (
                "system",
                "记忆: 仅使用以下经过策略、隐私与来源检查的内容:\n"
                "- [relevant] 我的 Windows CUDA 验收编号是 NENE-WIN-CUDA-0901。",
            ),
            (
                "system",
                "[UNTRUSTED MEMORY SOURCE]\n"
                "memory_id=MEMORY-ID-MUST-STAY-HIDDEN;"
                "provider_id=PROVIDER-MUST-STAY-HIDDEN;"
                "conversation_key=CONVERSATION-MUST-STAY-HIDDEN",
            ),
        ),
        recalled_memory_texts=("我的 Windows CUDA 验收编号是 NENE-WIN-CUDA-0901。",),
    )

    events = [event async for event in provider.stream(request)]
    response = "".join(event.text for event in events if isinstance(event, LlmTextDelta))

    assert "我还记得\uff1a我的 Windows CUDA 验收编号是 NENE-WIN-CUDA-0901。" in response
    assert "记忆:" not in response
    assert "仅使用以下经过策略" not in response
    assert "[relevant]" not in response
    assert "UNTRUSTED MEMORY SOURCE" not in response
    assert "MEMORY-ID-MUST-STAY-HIDDEN" not in response
    assert "PROVIDER-MUST-STAY-HIDDEN" not in response
    assert "CONVERSATION-MUST-STAY-HIDDEN" not in response


@pytest.mark.asyncio
async def test_openai_stream_assembles_fragmented_parallel_tool_calls() -> None:
    observed: list[dict[str, object]] = []

    async def handler(request: httpx2.Request) -> httpx2.Response:
        observed.append(json.loads(request.content))
        return _sse_response(
            {
                "choices": [
                    {
                        "delta": {
                            "tool_calls": [
                                {
                                    "index": 0,
                                    "id": "call_a",
                                    "function": {"name": "runtime_", "arguments": '{"'},
                                },
                                {
                                    "index": 1,
                                    "id": "call_b",
                                    "function": {"name": "echo", "arguments": '{"text":'},
                                },
                            ]
                        },
                        "finish_reason": None,
                    }
                ]
            },
            {
                "choices": [
                    {
                        "delta": {
                            "tool_calls": [
                                {
                                    "index": 1,
                                    "function": {"arguments": '"你好"}'},
                                },
                                {
                                    "index": 0,
                                    "function": {"name": "status", "arguments": 'scope":"all"}'},
                                },
                            ]
                        },
                        "finish_reason": "tool_calls",
                    }
                ]
            },
        )

    provider = OpenAiCompatibleLlmProvider(
        base_url="https://example.test/v1",
        model="test-model",
        api_key=None,
        timeout_seconds=5,
        transport=httpx2.MockTransport(handler),
    )
    request = LlmRequest(
        generation_id=uuid4(),
        user_text="查状态并回声",
        system_prompt="test",
        tools=(
            LlmToolDefinition(
                name="runtime_status", description="status", input_schema={"type": "object"}
            ),
            LlmToolDefinition(name="echo", description="echo", input_schema={"type": "object"}),
        ),
    )

    events = await _events(provider, request)

    assert len(events) == 3
    assert isinstance(events[0], LlmToolCallRequested)
    assert isinstance(events[1], LlmToolCallRequested)
    assert events[2] == LlmResponseCompleted("tool_calls")
    calls = [event.call for event in events if isinstance(event, LlmToolCallRequested)]
    assert [(call.call_id, call.name, call.arguments) for call in calls] == [
        ("call_a", "runtime_status", {"scope": "all"}),
        ("call_b", "echo", {"text": "你好"}),
    ]
    assert observed[0]["tool_choice"] == "required"
    tools = cast(list[dict[str, object]], observed[0]["tools"])
    assert [cast(dict[str, object], tool["function"])["name"] for tool in tools] == [
        "runtime_status",
        "echo",
    ]


@pytest.mark.asyncio
async def test_openai_tool_result_transcript_is_sent_back_to_model() -> None:
    observed: list[dict[str, object]] = []

    async def handler(request: httpx2.Request) -> httpx2.Response:
        observed.append(json.loads(request.content))
        return _sse_response(
            {"choices": [{"delta": {"content": "完成了。"}, "finish_reason": "stop"}]}
        )

    provider = OpenAiCompatibleLlmProvider(
        base_url="https://example.test/v1",
        model="test-model",
        api_key=None,
        timeout_seconds=5,
        transport=httpx2.MockTransport(handler),
    )
    request = LlmRequest(
        generation_id=uuid4(),
        user_text="查状态",
        system_prompt="test",
        tool_exchanges=(
            LlmToolExchange(
                assistant_text="",
                calls=(LlmToolCall(call_id="call_1", name="runtime_status", arguments={}),),
                results=(
                    LlmToolResult(
                        call_id="call_1",
                        name="runtime_status",
                        content={"ok": True, "data": {"runtime": "ready"}},
                    ),
                ),
            ),
        ),
    )

    events = await _events(provider, request)

    assert events == [LlmTextDelta("完成了。"), LlmResponseCompleted("stop")]
    messages = cast(list[dict[str, object]], observed[0]["messages"])
    assert [message["role"] for message in messages[-3:]] == ["user", "assistant", "tool"]
    assert messages[-1]["tool_call_id"] == "call_1"
    tool_content = cast(dict[str, object], json.loads(cast(str, messages[-1]["content"])))
    tool_data = cast(dict[str, object], tool_content["data"])
    assert tool_data["runtime"] == "ready"


@pytest.mark.asyncio
async def test_openai_rejects_malformed_tool_arguments_without_executing_a_call() -> None:
    async def handler(_request: httpx2.Request) -> httpx2.Response:
        return _sse_response(
            {
                "choices": [
                    {
                        "delta": {
                            "tool_calls": [
                                {
                                    "index": 0,
                                    "id": "call_bad",
                                    "function": {
                                        "name": "runtime_status",
                                        "arguments": "{not-json",
                                    },
                                }
                            ]
                        },
                        "finish_reason": "tool_calls",
                    }
                ]
            }
        )

    provider = OpenAiCompatibleLlmProvider(
        base_url="https://example.test/v1",
        model="test-model",
        api_key=None,
        timeout_seconds=5,
        transport=httpx2.MockTransport(handler),
    )
    request = LlmRequest(
        generation_id=uuid4(),
        user_text="查状态",
        system_prompt="test",
        tools=(
            LlmToolDefinition(
                name="runtime_status", description="status", input_schema={"type": "object"}
            ),
        ),
    )

    with pytest.raises(RuntimeError, match="malformed tool arguments"):
        await _events(provider, request)


@pytest.mark.asyncio
@pytest.mark.parametrize("constant", ["NaN", "Infinity", "-Infinity", "1e999", "-1e999"])
async def test_openai_rejects_non_finite_tool_arguments(constant: str) -> None:
    async def handler(_request: httpx2.Request) -> httpx2.Response:
        return _sse_response(
            {
                "choices": [
                    {
                        "delta": {
                            "tool_calls": [
                                {
                                    "index": 0,
                                    "id": "call_non_finite",
                                    "function": {
                                        "name": "runtime_status",
                                        "arguments": f'{{"value":{constant}}}',
                                    },
                                }
                            ]
                        },
                        "finish_reason": "tool_calls",
                    }
                ]
            }
        )

    provider = OpenAiCompatibleLlmProvider(
        base_url="https://example.test/v1",
        model="test-model",
        api_key=None,
        timeout_seconds=5,
        transport=httpx2.MockTransport(handler),
    )
    request = LlmRequest(
        generation_id=uuid4(),
        user_text="查状态",
        system_prompt="test",
        tools=(
            LlmToolDefinition(
                name="runtime_status", description="status", input_schema={"type": "object"}
            ),
        ),
    )

    with pytest.raises(RuntimeError, match="malformed tool arguments"):
        await _events(provider, request)


@pytest.mark.asyncio
async def test_openai_rejects_non_standard_numbers_in_stream_envelope() -> None:
    async def handler(_request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(
            200,
            headers={"content-type": "text/event-stream"},
            content=(
                b'data: {"choices":[{"delta":{},"finish_reason":null}],"usage":NaN}\n\n'
                b"data: [DONE]\n\n"
            ),
        )

    provider = OpenAiCompatibleLlmProvider(
        base_url="https://example.test/v1",
        model="test-model",
        api_key=None,
        timeout_seconds=5,
        transport=httpx2.MockTransport(handler),
    )
    request = LlmRequest(generation_id=uuid4(), user_text="你好", system_prompt="test")

    with pytest.raises(RuntimeError, match="invalid stream JSON"):
        await _events(provider, request)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("call_id", "tool_name", "expected"),
    [
        ("x" * 257, "runtime_status", "tool call id exceeded"),
        ("call_too_long", "x" * 65, "tool name exceeded"),
    ],
)
async def test_openai_bounds_streamed_tool_identity_fragments(
    call_id: str, tool_name: str, expected: str
) -> None:
    async def handler(_request: httpx2.Request) -> httpx2.Response:
        return _sse_response(
            {
                "choices": [
                    {
                        "delta": {
                            "tool_calls": [
                                {
                                    "index": 0,
                                    "id": call_id,
                                    "function": {"name": tool_name, "arguments": "{}"},
                                }
                            ]
                        },
                        "finish_reason": "tool_calls",
                    }
                ]
            }
        )

    provider = OpenAiCompatibleLlmProvider(
        base_url="https://example.test/v1",
        model="test-model",
        api_key=None,
        timeout_seconds=5,
        transport=httpx2.MockTransport(handler),
    )
    request = LlmRequest(
        generation_id=uuid4(),
        user_text="查状态",
        system_prompt="test",
        tools=(
            LlmToolDefinition(
                name=tool_name, description="status", input_schema={"type": "object"}
            ),
        ),
    )

    with pytest.raises(RuntimeError, match=expected):
        await _events(provider, request)


@pytest.mark.asyncio
async def test_openai_explicit_tool_unsupported_error_fails_without_text_fallback() -> None:
    requests: list[dict[str, object]] = []

    async def handler(request: httpx2.Request) -> httpx2.Response:
        payload = json.loads(request.content)
        requests.append(payload)
        return httpx2.Response(400, json={"error": "tools are not supported"})

    provider = OpenAiCompatibleLlmProvider(
        base_url="https://example.test/v1",
        model="text-only-model",
        api_key=None,
        timeout_seconds=5,
        transport=httpx2.MockTransport(handler),
    )
    request = LlmRequest(
        generation_id=uuid4(),
        user_text="你好",
        system_prompt="test",
        tools=(
            LlmToolDefinition(
                name="runtime_status", description="status", input_schema={"type": "object"}
            ),
        ),
    )

    with pytest.raises(LlmToolCallingUnavailableError):
        await _events(provider, request)
    assert len(requests) == 1
    assert "tools" in requests[0]
    assert requests[0]["tool_choice"] == "required"
