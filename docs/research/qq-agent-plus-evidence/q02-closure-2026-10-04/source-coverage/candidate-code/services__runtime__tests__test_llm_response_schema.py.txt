"""Structured answer requests stay immutable, counted and explicit on failure."""

import json
from dataclasses import replace
from typing import cast
from uuid import uuid4

import httpx2
import pytest
from chatwaifu_protocol.base import JsonObject
from chatwaifu_runtime.agent.input_budget import InputBudgetExceeded, fit_input_budget
from chatwaifu_runtime.providers.contracts import (
    LlmInputBudget,
    LlmRequest,
    LlmResponseSchema,
    LlmResponseSchemaUnavailableError,
)
from chatwaifu_runtime.providers.input_estimation import estimate_reference_input_tokens
from chatwaifu_runtime.providers.openai_compatible import (
    OpenAiCompatibleLlmProvider,
    build_chat_completions_payload,
    build_messages,
)


def test_schema_snapshot_matches_wire_and_cannot_be_mutated_by_caller():
    properties: JsonObject = {"reply": {"type": "string"}}
    original: JsonObject = {"type": "object", "properties": properties}
    schema = LlmResponseSchema.from_schema("source_frame", original)
    properties["reply"] = {"type": "number"}
    request = LlmRequest(uuid4(), "question", "policy", response_schema=schema)
    payload = build_chat_completions_payload("model", request, build_messages(request))
    response_format = cast(JsonObject, payload["response_format"])
    assert response_format == {
        "type": "json_schema",
        "json_schema": {
            "name": "source_frame",
            "strict": True,
            "schema": {"type": "object", "properties": {"reply": {"type": "string"}}},
        },
    }
    definition = cast(JsonObject, response_format["json_schema"])
    cast(JsonObject, definition["schema"])["type"] = "array"
    fresh = build_chat_completions_payload("model", request, build_messages(request))
    assert fresh["response_format"] == schema.response_format()
    assert (
        schema.schema_json
        == LlmResponseSchema.from_schema(
            "source_frame", {"type": "object", "properties": {"reply": {"type": "string"}}}
        ).schema_json
    )


def test_whole_request_budget_includes_the_actual_response_schema():
    base = LlmRequest(uuid4(), "question", "policy")
    schema = LlmResponseSchema.from_schema("answer", {"description": "the declared format " * 200})
    request = replace(base, response_schema=schema)
    base_estimate = estimate_reference_input_tokens(base)
    assert estimate_reference_input_tokens(request) > base_estimate + 300
    with pytest.raises(InputBudgetExceeded):
        fit_input_budget(replace(request, input_budget=LlmInputBudget(base_estimate)))


@pytest.mark.parametrize("text", ['{"type":"object","type":"array"}', '{"x":NaN}', "[]", "{"])
def test_noncanonical_or_invalid_schema_is_rejected_before_dispatch(text: str):
    with pytest.raises(ValueError):
        LlmResponseSchema("answer", text)


async def test_unsupported_format_is_one_explicit_failure_without_prose_retry():
    attempts: list[JsonObject] = []

    def handler(request: httpx2.Request):
        attempts.append(json.loads(request.content))
        return httpx2.Response(
            400,
            json={
                "error": {"message": "json_schema response_format is not supported; PRIVATE-DATA"}
            },
        )

    provider = OpenAiCompatibleLlmProvider(
        base_url="https://provider.example/v1",
        model="model",
        api_key=None,
        timeout_seconds=5,
        transport=httpx2.MockTransport(handler),
    )
    request = LlmRequest(
        uuid4(),
        "question",
        "policy",
        response_schema=LlmResponseSchema.from_schema("answer", {"type": "object"}),
    )
    with pytest.raises(LlmResponseSchemaUnavailableError) as failure:
        _ = [event async for event in provider.stream(request)]
    assert len(attempts) == 1 and "response_format" in attempts[0]
    assert failure.value.code == "response_schema_unavailable"
    assert "PRIVATE-DATA" not in str(failure.value)


async def test_unrelated_authentication_error_is_not_called_unsupported_format():
    provider = OpenAiCompatibleLlmProvider(
        base_url="https://provider.example/v1",
        model="model",
        api_key=None,
        timeout_seconds=5,
        transport=httpx2.MockTransport(
            lambda _: httpx2.Response(401, json={"error": "not authorized"})
        ),
    )
    request = LlmRequest(
        uuid4(),
        "question",
        "policy",
        response_schema=LlmResponseSchema.from_schema("answer", {"type": "object"}),
    )
    with pytest.raises(httpx2.HTTPStatusError):
        _ = [event async for event in provider.stream(request)]
