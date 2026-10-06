"""Provider-neutral streaming and synthesis contracts."""

from __future__ import annotations

import json
import re
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal, Protocol
from uuid import UUID

from chatwaifu_protocol.base import JsonObject, JsonValue

MAX_LLM_IMAGE_BYTES = 5 * 1024 * 1024


class LlmToolCallingUnavailableError(RuntimeError):
    """The selected provider cannot honor a requested structured tool round."""


type LlmToolCallProtocolCode = Literal[
    "unknown_tool",
    "malformed_arguments",
    "arguments_not_object",
    "duplicate_ids",
    "incomplete_calls",
]


class LlmToolCallProtocolError(RuntimeError):
    """A rejected native call batch; only a fixed, nonsecret code crosses adapters."""

    def __init__(self, code: LlmToolCallProtocolCode) -> None:
        messages = {
            "unknown_tool": "OpenAI-compatible LLM requested an unknown tool",
            "malformed_arguments": "OpenAI-compatible LLM returned malformed tool arguments",
            "arguments_not_object": "OpenAI-compatible LLM tool arguments must be a JSON object",
            "duplicate_ids": "OpenAI-compatible LLM returned duplicate tool call ids",
            "incomplete_calls": "OpenAI-compatible LLM ended with no complete tool calls",
        }
        super().__init__(messages[code])
        self.code = code


class LlmImageInputUnavailableError(RuntimeError):
    """The selected provider cannot honor an image input."""


class LlmEmptyResponseError(RuntimeError):
    """A completed response has no answer; recorded tool results remain valid."""

    def __init__(self, *, has_tool_results: bool = False) -> None:
        super().__init__("LLM completed without visible answer text")
        self.has_tool_results = has_tool_results


class LlmResponseSchemaUnavailableError(RuntimeError):
    """A provider cannot honor a declared format; no silent prose downgrade."""

    code = "response_schema_unavailable"

    def __init__(self) -> None:
        super().__init__("The selected LLM cannot honor the declared response schema")


def _schema_pairs(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("invalid response schema")
        result[key] = value
    return result


def _schema_constant(_: str) -> object:
    raise ValueError("invalid response schema")


@dataclass(frozen=True, slots=True)
class LlmResponseSchema:
    """Immutable provider-neutral JSON Schema snapshot, with no SDK objects."""

    name: str
    schema_json: str = field(repr=False)
    version: Literal["1.0"] = "1.0"

    def __post_init__(self) -> None:
        if self.version != "1.0" or not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{0,63}", self.name):
            raise ValueError("invalid response schema identity")
        try:
            if len(self.schema_json.encode()) > 16_384:
                raise ValueError("response schema exceeds bound")
            value = json.loads(
                self.schema_json, object_pairs_hook=_schema_pairs, parse_constant=_schema_constant
            )
        except (json.JSONDecodeError, UnicodeError, RecursionError) as error:
            raise ValueError("invalid response schema") from error
        if not isinstance(value, dict):
            raise ValueError("response schema must be an object")

    @classmethod
    def from_schema(cls, name: str, schema: JsonObject) -> LlmResponseSchema:
        return cls(
            name,
            json.dumps(
                schema, ensure_ascii=False, separators=(",", ":"), sort_keys=True, allow_nan=False
            ),
        )

    def response_format(self) -> JsonObject:
        # Return a fresh object each time; callers cannot mutate the snapshot.
        return {
            "type": "json_schema",
            "json_schema": {
                "name": self.name,
                "strict": True,
                "schema": json.loads(self.schema_json),
            },
        }


@dataclass(frozen=True, slots=True)
class LlmInputImage:
    """One provider-neutral raster image input attached to a turn."""

    data: bytes = field(repr=False)
    mime_type: Literal["image/png", "image/jpeg"]

    def __post_init__(self) -> None:
        if self.mime_type not in ("image/png", "image/jpeg"):
            raise ValueError(f"unsupported image mime type: {self.mime_type}")
        if type(self.data) is not bytes or not self.data:
            raise ValueError("image data must be non-empty bytes")
        if len(self.data) > MAX_LLM_IMAGE_BYTES:
            raise ValueError(
                f"image size {len(self.data)} exceeds maximum allowed of "
                f"{MAX_LLM_IMAGE_BYTES} bytes"
            )


@dataclass(frozen=True, slots=True)
class LlmToolDefinition:
    """One provider-neutral function exposed to a reasoning backend."""

    name: str
    description: str
    input_schema: JsonObject


@dataclass(frozen=True, slots=True)
class LlmToolCall:
    """A complete, validated tool request assembled by a provider adapter."""

    call_id: str
    name: str
    arguments: JsonObject


@dataclass(frozen=True, slots=True)
class LlmToolResult:
    """Bounded Runtime Skill result returned to the reasoning backend."""

    call_id: str
    name: str
    content: JsonValue
    is_error: bool = False


@dataclass(frozen=True, slots=True)
class LlmToolExchange:
    """One assistant tool-request message and all of its tool results."""

    assistant_text: str
    calls: tuple[LlmToolCall, ...]
    results: tuple[LlmToolResult, ...]


@dataclass(frozen=True, slots=True)
class LlmTextDelta:
    text: str


@dataclass(frozen=True, slots=True)
class LlmToolCallRequested:
    call: LlmToolCall


type LlmFinishReason = Literal["stop", "tool_calls", "length", "content_filter", "other"]


@dataclass(frozen=True, slots=True)
class LlmUsage:
    """Token usage metrics reported by an LLM provider."""

    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    total_tokens: int | None = None
    reasoning_tokens: int | None = None


@dataclass(frozen=True, slots=True)
class LlmResponseIdentity:
    """Unverified provider labels, never proof of weights or reasoning effort."""

    reported_model_ids: tuple[str, ...]
    incomplete: bool = False
    version: Literal["1.0"] = "1.0"


@dataclass(frozen=True, slots=True)
class LlmResponseCompleted:
    finish_reason: LlmFinishReason
    usage: LlmUsage | None = None
    identity: LlmResponseIdentity | None = None


type LlmStreamEvent = LlmTextDelta | LlmToolCallRequested | LlmResponseCompleted


@dataclass(frozen=True, slots=True)
class LlmInputBudget:
    """Frozen estimated input allowance, not a provider tokenizer guarantee."""

    estimated_token_limit: int
    version: Literal["1.0"] = "1.0"

    def __post_init__(self) -> None:
        if type(self.estimated_token_limit) is not int or self.estimated_token_limit < 1:
            raise ValueError("estimated input token limit must be a positive integer")
        if self.version != "1.0":
            raise ValueError("unsupported input budget version")


@dataclass(frozen=True, slots=True)
class LlmInputBudgetReport:
    """Nonsecret projection metadata; actual usage remains provider-reported."""

    estimated_token_limit: int
    estimated_original_tokens: int
    estimated_input_tokens: int
    omitted_history_indices: tuple[int, ...] = ()
    omitted_tool_preamble_indices: tuple[int, ...] = ()
    estimated_image_tokens: int = 0
    estimator: Literal["cl100k_chat_json_v1"] = "cl100k_chat_json_v1"
    version: Literal["1.1"] = "1.1"


@dataclass(frozen=True, slots=True)
class LlmRequest:
    generation_id: UUID
    user_text: str
    system_prompt: str
    character_name: str = "ChatWaifu"
    context: tuple[tuple[str, str], ...] = ()
    history: tuple[tuple[str, str], ...] = ()
    # Trusted same-session user turn for routing an immediate read-only follow-up.
    # This is never taken from cross-surface history or model output.
    routing_previous_user_text: str | None = None
    recalled_memory_texts: tuple[str, ...] = ()
    trigger: Literal["user", "proactive"] = "user"
    tools: tuple[LlmToolDefinition, ...] = ()
    tool_choice: Literal["required", "auto"] = "required"
    tool_exchanges: tuple[LlmToolExchange, ...] = ()
    images: tuple[LlmInputImage, ...] = field(default=(), repr=False)
    # Trusted safety/time prompt for a required initial operation decision.
    # Native optional decisions and later responses retain the full character contract.
    tool_decision_system_prompt: str | None = None
    # Trusted Runtime instruction after user/history/tool messages, never source data.
    # Guides the next tool decision or final revision; included in the wire estimate.
    continuation_system_prompt: str | None = None
    input_budget: LlmInputBudget | None = None
    input_budget_report: LlmInputBudgetReport | None = None
    max_output_tokens: int | None = None
    tool_result_max_bytes: int = 32_768
    # Trusted Runtime output policy after context/history, before the current user.
    # Included by build_messages in every provider-wire budget estimate.
    pre_user_system_prompt: str | None = None
    response_schema: LlmResponseSchema | None = None
    # Ephemeral user-role evidence for this turn, after history and before the
    # trusted output policy/current question. Never persisted history or routing input.
    current_turn_evidence: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.max_output_tokens is not None and (
            type(self.max_output_tokens) is not int or self.max_output_tokens < 1
        ):
            raise ValueError("max_output_tokens must be a positive integer")
        if (
            type(self.tool_result_max_bytes) is not int
            or not 1024 <= self.tool_result_max_bytes <= 1_048_576
        ):
            raise ValueError("tool_result_max_bytes must be within the supported projection limit")
        if len(self.images) > 4:
            raise ValueError("at most 4 images are currently supported")
        for image in self.images:
            if not isinstance(image, LlmInputImage):  # pyright: ignore[reportUnnecessaryIsInstance]
                raise TypeError("images must contain only LlmInputImage instances")


class LlmProvider(Protocol):
    @property
    def kind(self) -> str: ...

    @property
    def supports_tool_calling(self) -> bool: ...

    def stream(self, request: LlmRequest) -> AsyncIterator[LlmStreamEvent]: ...


@dataclass(frozen=True, slots=True)
class SynthesisResult:
    path: Path
    media_type: str
    sample_rate: int
    duration_ms: int
    provider_id: str
    model: str


@dataclass(frozen=True, slots=True)
class TtsPcmChunk:
    """One ordered provider-neutral PCM16 fragment."""

    sequence: int
    pcm16: bytes
    sample_rate: int
    channels: int = 1
    native_streaming: bool = False


@dataclass(frozen=True, slots=True)
class TtsStreamCompleted:
    result: SynthesisResult


type TtsStreamEvent = TtsPcmChunk | TtsStreamCompleted


@dataclass(frozen=True, slots=True)
class SynthesisRequest:
    session_id: UUID
    turn_id: UUID
    generation_id: UUID
    segment_id: UUID
    text: str
    destination: Path
    language: str
    voice_id: str
    speaker_id: int
    speed: float
    style: str | None = None
    pitch: float | None = None


@dataclass(frozen=True, slots=True)
class TtsProviderDescriptor:
    provider_id: str
    display_name: str
    model: str
    languages: tuple[str, ...]
    supports_voice_cloning: bool
    supports_style: bool
    supports_speed: bool
    supports_pitch: bool
    native_streaming: bool
    local_only: bool = True


@dataclass(frozen=True, slots=True)
class TtsProviderHealth:
    status: Literal["ready", "busy", "starting", "degraded", "unavailable"]
    model_loaded: bool
    queue_depth: int = 0
    device: str | None = None
    detail: str | None = None


class TtsProvider(Protocol):
    @property
    def kind(self) -> str: ...

    @property
    def descriptor(self) -> TtsProviderDescriptor: ...

    async def synthesize(self, request: SynthesisRequest) -> SynthesisResult: ...

    async def health(self) -> TtsProviderHealth: ...

    async def deactivate(self) -> None: ...

    async def close(self) -> None: ...
