"""Minimal streaming adapter for local OpenAI-compatible chat-completion servers."""

import hashlib
import json
import logging
import math
from collections.abc import AsyncIterator
from dataclasses import dataclass
from time import monotonic
from typing import Literal, NoReturn, cast
from urllib.parse import urlsplit, urlunsplit

import httpx2
from chatwaifu_protocol.base import JsonObject

from chatwaifu_runtime.providers.contracts import (
    LlmFinishReason,
    LlmRequest,
    LlmResponseCompleted,
    LlmStreamEvent,
    LlmTextDelta,
    LlmToolCall,
    LlmToolCallingUnavailableError,
    LlmToolCallRequested,
)

logger = logging.getLogger(__name__)
MAX_TOOL_CALLS_PER_RESPONSE = 8
MAX_TOOL_ARGUMENT_CHARACTERS = 65_536
MAX_TOOL_CALL_ID_CHARACTERS = 256
MAX_TOOL_NAME_CHARACTERS = 64


class OpenAiCompatibleLlmProvider:
    kind = "openai_compatible"
    supports_tool_calling = True

    def __init__(
        self,
        *,
        base_url: str,
        model: str,
        api_key: str | None,
        timeout_seconds: float,
        transport: httpx2.AsyncBaseTransport | None = None,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._model = model
        self._api_key = api_key
        self._timeout = timeout_seconds
        self._transport = transport

    async def stream(self, request: LlmRequest) -> AsyncIterator[LlmStreamEvent]:
        try:
            async for event in self._stream_once(request):
                yield event
        except _ToolCallingUnsupported as error:
            # OpenAI-compatible describes an endpoint shape, not a guarantee that
            # the selected model implements function calling. Never retry an
            # external-action turn as plain text: that could look like a verified
            # lookup even though no Runtime Skill ran.
            logger.warning(
                "OpenAI-compatible model rejected required function tools generation=%s model=%s",
                request.generation_id,
                self._model,
            )
            raise LlmToolCallingUnavailableError(
                "OpenAI-compatible model does not support the required tool round"
            ) from error

    async def _stream_once(self, request: LlmRequest) -> AsyncIterator[LlmStreamEvent]:
        messages = build_messages(request)
        headers = {"Content-Type": "application/json"}
        if self._api_key:
            headers["Authorization"] = f"Bearer {self._api_key}"
        started_at = monotonic()
        first_chunk_at: float | None = None
        last_chunk_at: float | None = None
        chunk_count = 0
        character_count = 0
        max_chunk_characters = 0
        completed = False
        tool_parts: dict[int, _ToolCallParts] = {}
        try:
            async with httpx2.AsyncClient(
                timeout=self._timeout, transport=self._transport
            ) as client:
                async with client.stream(
                    "POST",
                    openai_compatible_endpoint(self._base_url, "chat/completions"),
                    headers=headers,
                    json=build_chat_completions_payload(self._model, request, messages),
                ) as response:
                    if response.status_code >= 400:
                        body = await response.aread()
                        if request.tools and _rejects_tool_calling(response.status_code, body):
                            raise _ToolCallingUnsupported
                    response.raise_for_status()
                    async for line in response.aiter_lines():
                        if not line.startswith("data:"):
                            continue
                        data = line.removeprefix("data:").strip()
                        if data == "[DONE]":
                            if tool_parts:
                                for call in _finalize_tool_calls(tool_parts, request):
                                    yield LlmToolCallRequested(call)
                                yield LlmResponseCompleted("tool_calls")
                            else:
                                yield LlmResponseCompleted("stop")
                            completed = True
                            return
                        try:
                            payload = _strict_json_loads(data)
                        except (json.JSONDecodeError, ValueError) as error:
                            raise RuntimeError(
                                "OpenAI-compatible LLM returned invalid stream JSON"
                            ) from error
                        if not isinstance(payload, dict):
                            raise RuntimeError("OpenAI-compatible LLM returned invalid stream data")
                        payload_object = cast(dict[str, object], payload)
                        choices_value = payload_object.get("choices", [])
                        if not isinstance(choices_value, list) or not choices_value:
                            continue
                        choices = cast(list[object], choices_value)
                        choice_value = choices[0]
                        if not isinstance(choice_value, dict):
                            raise RuntimeError("OpenAI-compatible LLM returned invalid choice data")
                        choice = cast(dict[str, object], choice_value)
                        delta_value = choice.get("delta", {})
                        if not isinstance(delta_value, dict):
                            raise RuntimeError("OpenAI-compatible LLM returned invalid delta data")
                        delta = cast(dict[str, object], delta_value)
                        content = delta.get("content")
                        if isinstance(content, str) and content:
                            received_at = monotonic()
                            first_chunk_at = first_chunk_at or received_at
                            last_chunk_at = received_at
                            chunk_count += 1
                            character_count += len(content)
                            max_chunk_characters = max(max_chunk_characters, len(content))
                            yield LlmTextDelta(content)
                        _accumulate_tool_calls(tool_parts, delta)
                        finish_value = choice.get("finish_reason")
                        if finish_value is not None:
                            finish_reason = _finish_reason(finish_value)
                            if finish_reason == "tool_calls":
                                for call in _finalize_tool_calls(tool_parts, request):
                                    yield LlmToolCallRequested(call)
                            yield LlmResponseCompleted(finish_reason)
                            completed = True
                            return
                    if tool_parts:
                        for call in _finalize_tool_calls(tool_parts, request):
                            yield LlmToolCallRequested(call)
                        yield LlmResponseCompleted("tool_calls")
                    else:
                        yield LlmResponseCompleted("other")
                    completed = True
        finally:
            if first_chunk_at is not None and last_chunk_at is not None:
                ttft_ms = round((first_chunk_at - started_at) * 1000)
                delivery_span_ms = round((last_chunk_at - first_chunk_at) * 1000)
                pattern = classify_stream_delivery(
                    chunk_count=chunk_count,
                    character_count=character_count,
                    max_chunk_characters=max_chunk_characters,
                    delivery_span_ms=delivery_span_ms,
                )
                log = logger.warning if pattern == "burst_buffered" else logger.info
                log(
                    "OpenAI-compatible LLM stream generation=%s model=%s completed=%s "
                    "chunks=%d characters=%d ttft_ms=%d delivery_span_ms=%d "
                    "max_chunk_characters=%d delivery_pattern=%s",
                    request.generation_id,
                    self._model,
                    completed,
                    chunk_count,
                    character_count,
                    ttft_ms,
                    delivery_span_ms,
                    max_chunk_characters,
                    pattern,
                )


def classify_stream_delivery(
    *,
    chunk_count: int,
    character_count: int,
    max_chunk_characters: int,
    delivery_span_ms: int,
) -> Literal["empty", "token_stream", "sentence_batched", "burst_buffered"]:
    """Classify delivery timing without inspecting or logging generated text."""

    if chunk_count <= 0 or character_count <= 0:
        return "empty"
    if character_count >= 40 and delivery_span_ms <= 80:
        return "burst_buffered"
    if max_chunk_characters >= 16:
        return "sentence_batched"
    return "token_stream"


def openai_compatible_endpoint(base_url: str, operation: str) -> str:
    """Build a standard endpoint while accepting either a host or an explicit API base path."""

    parts = urlsplit(base_url.rstrip("/"))
    base_path = parts.path.rstrip("/") or "/v1"
    endpoint_path = f"{base_path}/{operation.strip('/')}"
    return urlunsplit((parts.scheme, parts.netloc, endpoint_path, parts.query, parts.fragment))


def build_messages(request: LlmRequest) -> list[dict[str, object]]:
    messages: list[dict[str, object]] = [{"role": "system", "content": request.system_prompt}]
    messages.extend({"role": role, "content": text} for role, text in request.context)
    messages.extend({"role": role, "content": text} for role, text in request.history)
    messages.append({"role": "user", "content": request.user_text})
    for exchange in request.tool_exchanges:
        messages.append(
            {
                "role": "assistant",
                "content": exchange.assistant_text or None,
                "tool_calls": [
                    {
                        "id": call.call_id,
                        "type": "function",
                        "function": {
                            "name": call.name,
                            "arguments": json.dumps(
                                call.arguments,
                                ensure_ascii=False,
                                separators=(",", ":"),
                            ),
                        },
                    }
                    for call in exchange.calls
                ],
            }
        )
        messages.extend(
            {
                "role": "tool",
                "tool_call_id": result.call_id,
                "content": json.dumps(
                    result.content,
                    ensure_ascii=False,
                    separators=(",", ":"),
                ),
            }
            for result in exchange.results
        )
    return messages


def build_chat_completions_payload(
    model: str, request: LlmRequest, messages: list[dict[str, object]]
) -> dict[str, object]:
    payload: dict[str, object] = {"model": model, "messages": messages, "stream": True}
    if request.tools:
        payload["tools"] = [
            {
                "type": "function",
                "function": {
                    "name": tool.name,
                    "description": tool.description,
                    "parameters": tool.input_schema,
                },
            }
            for tool in request.tools
        ]
        payload["tool_choice"] = "required"
    return payload


@dataclass(slots=True)
class _ToolCallParts:
    call_id: str = ""
    name: str = ""
    arguments: str = ""


class _ToolCallingUnsupported(RuntimeError):
    pass


def _accumulate_tool_calls(parts: dict[int, _ToolCallParts], delta: dict[str, object]) -> None:
    raw_calls = delta.get("tool_calls")
    if isinstance(raw_calls, list):
        for fallback_index, raw_call_value in enumerate(cast(list[object], raw_calls)):
            if not isinstance(raw_call_value, dict):
                raise RuntimeError("OpenAI-compatible LLM returned an invalid tool call delta")
            raw_call = cast(dict[str, object], raw_call_value)
            raw_index = raw_call.get("index", fallback_index)
            if (
                not isinstance(raw_index, int)
                or raw_index < 0
                or raw_index >= MAX_TOOL_CALLS_PER_RESPONSE
            ):
                raise RuntimeError("OpenAI-compatible LLM returned an invalid tool call index")
            item = parts.setdefault(raw_index, _ToolCallParts())
            call_id = raw_call.get("id")
            if isinstance(call_id, str):
                item.call_id = _append_bounded_tool_fragment(
                    item.call_id,
                    call_id,
                    max_characters=MAX_TOOL_CALL_ID_CHARACTERS,
                    field="tool call id",
                )
            function = raw_call.get("function")
            if isinstance(function, dict):
                function_object = cast(dict[str, object], function)
                name = function_object.get("name")
                arguments = function_object.get("arguments")
                if isinstance(name, str):
                    item.name = _append_bounded_tool_fragment(
                        item.name,
                        name,
                        max_characters=MAX_TOOL_NAME_CHARACTERS,
                        field="tool name",
                    )
                if isinstance(arguments, str):
                    item.arguments += arguments
                    if len(item.arguments) > MAX_TOOL_ARGUMENT_CHARACTERS:
                        raise RuntimeError(
                            "OpenAI-compatible LLM tool arguments exceeded the size limit"
                        )

    # A few older compatible servers still emit the pre-tools function_call
    # shape. Normalize it into index zero without leaking that wire format.
    legacy = delta.get("function_call")
    if isinstance(legacy, dict):
        legacy_object = cast(dict[str, object], legacy)
        item = parts.setdefault(0, _ToolCallParts())
        name = legacy_object.get("name")
        arguments = legacy_object.get("arguments")
        if isinstance(name, str):
            item.name = _append_bounded_tool_fragment(
                item.name,
                name,
                max_characters=MAX_TOOL_NAME_CHARACTERS,
                field="tool name",
            )
        if isinstance(arguments, str):
            item.arguments += arguments
            if len(item.arguments) > MAX_TOOL_ARGUMENT_CHARACTERS:
                raise RuntimeError("OpenAI-compatible LLM tool arguments exceeded the size limit")


def _finalize_tool_calls(
    parts: dict[int, _ToolCallParts], request: LlmRequest
) -> tuple[LlmToolCall, ...]:
    calls: list[LlmToolCall] = []
    call_ids: set[str] = set()
    allowed_names = {tool.name for tool in request.tools}
    for index, item in sorted(parts.items()):
        name = item.name.strip()
        if not name or name not in allowed_names:
            raise RuntimeError("OpenAI-compatible LLM requested an unknown tool")
        try:
            arguments_value = _strict_json_loads(item.arguments or "{}")
        except (json.JSONDecodeError, ValueError) as error:
            raise RuntimeError("OpenAI-compatible LLM returned malformed tool arguments") from error
        if not isinstance(arguments_value, dict):
            raise RuntimeError("OpenAI-compatible LLM tool arguments must be a JSON object")
        arguments = cast(JsonObject, arguments_value)
        call_id = item.call_id.strip()
        if not call_id:
            digest = hashlib.sha256(
                f"{request.generation_id}:{index}:{name}:{item.arguments}".encode()
            ).hexdigest()[:16]
            call_id = f"call_{digest}"
        if call_id in call_ids:
            raise RuntimeError("OpenAI-compatible LLM returned duplicate tool call ids")
        call_ids.add(call_id)
        calls.append(LlmToolCall(call_id=call_id, name=name, arguments=arguments))
    if not calls:
        raise RuntimeError("OpenAI-compatible LLM ended with no complete tool calls")
    return tuple(calls)


def _finish_reason(value: object) -> LlmFinishReason:
    if value in {"stop", "tool_calls", "length", "content_filter"}:
        return cast(LlmFinishReason, value)
    if value == "function_call":
        return "tool_calls"
    return "other"


def _rejects_tool_calling(status_code: int, body: bytes) -> bool:
    if status_code not in {400, 404, 422}:
        return False
    detail = body[:8192].decode("utf-8", errors="ignore").casefold()
    return any(
        marker in detail
        for marker in ("tool_choice", "tool call", "tool_call", "function call", "tools")
    )


def _append_bounded_tool_fragment(
    current: str,
    fragment: str,
    *,
    max_characters: int,
    field: str,
) -> str:
    if len(fragment) > max_characters - len(current):
        raise RuntimeError(f"OpenAI-compatible LLM {field} exceeded the size limit")
    return current + fragment


def _strict_json_loads(value: str) -> object:
    decoded = json.loads(value, parse_constant=_reject_nonstandard_json_constant)
    _reject_nonfinite_json_values(decoded)
    return decoded


def _reject_nonstandard_json_constant(value: str) -> NoReturn:
    raise ValueError(f"non-standard JSON number is not allowed: {value}")


def _reject_nonfinite_json_values(value: object) -> None:
    pending = [value]
    while pending:
        item = pending.pop()
        if isinstance(item, float) and not math.isfinite(item):
            raise ValueError("non-finite JSON number is not allowed")
        if isinstance(item, dict):
            pending.extend(cast(dict[object, object], item).values())
        elif isinstance(item, list):
            pending.extend(cast(list[object], item))
