"""Tests for StickerClassifier and StickerClassification."""

from __future__ import annotations

import asyncio
import io
from collections.abc import AsyncIterator
from uuid import uuid4

import pytest
from chatwaifu_runtime.providers.contracts import (
    LlmInputImage,
    LlmRequest,
    LlmResponseCompleted,
    LlmStreamEvent,
    LlmTextDelta,
    LlmToolCall,
    LlmToolCallRequested,
)
from chatwaifu_runtime.sticker_library.classifier import (
    StickerClassification,
    StickerClassifier,
)
from PIL import Image
from pydantic import ValidationError


class MockLlmProvider:
    """Configurable mock LlmProvider for unit tests."""

    def __init__(
        self,
        events: list[LlmStreamEvent] | None = None,
        *,
        supports_tool_calling: bool = True,
        hang_forever: bool = False,
        raise_error: BaseException | None = None,
    ) -> None:
        self.kind = "mock_llm"
        self.supports_tool_calling = supports_tool_calling
        self._events = events or []
        self._hang_forever = hang_forever
        self._raise_error = raise_error
        self.captured_requests: list[LlmRequest] = []

    async def stream(self, request: LlmRequest) -> AsyncIterator[LlmStreamEvent]:
        self.captured_requests.append(request)
        if self._hang_forever:
            await asyncio.Event().wait()
        if self._raise_error is not None:
            raise self._raise_error
        for event in self._events:
            yield event


def _make_dummy_image() -> LlmInputImage:
    output = io.BytesIO()
    Image.new("RGB", (10, 10), "white").save(output, format="PNG")
    return LlmInputImage(data=output.getvalue(), mime_type="image/png")


# --- Schema / Pydantic validation tests ---


def test_schema_valid_suitable_response() -> None:
    classification = StickerClassification(
        schema_version="1.0",
        suitable=True,
        confidence=0.95,
        label="happy cat",
        description="A cute happy cartoon cat smiling",
        expression="happy",
    )
    assert classification.suitable is True
    assert classification.confidence == 0.95
    assert classification.label == "happy cat"
    assert classification.description == "A cute happy cartoon cat smiling"
    assert classification.expression == "happy"
    assert classification.schema_version == "1.0"


def test_schema_suitable_requires_nonempty_stripped_label_and_description() -> None:
    with pytest.raises(ValidationError):
        StickerClassification(
            suitable=True,
            confidence=0.95,
            label="   ",
            description="Valid description",
            expression="happy",
        )

    with pytest.raises(ValidationError):
        StickerClassification(
            suitable=True,
            confidence=0.95,
            label="Valid label",
            description="",
            expression="happy",
        )


def test_schema_unsuitable_permits_empty_label_and_description() -> None:
    classification = StickerClassification(
        suitable=False,
        confidence=0.1,
        label="",
        description="",
        expression="neutral",
    )
    assert classification.suitable is False
    assert classification.label == ""
    assert classification.description == ""


def test_schema_confidence_bounds_and_finite() -> None:
    # Less than 0
    with pytest.raises(ValidationError):
        StickerClassification(
            suitable=False,
            confidence=-0.1,
            label="",
            description="",
            expression="neutral",
        )

    # Greater than 1
    with pytest.raises(ValidationError):
        StickerClassification(
            suitable=False,
            confidence=1.05,
            label="",
            description="",
            expression="neutral",
        )

    # NaN / Inf
    with pytest.raises(ValidationError):
        StickerClassification(
            suitable=False,
            confidence=float("nan"),
            label="",
            description="",
            expression="neutral",
        )

    with pytest.raises(ValidationError):
        StickerClassification(
            suitable=False,
            confidence=float("inf"),
            label="",
            description="",
            expression="neutral",
        )


def test_schema_forbid_extra_fields() -> None:
    with pytest.raises(ValidationError):
        StickerClassification.model_validate(
            {
                "suitable": True,
                "confidence": 0.95,
                "label": "happy",
                "description": "desc",
                "expression": "happy",
                "extra_field": "not allowed",
            }
        )


def test_schema_expression_enum_restriction() -> None:
    for expr in ["neutral", "happy", "sad", "angry", "surprised", "shy", "curious"]:
        obj = StickerClassification(
            suitable=False,
            confidence=0.5,
            label="",
            description="",
            expression=expr,  # pyright: ignore[reportArgumentType]
        )
        assert obj.expression == expr

    with pytest.raises(ValidationError):
        StickerClassification(
            suitable=False,
            confidence=0.5,
            label="",
            description="",
            expression="disgusted",  # pyright: ignore[reportArgumentType]
        )


# --- Classifier execution tests ---


@pytest.mark.asyncio
async def test_classify_exact_schema_valid_response() -> None:
    call = LlmToolCall(
        call_id="call_1",
        name="classify_sticker",
        arguments={
            "schema_version": "1.0",
            "suitable": True,
            "confidence": 0.95,
            "label": "cheering waifu",
            "description": "Cartoon waifu cheering with pom-poms",
            "expression": "happy",
        },
    )
    provider = MockLlmProvider(
        events=[
            LlmToolCallRequested(call),
            LlmResponseCompleted("tool_calls"),
        ]
    )
    classifier = StickerClassifier(provider)
    image = _make_dummy_image()
    gen_id = uuid4()

    res = await classifier.classify(image, generation_id=gen_id)

    assert res is not None
    assert res.suitable is True
    assert res.confidence == 0.95
    assert res.label == "cheering waifu"
    assert res.description == "Cartoon waifu cheering with pom-poms"
    assert res.expression == "happy"

    # Verify request preservation
    assert len(provider.captured_requests) == 1
    captured = provider.captured_requests[0]
    assert captured.generation_id == gen_id
    assert len(captured.images) == 1
    assert captured.images[0] is image
    assert len(captured.tools) == 1
    assert captured.tools[0].name == "classify_sticker"


@pytest.mark.asyncio
async def test_classify_text_only_prose_discarded() -> None:
    provider = MockLlmProvider(
        events=[
            LlmTextDelta("I think this is a great sticker with confidence 0.99 suitable=True"),
            LlmResponseCompleted("stop"),
        ]
    )
    classifier = StickerClassifier(provider)
    res = await classifier.classify(_make_dummy_image(), generation_id=uuid4())
    assert res is None


@pytest.mark.asyncio
async def test_classify_low_confidence_discarded() -> None:
    call = LlmToolCall(
        call_id="call_1",
        name="classify_sticker",
        arguments={
            "schema_version": "1.0",
            "suitable": True,
            "confidence": 0.85,
            "label": "maybe waifu",
            "description": "A cartoon illustration that might be a sticker",
            "expression": "curious",
        },
    )
    provider = MockLlmProvider(
        events=[
            LlmToolCallRequested(call),
            LlmResponseCompleted("tool_calls"),
        ]
    )
    classifier = StickerClassifier(provider)
    res = await classifier.classify(_make_dummy_image(), generation_id=uuid4())
    assert res is None


@pytest.mark.asyncio
async def test_classify_photo_unsuitable_returns_none() -> None:
    call = LlmToolCall(
        call_id="call_1",
        name="classify_sticker",
        arguments={
            "schema_version": "1.0",
            "suitable": False,
            "confidence": 0.99,
            "label": "",
            "description": "Photograph of a cat in a living room",
            "expression": "neutral",
        },
    )
    provider = MockLlmProvider(
        events=[
            LlmToolCallRequested(call),
            LlmResponseCompleted("tool_calls"),
        ]
    )
    classifier = StickerClassifier(provider)
    res = await classifier.classify(_make_dummy_image(), generation_id=uuid4())
    assert res is None


@pytest.mark.asyncio
async def test_classify_invalid_schema_and_out_of_range() -> None:
    # Malformed arguments string
    call_bad_json = LlmToolCall(
        call_id="call_1",
        name="classify_sticker",
        arguments="not-valid-json",  # pyright: ignore[reportArgumentType]
    )
    provider = MockLlmProvider(events=[LlmToolCallRequested(call_bad_json)])
    classifier = StickerClassifier(provider)
    assert await classifier.classify(_make_dummy_image(), generation_id=uuid4()) is None

    # Out of range confidence
    call_bad_conf = LlmToolCall(
        call_id="call_2",
        name="classify_sticker",
        arguments={
            "schema_version": "1.0",
            "suitable": True,
            "confidence": 1.5,
            "label": "label",
            "description": "desc",
            "expression": "happy",
        },
    )
    provider2 = MockLlmProvider(events=[LlmToolCallRequested(call_bad_conf)])
    assert (
        await StickerClassifier(provider2).classify(_make_dummy_image(), generation_id=uuid4())
        is None
    )


@pytest.mark.asyncio
async def test_classify_unknown_tool_and_multiple_calls_rejected() -> None:
    # Unknown tool name
    unknown_call = LlmToolCall(
        call_id="call_1",
        name="unknown_tool",
        arguments={"suitable": True, "confidence": 0.95},
    )
    provider_unknown = MockLlmProvider(events=[LlmToolCallRequested(unknown_call)])
    assert (
        await StickerClassifier(provider_unknown).classify(
            _make_dummy_image(), generation_id=uuid4()
        )
        is None
    )

    # Multiple calls
    valid_call = LlmToolCall(
        call_id="call_1",
        name="classify_sticker",
        arguments={
            "schema_version": "1.0",
            "suitable": True,
            "confidence": 0.95,
            "label": "valid",
            "description": "valid",
            "expression": "happy",
        },
    )
    provider_multiple = MockLlmProvider(
        events=[
            LlmToolCallRequested(valid_call),
            LlmToolCallRequested(valid_call),
        ]
    )
    assert (
        await StickerClassifier(provider_multiple).classify(
            _make_dummy_image(), generation_id=uuid4()
        )
        is None
    )


@pytest.mark.asyncio
async def test_classify_unsupported_tool_calling_returns_none() -> None:
    provider = MockLlmProvider(supports_tool_calling=False)
    classifier = StickerClassifier(provider)
    res = await classifier.classify(_make_dummy_image(), generation_id=uuid4())
    assert res is None
    # No request should even be streamed
    assert len(provider.captured_requests) == 0


@pytest.mark.asyncio
async def test_classify_provider_error_returns_none() -> None:
    provider = MockLlmProvider(raise_error=RuntimeError("connection dropped"))
    classifier = StickerClassifier(provider)
    res = await classifier.classify(_make_dummy_image(), generation_id=uuid4())
    assert res is None


@pytest.mark.asyncio
async def test_classify_cancelled_propagates() -> None:
    provider = MockLlmProvider(raise_error=asyncio.CancelledError())
    classifier = StickerClassifier(provider)
    with pytest.raises(asyncio.CancelledError):
        await classifier.classify(_make_dummy_image(), generation_id=uuid4())


@pytest.mark.asyncio
async def test_classify_bounded_timeout_and_hanging_provider(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import chatwaifu_runtime.sticker_library.classifier as classifier_module

    monkeypatch.setattr(classifier_module, "CLASSIFICATION_TIMEOUT_SECONDS", 0.05)

    provider = MockLlmProvider(hang_forever=True)
    classifier = StickerClassifier(provider)
    res = await classifier.classify(_make_dummy_image(), generation_id=uuid4())
    assert res is None


@pytest.mark.asyncio
async def test_no_side_effects_or_tools_executed() -> None:
    call = LlmToolCall(
        call_id="call_1",
        name="classify_sticker",
        arguments={
            "schema_version": "1.0",
            "suitable": True,
            "confidence": 0.95,
            "label": "sticker",
            "description": "desc",
            "expression": "happy",
        },
    )
    provider = MockLlmProvider(
        events=[
            LlmToolCallRequested(call),
            LlmResponseCompleted("tool_calls"),
        ]
    )
    classifier = StickerClassifier(provider)
    res = await classifier.classify(_make_dummy_image(), generation_id=uuid4())
    assert res is not None
    # Verify no tool exchange was appended to request or executed
    req = provider.captured_requests[0]
    assert req.tool_exchanges == ()
