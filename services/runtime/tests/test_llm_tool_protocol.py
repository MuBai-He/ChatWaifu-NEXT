"""Malformed native function batches remain atomic and diagnosable."""

import json
from uuid import uuid4

import httpx2
import pytest
from chatwaifu_runtime.providers.contracts import LlmRequest, LlmToolDefinition
from chatwaifu_runtime.providers.openai_compatible import OpenAiCompatibleLlmProvider


@pytest.mark.parametrize(
    ("name", "arguments", "expected_code"),
    [
        ("unadvertised", "{}", "unknown_tool"),
        ("known", "{", "malformed_arguments"),
        ("known", "[]", "arguments_not_object"),
    ],
)
async def test_native_protocol_errors_never_publish_partially_valid_batches(
    name: str, arguments: str, expected_code: str
) -> None:
    calls = [
        {"index": 0, "id": "valid", "function": {"name": "known", "arguments": "{}"}},
        {"index": 1, "id": "invalid", "function": {"name": name, "arguments": arguments}},
    ]
    chunk = {"choices": [{"delta": {"tool_calls": calls}, "finish_reason": "tool_calls"}]}
    transport = httpx2.MockTransport(
        lambda _: httpx2.Response(
            200, content=f"data: {json.dumps(chunk)}\n\ndata: [DONE]\n\n".encode()
        )
    )
    provider = OpenAiCompatibleLlmProvider(
        base_url="https://provider.example/v1",
        model="test",
        api_key=None,
        timeout_seconds=10,
        transport=transport,
    )
    request = LlmRequest(uuid4(), "Question", "System", tools=(LlmToolDefinition("known", "", {}),))
    published: list[object] = []
    with pytest.raises(RuntimeError) as failure:
        async for event in provider.stream(request):
            published.append(event)
    assert getattr(failure.value, "code", None) == expected_code
    assert not published
    if name == "unadvertised":
        assert name not in str(failure.value)
