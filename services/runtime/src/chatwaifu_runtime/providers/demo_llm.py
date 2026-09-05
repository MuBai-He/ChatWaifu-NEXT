"""Clearly labelled deterministic LLM used when no model server is configured."""

import asyncio
from collections.abc import AsyncIterator

from chatwaifu_runtime.providers.contracts import (
    LlmImageInputUnavailableError,
    LlmRequest,
    LlmResponseCompleted,
    LlmStreamEvent,
    LlmTextDelta,
)


class DemoLlmProvider:
    kind = "demo"
    supports_tool_calling = False

    def __init__(self, chunk_delay_ms: int = 25) -> None:
        self._delay_seconds = chunk_delay_ms / 1000

    async def stream(self, request: LlmRequest) -> AsyncIterator[LlmStreamEvent]:
        if request.images:
            raise LlmImageInputUnavailableError("Demo LLM provider does not support image inputs")
        if request.trigger == "proactive":
            response = "那个……忙了这么久，也别忘了稍微休息一下哦。想聊点什么的话，我就在这里。"
            for chunk in _chunks(response):
                if self._delay_seconds:
                    await asyncio.sleep(self._delay_seconds)
                yield LlmTextDelta(chunk)
            yield LlmResponseCompleted("stop")
            return
        memory_note = _memory_note(request.recalled_memory_texts)
        response = (
            f"你好，我是{request.character_name}，ChatWaifu NEXT 的本地演示角色。"
            f"我收到你说的: “{request.user_text.strip()}”。"
            "现在使用的是可离线运行的 Demo 模型; 配置 OpenAI 兼容接口后，"
            f"我就能换成真正的大语言模型继续聊天。{memory_note}"
        )
        for chunk in _chunks(response):
            if self._delay_seconds:
                await asyncio.sleep(self._delay_seconds)
            yield LlmTextDelta(chunk)
        yield LlmResponseCompleted("stop")


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


def _memory_note(recalled_memory_texts: tuple[str, ...]) -> str:
    """Render recalled facts without exposing Runtime-owned prompt markup."""
    excerpts = tuple(text.strip() for text in recalled_memory_texts if text.strip())
    return " 我还记得\uff1a" + "\uff1b".join(excerpts) if excerpts else ""
