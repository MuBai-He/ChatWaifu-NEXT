"""SSE iterator ownership must finish before a request or consumer exits."""

import asyncio
import gzip
import json
from collections.abc import AsyncGenerator
from uuid import uuid4

import httpx2
import pytest
from chatwaifu_runtime.providers.contracts import (
    LlmRequest,
    LlmResponseCompleted,
    LlmTextDelta,
    LlmToolCallRequested,
    LlmToolDefinition,
)
from chatwaifu_runtime.providers.openai_compatible import OpenAiCompatibleLlmProvider


class _TrackedBody(httpx2.AsyncByteStream):
    def __init__(self, chunks: list[bytes], *, hold_open: bool = True) -> None:
        self.chunks = chunks
        self.hold_open = hold_open
        self.waiting = asyncio.Event()
        self.iterator_closed = False
        self.body_closed = False

    async def __aiter__(self) -> AsyncGenerator[bytes]:
        try:
            for chunk in self.chunks:
                yield chunk
            if self.hold_open:
                self.waiting.set()
                await asyncio.Event().wait()
        finally:
            self.iterator_closed = True

    async def aclose(self) -> None:
        self.body_closed = True


def _provider(
    body: _TrackedBody, *, usage: bool = False, compressed: bool = False
) -> OpenAiCompatibleLlmProvider:
    async def handler(_request: httpx2.Request) -> httpx2.Response:
        headers = {"content-type": "text/event-stream"}
        if compressed:
            headers["content-encoding"] = "gzip"
        return httpx2.Response(200, headers=headers, stream=body)

    return OpenAiCompatibleLlmProvider(
        base_url="https://example.test/v1",
        model="test-model",
        api_key=None,
        timeout_seconds=5,
        transport=httpx2.MockTransport(handler),
        request_usage=usage,
    )


def _request() -> LlmRequest:
    return LlmRequest(generation_id=uuid4(), user_text="你好", system_prompt="test")


@pytest.mark.parametrize("usage", [False, True])
async def test_terminal_sse_closes_byte_iterator_before_return(usage: bool) -> None:
    body = _TrackedBody(
        [
            b'data: {"choices":[{"delta":{"content":"ok"},"finish_reason":"stop"}]}\n\n',
            b'data: {"choices":[],"usage":{"prompt_tokens":2,'
            b'"completion_tokens":1,"total_tokens":3}}\n\n',
            b"data: [DONE]\n\n",
        ]
    )
    events = [event async for event in _provider(body, usage=usage).stream(_request())]
    assert events[0] == LlmTextDelta("ok")
    assert isinstance(events[-1], LlmResponseCompleted)
    assert (events[-1].usage is not None) == usage
    assert body.iterator_closed
    assert body.body_closed
    assert not body.waiting.is_set()


@pytest.mark.parametrize("final_newline", [b"", b"\n\n"])
async def test_http_eof_without_done_keeps_final_event(final_newline: bytes) -> None:
    body = _TrackedBody(
        [b'data: {"choices":[{"delta":{"content":"ok"},"finish_reason":"stop"}]}' + final_newline],
        hold_open=False,
    )
    events = [event async for event in _provider(body, usage=True).stream(_request())]
    assert events == [LlmTextDelta("ok"), LlmResponseCompleted("stop")]
    assert body.iterator_closed
    assert body.body_closed
    assert not body.waiting.is_set()


async def test_consumer_close_closes_nested_streams_immediately() -> None:
    body = _TrackedBody([b'data: {"choices":[{"delta":{"content":"ok"}}]}\n\n'])
    stream = _provider(body).stream(_request())
    assert await anext(stream) == LlmTextDelta("ok")
    await stream.aclose()
    assert body.iterator_closed
    assert body.body_closed


async def test_cancel_during_read_closes_stream_and_propagates() -> None:
    body = _TrackedBody([b'data: {"choices":[{"delta":{"content":"ok"}}]}\n\n'])
    stream = _provider(body).stream(_request())
    assert await anext(stream) == LlmTextDelta("ok")
    task = asyncio.create_task(anext(stream))
    await asyncio.wait_for(body.waiting.wait(), timeout=1)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert body.iterator_closed
    assert body.body_closed


async def test_invalid_json_closes_stream_without_hiding_error() -> None:
    body = _TrackedBody([b"data: invalid-json\n\n"])
    with pytest.raises(RuntimeError, match="invalid stream JSON"):
        _ = [event async for event in _provider(body).stream(_request())]
    assert body.iterator_closed
    assert body.body_closed


@pytest.mark.parametrize("compressed", [False, True])
async def test_fragmented_utf8_and_sse_line_endings(compressed: bool) -> None:
    wire = (
        '\ufeffdata: {"choices":[{"delta":{"content":"你好"}}]}\r\n'
        '\r\ndata: {"choices":[{"delta":{},"finish_reason":"stop"}]}\r'
        "\rdata: [DONE]\n\n"
    ).encode()
    if compressed:
        wire = gzip.compress(wire)
    body = _TrackedBody([wire[i : i + 1] for i in range(len(wire))])
    events = [event async for event in _provider(body, compressed=compressed).stream(_request())]
    assert events == [LlmTextDelta("你好"), LlmResponseCompleted("stop")]
    assert body.iterator_closed
    assert body.body_closed


@pytest.mark.parametrize("usage", [False, True])
@pytest.mark.parametrize("done", [False, True])
async def test_response_model_labels_survive_all_terminal_paths(usage: bool, done: bool) -> None:
    body = _TrackedBody(
        [
            b'data: {"model":"upstream-a","choices":[{"delta":{"content":"ok"}}]}\n\n',
            b'data: {"model":"upstream-b","choices":[{"delta":{},"finish_reason":"stop"}]}\n\n',
            *([b"data: [DONE]\n\n"] if done else []),
        ],
        hold_open=False,
    )
    events = [event async for event in _provider(body, usage=usage).stream(_request())]
    identity = getattr(events[-1], "identity", None)
    assert identity is not None
    assert identity.version == "1.0"
    assert identity.reported_model_ids == ("upstream-a", "upstream-b")
    assert identity.incomplete is False
    assert events[0] == LlmTextDelta("ok")
    assert body.iterator_closed and body.body_closed


async def test_response_model_metadata_is_bounded_and_invalid_values_are_not_labels() -> None:
    labels = ["same", "same", None, 4, "", "bad\nlabel", "x" * 257]
    labels.extend(f"model-{i}" for i in range(10))
    chunks = [
        ("data: " + json.dumps({"model": label, "choices": []}) + "\n\n").encode()
        for label in labels
    ]
    chunks += [
        b'data: {"choices":[{"delta":{"content":"ok"},"finish_reason":"stop"}]}\n\n',
        b"data: [DONE]\n\n",
    ]
    events = [
        event async for event in _provider(_TrackedBody(chunks), usage=True).stream(_request())
    ]
    identity = getattr(events[-1], "identity", None)
    assert identity is not None
    assert identity.reported_model_ids == ("same", *(f"model-{i}" for i in range(7)))
    assert identity.incomplete is True
    assert events[0] == LlmTextDelta("ok")


async def test_model_on_usage_tail_is_preserved_without_filling_a_missing_model() -> None:
    body = _TrackedBody(
        [
            b'data: {"choices":[{"delta":{"content":"ok"},"finish_reason":"stop"}]}\n\n',
            b'data: {"model":"reported-only-on-tail","choices":[],"usage":{"total_tokens":3}}\n\n',
            b"data: [DONE]\n\n",
        ]
    )
    events = [event async for event in _provider(body, usage=True).stream(_request())]
    identity = getattr(events[-1], "identity", None)
    assert identity is not None
    assert identity.reported_model_ids == ("reported-only-on-tail",)
    missing = _TrackedBody([b'data: {"choices":[{"delta":{},"finish_reason":"stop"}]}\n\n'])
    events = [event async for event in _provider(missing).stream(_request())]
    assert getattr(events[-1], "identity", None) is None


async def test_response_identity_does_not_leak_across_requests_on_one_provider() -> None:
    replies = iter(
        [
            b'data: {"model":"first-response","choices":[{"delta":{},"finish_reason":"stop"}]}\n\n',
            b'data: {"choices":[{"delta":{},"finish_reason":"stop"}]}\n\n',
        ]
    )

    async def handler(_request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(200, content=next(replies))

    provider = OpenAiCompatibleLlmProvider(
        base_url="https://example.test/v1",
        model="requested-high",
        api_key=None,
        timeout_seconds=5,
        transport=httpx2.MockTransport(handler),
    )
    first = [event async for event in provider.stream(_request())]
    second = [event async for event in provider.stream(_request())]
    assert isinstance(first[-1], LlmResponseCompleted) and first[-1].identity is not None
    assert first[-1].identity.reported_model_ids == ("first-response",)
    assert isinstance(second[-1], LlmResponseCompleted) and second[-1].identity is None


@pytest.mark.parametrize("usage", [False, True])
async def test_native_tool_completion_retains_response_identity(usage: bool) -> None:
    body = _TrackedBody(
        [
            b'data: {"model":"tool-alias","choices":[{"delta":{"tool_calls":'
            b'[{"index":0,"id":"c1","type":"function","function":{"name":"lookup",'
            b'"arguments":"{}"}}]},"finish_reason":"tool_calls"}]}\n\n',
            b"data: [DONE]\n\n",
        ]
    )
    request = LlmRequest(
        generation_id=uuid4(),
        user_text="Check source",
        system_prompt="test",
        tools=(LlmToolDefinition("lookup", "Read only", {"type": "object"}),),
    )
    events = [event async for event in _provider(body, usage=usage).stream(request)]
    assert isinstance(events[0], LlmToolCallRequested) and events[0].call.name == "lookup"
    assert isinstance(events[-1], LlmResponseCompleted)
    assert events[-1].finish_reason == "tool_calls"
    assert events[-1].identity is not None
    assert events[-1].identity.reported_model_ids == ("tool-alias",)
    assert body.iterator_closed and body.body_closed
