"""Native Jev decisions cannot become arbitrary text, unknown sources or stale output."""

import asyncio
import json
from typing import cast

import httpx2
import pytest
from chatwaifu_protocol.base import JsonObject
from chatwaifu_runtime.agent.behavior import BehaviorDecisionService
from chatwaifu_runtime.providers.demo_llm import DemoLlmProvider
from chatwaifu_runtime.providers.typesafe import TypeSafeBehaviorProvider, TypeSafeDecisionError


def choice(selected: str, options: list[str]) -> dict[str, object]:
    return {
        "type": "choice",
        "choice": selected,
        "confidence": 1.0,
        "probabilities": {option: float(option == selected) for option in options},
    }


def state(*, work: bool = False) -> JsonObject:
    return {
        "messages": [{"source_ref": "100", "text": "Please create the requested report."}],
        "available_actions": ["wait", "respond", "clarify", "task", "defer"]
        if work
        else ["wait", "respond", "clarify"],
    }


def adapter(
    handler: httpx2.MockTransport | None = None,
    *,
    timeout: float = 1,
    key: str | None = "test-private-key",
) -> TypeSafeBehaviorProvider:
    return TypeSafeBehaviorProvider(
        base_url="https://api.typesafe.ai/v1",
        model="jev-latest",
        api_key=lambda: key,
        timeout_seconds=timeout,
        transport=handler,
    )


@pytest.mark.parametrize("action", ["wait", "respond", "clarify", "task", "defer"])
async def test_jev_maps_only_typed_actions_and_original_goals(action: str) -> None:
    seen: list[dict[str, object]] = []

    def handle(request: httpx2.Request) -> httpx2.Response:
        assert str(request.url) == "https://api.typesafe.ai/v1/systemone"
        assert request.headers["Authorization"] == "Bearer test-private-key"
        body = cast(dict[str, object], json.loads(request.content))
        seen.append(body)
        questions = cast(dict[str, dict[str, object]], body["questions"])
        options = list(cast(dict[str, object], questions["action"]["criteria"]))
        return httpx2.Response(
            200, json={"model": "jev-1.13.0", "answers": {"action": choice(action, options)}}
        )

    native = adapter(httpx2.MockTransport(handle))
    service = BehaviorDecisionService(DemoLlmProvider(), decision_factory=lambda: native)
    result = await service.decide("Ningning", state(work=True), frozenset({"100"}))
    assert result.action == action and result.source_refs == ["100"]
    assert seen[0]["model"] == "jev-latest"
    assert result.goal == (
        "Please create the requested report." if action in {"task", "defer"} else None
    )
    assert result.wake_after_seconds == (30 if action == "defer" else None)
    assert not result.memory_source_refs


async def test_multiple_sources_are_selected_from_exact_original_ids() -> None:
    def handle(request: httpx2.Request) -> httpx2.Response:
        body = cast(dict[str, object], json.loads(request.content))
        questions = cast(dict[str, dict[str, object]], body["questions"])
        assert questions["source"]["criteria"] == {"source_0": "100", "source_1": "200"}
        return httpx2.Response(
            200,
            json={
                "model": "jev-1.13.0",
                "answers": {
                    "action": choice("respond", ["wait", "respond", "clarify"]),
                    "source": choice("source_1", ["source_0", "source_1"]),
                },
            },
        )

    result = await adapter(httpx2.MockTransport(handle)).decide(
        "Ningning", state(), frozenset({"100", "200"})
    )
    assert result.source_refs == ["200"]


@pytest.mark.parametrize(
    "answer",
    [
        {
            "type": "choice",
            "choice": "delete_account",
            "confidence": 1.0,
            "probabilities": {"delete_account": 1.0},
        },
        {
            "type": "choice",
            "choice": "respond",
            "confidence": 1.1,
            "probabilities": {"respond": 1.0},
        },
        {
            "type": "choice",
            "choice": "respond",
            "confidence": 0.5,
            "probabilities": {"wait": 0.6, "respond": 0.2, "clarify": 0.2},
        },
        {
            "type": "choice",
            "choice": "respond",
            "confidence": 1.0,
            "probabilities": {"wait": 0.0, "respond": 0.4, "clarify": 0.0},
        },
        {
            "type": "choice",
            "choice": "respond",
            "confidence": float("nan"),
            "probabilities": {"wait": 0.0, "respond": 1.0, "clarify": 0.0},
        },
    ],
)
async def test_invalid_typed_answers_are_normalized_and_never_fall_back(
    answer: dict[str, object],
) -> None:
    def handle(_: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(200, json={"model": "jev-1.13.0", "answers": {"action": answer}})

    native = adapter(httpx2.MockTransport(handle))

    def forbidden():
        raise AssertionError("chat fallback was called")

    service = BehaviorDecisionService(
        DemoLlmProvider(), model_factory=forbidden, decision_factory=lambda: native
    )
    with pytest.raises(TypeSafeDecisionError, match="typesafe_invalid_response"):
        await service.decide("Ningning", state(), frozenset({"100"}))


@pytest.mark.parametrize("status", [401, 403, 422, 429, 529, 302])
async def test_http_errors_do_not_leak_provider_body_or_follow_redirects(status: int) -> None:
    attempts = 0

    def handle(_: httpx2.Request) -> httpx2.Response:
        nonlocal attempts
        attempts += 1
        return httpx2.Response(
            status,
            headers={"retry-after": "0", "location": "https://other.invalid/"},
            text="secret provider body test-private-key",
        )

    with pytest.raises(TypeSafeDecisionError, match=f"^typesafe_http_{status}$"):
        await adapter(httpx2.MockTransport(handle)).decide("Ningning", state(), frozenset({"100"}))
    assert attempts == (3 if status in {429, 529} else 1)


async def test_rate_limit_retry_can_recover_within_the_same_bounded_decision() -> None:
    attempts = 0

    def handle(_: httpx2.Request) -> httpx2.Response:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            return httpx2.Response(529, headers={"retry-after": "0"})
        return httpx2.Response(
            200,
            json={
                "model": "jev-1.13.0",
                "answers": {"action": choice("wait", ["wait", "respond", "clarify"])},
            },
        )

    result = await adapter(httpx2.MockTransport(handle)).decide(
        "Ningning", state(), frozenset({"100"})
    )
    assert result.action == "wait" and attempts == 2


async def test_timeout_and_cancellation_revoke_the_actual_native_request() -> None:
    started = asyncio.Event()
    cancelled = asyncio.Event()

    async def hanging(_: httpx2.Request) -> httpx2.Response:
        started.set()
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()
        raise AssertionError("unreachable")

    with pytest.raises(TypeSafeDecisionError, match="typesafe_timeout"):
        await adapter(httpx2.MockTransport(hanging), timeout=0.02).decide(
            "Ningning", state(), frozenset({"100"})
        )
    assert cancelled.is_set()
    started.clear()
    cancelled.clear()
    native = adapter(httpx2.MockTransport(hanging))
    service = BehaviorDecisionService(DemoLlmProvider(), decision_factory=lambda: native)
    task = asyncio.create_task(service.decide("Ningning", state(), frozenset({"100"})))
    await asyncio.wait_for(started.wait(), 1)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert cancelled.is_set()


async def test_missing_key_and_oversized_evidence_are_rejected_before_network() -> None:
    def forbidden(_: httpx2.Request) -> httpx2.Response:
        raise AssertionError("network should not be called")

    with pytest.raises(TypeSafeDecisionError, match="typesafe_key_not_configured"):
        await adapter(httpx2.MockTransport(forbidden), key=None).decide(
            "Ningning", state(), frozenset({"100"})
        )
    with pytest.raises(TypeSafeDecisionError, match="typesafe_evidence_too_large"):
        await adapter(httpx2.MockTransport(forbidden)).decide(
            "Ningning", {"text": "a" * 96_000}, frozenset({"100"})
        )
