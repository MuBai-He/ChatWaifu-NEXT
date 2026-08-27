"""Minimal streaming adapter for local OpenAI-compatible chat-completion servers."""

import json
import logging
from collections.abc import AsyncIterator
from time import monotonic
from typing import Literal
from urllib.parse import urlsplit, urlunsplit

import httpx2

from chatwaifu_runtime.providers.contracts import LlmRequest

logger = logging.getLogger(__name__)


class OpenAiCompatibleLlmProvider:
    kind = "openai_compatible"

    def __init__(
        self, *, base_url: str, model: str, api_key: str | None, timeout_seconds: float
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._model = model
        self._api_key = api_key
        self._timeout = timeout_seconds

    async def stream(self, request: LlmRequest) -> AsyncIterator[str]:
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
        try:
            async with httpx2.AsyncClient(timeout=self._timeout) as client:
                async with client.stream(
                    "POST",
                    openai_compatible_endpoint(self._base_url, "chat/completions"),
                    headers=headers,
                    json={"model": self._model, "messages": messages, "stream": True},
                ) as response:
                    response.raise_for_status()
                    async for line in response.aiter_lines():
                        if not line.startswith("data:"):
                            continue
                        data = line.removeprefix("data:").strip()
                        if data == "[DONE]":
                            completed = True
                            return
                        payload = json.loads(data)
                        choices = payload.get("choices", [])
                        if not choices:
                            continue
                        content = choices[0].get("delta", {}).get("content")
                        if isinstance(content, str) and content:
                            received_at = monotonic()
                            first_chunk_at = first_chunk_at or received_at
                            last_chunk_at = received_at
                            chunk_count += 1
                            character_count += len(content)
                            max_chunk_characters = max(max_chunk_characters, len(content))
                            yield content
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


def build_messages(request: LlmRequest) -> list[dict[str, str]]:
    messages = [{"role": "system", "content": request.system_prompt}]
    messages.extend({"role": role, "content": text} for role, text in request.context)
    messages.extend({"role": role, "content": text} for role, text in request.history)
    messages.append({"role": "user", "content": request.user_text})
    return messages
