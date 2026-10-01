"""Offline reference counting covers the adapter's full textual request."""

import json
from dataclasses import replace
from pathlib import Path
from uuid import uuid4

import pytest
from chatwaifu_runtime.providers.contracts import (
    LlmInputImage,
    LlmRequest,
    LlmToolCall,
    LlmToolDefinition,
    LlmToolExchange,
    LlmToolResult,
)
from chatwaifu_runtime.providers.openai_compatible import (
    build_chat_completions_payload,
    build_messages,
)


def _request() -> LlmRequest:
    return LlmRequest(
        uuid4(),
        "核对全部条件",
        "角色、安全与真实来源",
        context=(("system", "source ledger"),),
        history=(("user", "earlier facts"),),
        tools=(LlmToolDefinition("read", "读取资料", {"type": "object"}),),
        tool_exchanges=(
            LlmToolExchange(
                "核实前的说明",
                (LlmToolCall("one", "read", {"url": "https://example.org"}),),
                (LlmToolResult("one", "read", {"text": "原文条件", "ok": True}),),
            ),
        ),
    )


def test_reference_matches_full_adapter_projection() -> None:
    from chatwaifu_runtime.providers.input_estimation import (
        count_reference_tokens,
        estimate_reference_input_tokens,
    )

    request = _request()
    messages = build_messages(request)
    payload = build_chat_completions_payload("ignored-model", request, messages)
    projection = {key: payload[key] for key in ("messages", "tools", "tool_choice")}
    expected = count_reference_tokens(
        json.dumps(projection, ensure_ascii=False, separators=(",", ":"))
    )
    expected += 16 * (len(messages) + len(request.tools))
    assert estimate_reference_input_tokens(request) == expected


@pytest.mark.parametrize(
    "field",
    ["system", "current", "context", "history", "schema", "arguments", "result", "preamble"],
)
def test_every_transmitted_text_field_affects_reference(field: str) -> None:
    from chatwaifu_runtime.providers.input_estimation import estimate_reference_input_tokens

    request = _request()
    stress = "这是另一段必须核对的具体资料。" * 200
    exchange = request.tool_exchanges[0]
    if field == "system":
        changed = replace(request, system_prompt=stress)
    elif field == "current":
        changed = replace(request, user_text=stress)
    elif field == "context":
        changed = replace(request, context=(("system", stress),))
    elif field == "history":
        changed = replace(request, history=(("user", stress),))
    elif field == "schema":
        changed = replace(
            request, tools=(replace(request.tools[0], input_schema={"description": stress}),)
        )
    elif field == "arguments":
        changed = replace(
            request,
            tool_exchanges=(
                replace(exchange, calls=(replace(exchange.calls[0], arguments={"url": stress}),)),
            ),
        )
    elif field == "result":
        changed = replace(
            request,
            tool_exchanges=(
                replace(
                    exchange, results=(replace(exchange.results[0], content={"text": stress}),)
                ),
            ),
        )
    else:
        changed = replace(request, tool_exchanges=(replace(exchange, assistant_text=stress),))
    assert (
        estimate_reference_input_tokens(changed) > estimate_reference_input_tokens(request) + 1000
    )


def test_reference_is_offline_and_treats_special_token_text_as_ordinary(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import socket

    import tiktoken
    from chatwaifu_runtime.providers import input_estimation

    def forbidden(*args: object, **kwargs: object) -> None:
        pytest.fail("reference counting attempted network or tokenizer registry access")

    input_estimation._reference_encoder.cache_clear()  # pyright: ignore[reportPrivateUsage]
    monkeypatch.setattr(socket.socket, "connect", forbidden)
    monkeypatch.setattr(tiktoken, "get_encoding", forbidden)
    assert input_estimation.count_reference_tokens("hello world") == 2
    assert input_estimation.count_reference_tokens("你好") == 2
    assert input_estimation.count_reference_tokens("<|endoftext|>") > 1


def test_reference_fails_on_corrupt_bundled_vocabulary(monkeypatch: pytest.MonkeyPatch) -> None:
    from chatwaifu_runtime.providers import input_estimation

    def corrupt_read(self: Path) -> bytes:
        return b"corrupt vocabulary"

    monkeypatch.setattr(Path, "read_bytes", corrupt_read)
    with pytest.raises(RuntimeError, match="checksum"):
        input_estimation._load_reference_encoder()  # pyright: ignore[reportPrivateUsage]


def test_image_bytes_are_not_serialized_or_tokenized(monkeypatch: pytest.MonkeyPatch) -> None:
    from chatwaifu_runtime.providers import input_estimation

    request = _request()
    base = input_estimation.estimate_reference_input_tokens(request)
    build = input_estimation.build_messages

    def no_image_serialization(request: LlmRequest) -> list[dict[str, object]]:
        assert not request.images
        return build(request)

    monkeypatch.setattr(input_estimation, "build_messages", no_image_serialization)
    for data in (b"small", b"large" * 100_000):
        image_request = replace(request, images=(LlmInputImage(data, "image/png"),))
        assert input_estimation.estimate_reference_input_tokens(image_request) == base + 1024


def test_fit_reports_reference_provenance_and_recomputes_complete_input() -> None:
    from chatwaifu_runtime.agent.input_budget import fit_input_budget
    from chatwaifu_runtime.providers.contracts import LlmInputBudget
    from chatwaifu_runtime.providers.input_estimation import estimate_reference_input_tokens

    request = replace(
        _request(),
        history=(("assistant", "旧解释与未核实说明。" * 1000), ("user", "保留最后问题")),
        input_budget=LlmInputBudget(1000),
    )
    fitted = fit_input_budget(request)
    report = fitted.input_budget_report
    assert report is not None
    assert report.version == "1.1" and report.estimator == "cl100k_chat_json_v1"
    assert report.estimated_input_tokens == estimate_reference_input_tokens(fitted) <= 1000
    assert report.omitted_history_indices == (0,)
    assert fitted.tool_exchanges == request.tool_exchanges
    assert fitted.history[-1] == request.history[-1]
    assert fit_input_budget(fitted) == fitted
