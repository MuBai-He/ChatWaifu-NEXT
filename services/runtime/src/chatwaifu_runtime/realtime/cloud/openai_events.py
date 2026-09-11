"""OpenAI GA wire normalization. Provider identifiers never become Runtime IDs."""

from __future__ import annotations

import base64
import hashlib
import json
import math
from collections import OrderedDict, deque
from dataclasses import dataclass, field
from typing import cast
from uuid import UUID

from chatwaifu_protocol.base import JsonObject

from chatwaifu_runtime.realtime.cloud.contracts import (
    AssistantTranscriptEvent,
    InputAudioCommittedEvent,
    OutputAudioEvent,
    ProviderErrorEvent,
    RealtimeOutputAudioFrame,
    RealtimeProviderError,
    RealtimeProviderEvent,
    RealtimeToolCall,
    RealtimeTranscriptCandidate,
    RealtimeUsage,
    ResponseCancelledEvent,
    ResponseCompletedEvent,
    ResponseStartedEvent,
    SessionDegradedEvent,
    ToolCallRequestedEvent,
    UsageRecordedEvent,
    UserTranscriptEvent,
)

BACKEND_ID = "openai"
PCM_RATE = 24_000
MAX_TURNS = 64
MAX_TEXT = 64_000


class OpenAIRealtimeError(RuntimeError):
    """Safe error code: never contains provider payloads, URLs, keys or transcripts."""


def provider_error_code(raw: object) -> str:
    error = object_value(raw)
    code = error.get("code")
    if code in ("invalid_api_key", "invalid_authentication", "authentication_error"):
        return "openai_realtime_authentication_failed"
    if code == "insufficient_quota":
        return "openai_realtime_quota_exhausted"
    if code == "rate_limit_exceeded":
        return "openai_realtime_rate_limited"
    if code == "server_error" or error.get("type") == "server_error":
        return "openai_realtime_service_unavailable"
    return "openai_realtime_request_rejected"


def object_value(value: object) -> dict[str, object]:
    if not isinstance(value, dict):
        raise OpenAIRealtimeError("openai_realtime_invalid_event")
    return cast(dict[str, object], value)


def string_value(value: object) -> str:
    if not isinstance(value, str) or not value or len(value) > MAX_TEXT:
        raise OpenAIRealtimeError("openai_realtime_invalid_event")
    return value


@dataclass(slots=True)
class OpenAITurn:
    generation_id: UUID
    input_item_id: str | None = None
    response_id: str | None = None
    decision_done: bool = False
    decision_items: set[str] = field(default_factory=set[str])
    continuation_response_id: str | None = None
    continuation_reserved: bool = False
    tools_exposed: bool = False
    tool_calls_emitted: bool = False
    requested: bool = False
    interrupted: bool = False
    done: bool = False
    sequence: int = 0
    output_samples: int = 0
    output_items: set[str] = field(default_factory=set[str])
    deleted_items: set[str] = field(default_factory=set[str])
    function_call_items: dict[str, dict[str, object]] = field(
        default_factory=dict[str, dict[str, object]]
    )
    assistant_transcript_chars: int = 0
    user_transcript_chars: int = 0


class OpenAIEventMapper:
    """Bounded correlation using local commits and echoed response metadata only."""

    def __init__(self, session_id: UUID) -> None:
        self.session_id = session_id
        self.turns: OrderedDict[UUID, OpenAITurn] = OrderedDict()
        self.pending_commits: deque[UUID] = deque()
        self._seen: OrderedDict[str, None] = OrderedDict()

    def register(self, generation_id: UUID, *, tools_exposed: bool = False) -> OpenAITurn:
        if generation_id in self.turns:
            raise OpenAIRealtimeError("openai_realtime_reused_generation")
        if len(self.turns) >= MAX_TURNS:
            oldest, turn = next(iter(self.turns.items()))
            if oldest in self.pending_commits or not (turn.done or turn.interrupted):
                raise OpenAIRealtimeError("openai_realtime_correlation_capacity")
            self.turns.pop(oldest)
        turn = OpenAITurn(generation_id, tools_exposed=tools_exposed)
        self.turns[generation_id] = turn
        return turn

    def reserve_continuation(self, generation_id: UUID) -> None:
        turn = self.turns.get(generation_id)
        if turn is not None:
            turn.continuation_reserved = True

    def metadata(self, turn: OpenAITurn) -> dict[str, str]:
        return {"cw_session_id": str(self.session_id), "cw_generation_id": str(turn.generation_id)}

    def response_turn(self, response_id: str) -> OpenAITurn | None:
        return next(
            (
                t
                for t in self.turns.values()
                if t.response_id == response_id or t.continuation_response_id == response_id
            ),
            None,
        )

    def item_turn(self, item_id: str) -> OpenAITurn | None:
        return next((t for t in self.turns.values() if t.input_item_id == item_id), None)

    def normalize(self, event: dict[str, object]) -> list[RealtimeProviderEvent]:
        event_type = string_value(event.get("type"))
        event_id = string_value(event.get("event_id"))
        if event_id in self._seen:
            return []
        self._seen[event_id] = None
        if len(self._seen) > 2048:
            self._seen.popitem(last=False)
        if event_type == "input_audio_buffer.committed":
            item_id = string_value(event.get("item_id"))
            if self.item_turn(item_id) is not None:
                return []
            if not self.pending_commits:
                raise OpenAIRealtimeError("openai_realtime_unsolicited_commit")
            turn = self.turns[self.pending_commits.popleft()]
            turn.input_item_id = item_id
            return [InputAudioCommittedEvent(self.session_id, event_id=event_id)]
        if event_type.startswith("conversation.item.input_audio_transcription."):
            item_id = string_value(event.get("item_id"))
            turn = self.item_turn(item_id)
            if turn is None or turn.interrupted:
                return []
            if event_type.endswith(".failed"):
                return [
                    SessionDegradedEvent(
                        self.session_id, BACKEND_ID, "input_transcription_failed", event_id=event_id
                    )
                ]
            if not event_type.endswith((".delta", ".completed")):
                return []
            final = event_type.endswith(".completed")
            text = event.get("transcript" if final else "delta")
            if text == "":
                return []
            text = string_value(text)
            if not final:
                turn.user_transcript_chars += len(text)
                if turn.user_transcript_chars > MAX_TEXT:
                    raise OpenAIRealtimeError("openai_realtime_transcript_capacity")
            return [
                UserTranscriptEvent(
                    RealtimeTranscriptCandidate(
                        session_id=self.session_id,
                        generation_id=turn.generation_id,
                        role="user",
                        phase="final" if final else "delta",
                        text=text,
                        provider_item_id=item_id,
                    ),
                    event_id=event_id,
                )
            ]
        if event_type == "response.created":
            response = object_value(event.get("response"))
            metadata = response.get("metadata")
            if not isinstance(metadata, dict):
                raise OpenAIRealtimeError("openai_realtime_missing_response_identity")
            metadata = cast(dict[str, object], metadata)
            turn = next((t for t in self.turns.values() if metadata == self.metadata(t)), None)
            if turn is None or not turn.requested:
                raise OpenAIRealtimeError("openai_realtime_unknown_response_identity")
            response_id = string_value(response.get("id"))
            prior = self.response_turn(response_id)
            if prior is not None and prior is not turn:
                raise OpenAIRealtimeError("openai_realtime_response_rebinding")

            if turn.response_id is not None:
                # Idempotent replay of initial response.created
                if turn.response_id == response_id:
                    return []
                # Idempotent replay of continuation response.created
                if turn.continuation_response_id == response_id:
                    return []
                # Multiple provider responses belong to same Runtime generation
                # ONLY through explicitly reserved continuation
                if not turn.continuation_reserved:
                    raise OpenAIRealtimeError("openai_realtime_response_rebinding")
                if (
                    turn.continuation_response_id is not None
                    and turn.continuation_response_id != response_id
                ):
                    raise OpenAIRealtimeError("openai_realtime_response_rebinding")

                # Exact one reservation consumed
                turn.continuation_reserved = False
                turn.continuation_response_id = response_id
                turn.done = False
                # Do NOT call turn.output_items.clear()! Retain cleanup ownership of earlier items!
                return [
                    ResponseStartedEvent(
                        self.session_id, turn.generation_id, response_id, event_id=event_id
                    )
                ]

            turn.response_id = response_id
            return [
                ResponseStartedEvent(
                    self.session_id, turn.generation_id, response_id, event_id=event_id
                )
            ]
        if not event_type.startswith("response."):
            return []
        response = object_value(event["response"]) if event_type == "response.done" else event
        response_id = string_value(
            response.get("id") if event_type == "response.done" else event.get("response_id")
        )
        turn = self.response_turn(response_id)
        if turn is None:
            # Never attribute late/unknown events to the most recent utterance.
            return []

        # Permanently close decision phase at authoritative response.done;
        # Drop late old response events before/after final response.
        if response_id == turn.response_id and turn.decision_done:
            return []
        if turn.tool_calls_emitted and response_id == turn.response_id:
            return []

        if event_type == "response.output_item.added":
            item = object_value(event.get("item"))
            item_type = item.get("type")
            item_id = string_value(item.get("id"))
            turn.output_items.add(item_id)
            if len(turn.output_items) > 64:
                raise OpenAIRealtimeError("openai_realtime_output_item_capacity")

            if turn.tools_exposed and turn.continuation_response_id is None:
                if item_type == "message" and item.get("role") == "assistant":
                    turn.decision_items.add(item_id)
                    return []
                elif item_type == "function_call":
                    turn.function_call_items[item_id] = item
                    return []
                else:
                    raise OpenAIRealtimeError("openai_realtime_unsupported_output_item")

            if item_type == "message" and item.get("role") == "assistant":
                return []
            elif item_type == "function_call":
                if not turn.tools_exposed or turn.continuation_response_id is not None:
                    raise OpenAIRealtimeError("openai_realtime_unsupported_output_item")
                return []
            else:
                raise OpenAIRealtimeError("openai_realtime_unsupported_output_item")
        if event_type in {
            "response.function_call_arguments.delta",
            "response.function_call_arguments.done",
        }:
            return []
        if event_type == "response.output_item.done":
            item = object_value(event.get("item"))
            if item.get("type") == "function_call":
                return []
            return []
        if event_type == "response.done":
            if turn.done:
                return []
            if turn.interrupted or response.get("status") == "cancelled":
                turn.done = True
                return [
                    ResponseCancelledEvent(
                        self.session_id,
                        turn.generation_id,
                        response_id,
                        reason="cancelled" if turn.interrupted else "provider_cancelled",
                        event_id=event_id,
                    )
                ]
            if response.get("status") != "completed":
                # Incomplete and failed are never successful assistant turns.
                turn.done = True
                return [
                    ProviderErrorEvent(
                        RealtimeProviderError(
                            self.session_id,
                            BACKEND_ID,
                            "response_failed",
                            "Cloud voice response did not complete.",
                            generation_id=turn.generation_id,
                        ),
                        event_id=event_id,
                    )
                ]

            is_decision = turn.tools_exposed and turn.continuation_response_id is None
            if is_decision:
                turn.decision_done = True
                decision_events: list[RealtimeProviderEvent] = []
                usage = self._usage(response.get("usage"), turn)
                if usage is not None:
                    decision_events.append(UsageRecordedEvent(usage, event_id=f"{event_id}:usage"))

                raw_output = response.get("output", [])
                if not isinstance(raw_output, list):
                    raise OpenAIRealtimeError("openai_realtime_invalid_event")
                raw_output_items = cast(list[object], raw_output)

                function_call_items = [
                    object_value(raw_item)
                    for raw_item in raw_output_items
                    if object_value(raw_item).get("type") == "function_call"
                ]

                if function_call_items:
                    if len(function_call_items) > 16:
                        raise OpenAIRealtimeError("openai_realtime_output_item_capacity")

                    tool_calls: list[RealtimeToolCall] = []
                    seen_fcs_in_done: dict[str, str] = {}
                    for fc in function_call_items:
                        call_id = string_value(fc.get("call_id"))
                        name = string_value(fc.get("name"))
                        if len(call_id) > 128 or len(name) > 64:
                            raise OpenAIRealtimeError("openai_realtime_invalid_function_call")
                        args_raw = fc.get("arguments", "{}")
                        if not isinstance(args_raw, str):
                            raise OpenAIRealtimeError("openai_realtime_invalid_function_call")
                        args_bytes = args_raw.encode("utf-8")
                        if len(args_bytes) > 65_536:
                            raise OpenAIRealtimeError("openai_realtime_invalid_function_call")

                        def _reject_constant(_c: str) -> None:
                            raise OpenAIRealtimeError("openai_realtime_invalid_function_call")

                        try:
                            args_obj = json.loads(args_raw, parse_constant=_reject_constant)
                            if not isinstance(args_obj, dict):
                                raise OpenAIRealtimeError("openai_realtime_invalid_function_call")
                        except (ValueError, RecursionError):
                            raise OpenAIRealtimeError(
                                "openai_realtime_invalid_function_call"
                            ) from None

                        def _validate_json_bounds(val: object, depth: int = 0) -> None:
                            if depth > 8:
                                raise OpenAIRealtimeError("openai_realtime_invalid_function_call")
                            if isinstance(val, float) and not math.isfinite(val):
                                raise OpenAIRealtimeError("openai_realtime_invalid_function_call")
                            if isinstance(val, dict):
                                val_dict = cast(dict[object, object], val)
                                for k, v in val_dict.items():
                                    if not isinstance(k, str) or len(k) > 1024:
                                        raise OpenAIRealtimeError(
                                            "openai_realtime_invalid_function_call"
                                        )
                                    _validate_json_bounds(v, depth + 1)
                            elif isinstance(val, list):
                                val_list = cast(list[object], val)
                                if len(val_list) > 256:
                                    raise OpenAIRealtimeError(
                                        "openai_realtime_invalid_function_call"
                                    )
                                for it in val_list:
                                    _validate_json_bounds(it, depth + 1)

                        _validate_json_bounds(cast(object, args_obj))

                        digest = hashlib.sha256(name.encode() + b"\0" + args_bytes).hexdigest()
                        if call_id in seen_fcs_in_done:
                            if seen_fcs_in_done[call_id] != digest:
                                raise OpenAIRealtimeError("openai_realtime_invalid_function_call")
                        else:
                            seen_fcs_in_done[call_id] = digest

                        tool_calls.append(
                            RealtimeToolCall(
                                call_id=call_id,
                                name=name,
                                arguments=cast(JsonObject, args_obj),
                            )
                        )

                    turn.tool_calls_emitted = True
                    return [
                        *decision_events,
                        ToolCallRequestedEvent(
                            session_id=self.session_id,
                            generation_id=turn.generation_id,
                            provider_response_id=response_id,
                            calls=tuple(tool_calls),
                            event_id=event_id,
                        ),
                    ]
                else:
                    # The coordinator reserves the same final speech phase even for no calls.
                    return [
                        *decision_events,
                        ToolCallRequestedEvent(
                            session_id=self.session_id,
                            generation_id=turn.generation_id,
                            provider_response_id=response_id,
                            calls=(),
                            event_id=event_id,
                        ),
                    ]

            turn.done = True
            return [
                ResponseCompletedEvent(
                    self.session_id,
                    turn.generation_id,
                    response_id,
                    usage=self._usage(response.get("usage"), turn),
                    final_text=self._final_text(response),
                    event_id=event_id,
                )
            ]

        if turn.interrupted or turn.done:
            return []
        if event_type in {"response.output_audio.delta", "response.output_audio.done"}:
            if turn.tools_exposed and turn.continuation_response_id is None:
                # Decision phase when tools exposed: text-only, no audio emitted
                return []
            audio = b""
            final = event_type.endswith(".done")
            if not final:
                delta = event.get("delta")
                if not isinstance(delta, str) or len(delta) > 1_048_576:
                    raise OpenAIRealtimeError("openai_realtime_invalid_audio")
                try:
                    audio = base64.b64decode(delta, validate=True)
                except ValueError:
                    raise OpenAIRealtimeError("openai_realtime_invalid_audio") from None
                if not audio or len(audio) % 2:
                    raise OpenAIRealtimeError("openai_realtime_invalid_audio")
            frame = RealtimeOutputAudioFrame(
                self.session_id,
                turn.generation_id,
                turn.sequence,
                turn.output_samples * 1000 // PCM_RATE,
                PCM_RATE,
                1,
                audio,
                is_final=final,
            )
            turn.sequence += 1
            turn.output_samples += len(audio) // 2
            return [OutputAudioEvent(frame, event_id=event_id)]
        if event_type in {
            "response.output_audio_transcript.delta",
            "response.output_audio_transcript.done",
        }:
            if turn.tools_exposed and turn.continuation_response_id is None:
                # Decision phase when tools exposed: decision text never spoken or stored as heard
                return []
            final = event_type.endswith(".done")
            text = event.get("transcript" if final else "delta")
            if text == "":
                return []
            text = string_value(text)
            if not final:
                turn.assistant_transcript_chars += len(text)
                if turn.assistant_transcript_chars > MAX_TEXT:
                    raise OpenAIRealtimeError("openai_realtime_transcript_capacity")
            cand = RealtimeTranscriptCandidate(
                self.session_id,
                turn.generation_id,
                "assistant",
                "final" if final else "delta",
                text,
                provider_response_id=response_id,
            )
            return [AssistantTranscriptEvent(cand, event_id=event_id)]
        return []

    def _usage(self, raw: object, turn: OpenAITurn) -> RealtimeUsage | None:
        if raw is None:
            return None
        usage = object_value(raw)
        counts: list[int] = []
        for key in ("input_tokens", "output_tokens", "total_tokens"):
            value = usage.get(key, 0)
            if type(value) is not int or value < 0:
                raise OpenAIRealtimeError("openai_realtime_invalid_usage")
            counts.append(value)
        return RealtimeUsage(
            self.session_id,
            BACKEND_ID,
            generation_id=turn.generation_id,
            input_tokens=counts[0],
            output_tokens=counts[1],
            total_tokens=counts[2],
            output_audio_seconds=turn.output_samples / PCM_RATE,
        )

    @staticmethod
    def _final_text(response: dict[str, object]) -> str | None:
        output = response.get("output", [])
        if not isinstance(output, list):
            raise OpenAIRealtimeError("openai_realtime_invalid_event")
        texts: list[str] = []
        for raw_item in cast(list[object], output):
            item = object_value(raw_item)
            if item.get("type") == "function_call":
                continue
            if item.get("type") != "message" or item.get("role") != "assistant":
                raise OpenAIRealtimeError("openai_realtime_unsupported_output_item")
            content = item.get("content", [])
            if not isinstance(content, list):
                raise OpenAIRealtimeError("openai_realtime_invalid_event")
            for raw_part in cast(list[object], content):
                part = object_value(raw_part)
                if part.get("type") == "output_audio" and part.get("transcript"):
                    texts.append(string_value(part["transcript"]))
        text = "\n".join(texts)
        if len(text) > MAX_TEXT:
            raise OpenAIRealtimeError("openai_realtime_transcript_capacity")
        return text if texts else None
