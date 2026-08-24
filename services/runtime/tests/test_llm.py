"""Real LLM adapter context regression tests."""

from uuid import uuid4

from chatwaifu_runtime.providers.contracts import LlmRequest
from chatwaifu_runtime.providers.openai_compatible import build_messages


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
