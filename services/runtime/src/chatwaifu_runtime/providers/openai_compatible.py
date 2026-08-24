"""Minimal streaming adapter for local OpenAI-compatible chat-completion servers."""

import json
from collections.abc import AsyncIterator

import httpx2

from chatwaifu_runtime.providers.contracts import LlmRequest


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
        async with httpx2.AsyncClient(timeout=self._timeout) as client:
            async with client.stream(
                "POST",
                f"{self._base_url}/chat/completions",
                headers=headers,
                json={"model": self._model, "messages": messages, "stream": True},
            ) as response:
                response.raise_for_status()
                async for line in response.aiter_lines():
                    if not line.startswith("data:"):
                        continue
                    data = line.removeprefix("data:").strip()
                    if data == "[DONE]":
                        return
                    payload = json.loads(data)
                    choices = payload.get("choices", [])
                    if not choices:
                        continue
                    content = choices[0].get("delta", {}).get("content")
                    if isinstance(content, str) and content:
                        yield content


def build_messages(request: LlmRequest) -> list[dict[str, str]]:
    messages = [{"role": "system", "content": request.system_prompt}]
    messages.extend({"role": role, "content": text} for role, text in request.context)
    messages.extend({"role": role, "content": text} for role, text in request.history)
    messages.append({"role": "user", "content": request.user_text})
    return messages
