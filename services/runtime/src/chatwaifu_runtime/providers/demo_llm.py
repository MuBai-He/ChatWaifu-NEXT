"""Clearly labelled deterministic LLM used when no model server is configured."""

import asyncio
from collections.abc import AsyncIterator

from chatwaifu_runtime.providers.contracts import LlmRequest


class DemoLlmProvider:
    kind = "demo"

    def __init__(self, chunk_delay_ms: int = 25) -> None:
        self._delay_seconds = chunk_delay_ms / 1000

    async def stream(self, request: LlmRequest) -> AsyncIterator[str]:
        response = (
            "你好，我是 ChatWaifu NEXT 的本地演示角色。"
            f"我收到你说的: “{request.user_text.strip()}”。"
            "现在使用的是可离线运行的 Demo 模型; 配置 OpenAI 兼容接口后，"
            "我就能换成真正的大语言模型继续聊天。"
        )
        for chunk in _chunks(response):
            if self._delay_seconds:
                await asyncio.sleep(self._delay_seconds)
            yield chunk


def _chunks(text: str) -> tuple[str, ...]:
    endings = "，。\uff01\uff1f\uff1b\uff1a"
    chunks: list[str] = []
    pending = ""
    for character in text:
        pending += character
        if len(pending) >= 8 or character in endings:
            chunks.append(pending)
            pending = ""
    if pending:
        chunks.append(pending)
    return tuple(chunks) or (text,)
