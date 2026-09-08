"""OpenAI GA wire normalization. Provider identifiers never become Runtime IDs."""

from __future__ import annotations

import base64
from collections import OrderedDict, deque
from dataclasses import dataclass, field
from typing import cast
from uuid import UUID

from chatwaifu_runtime.realtime.cloud.contracts import (
    AssistantTranscriptEvent,
    InputAudioCommittedEvent,
    OutputAudioEvent,
    ProviderErrorEvent,
    RealtimeOutputAudioFrame,
    RealtimeProviderError,
    RealtimeProviderEvent,
    RealtimeTranscriptCandidate,
    RealtimeUsage,
    ResponseCancelledEvent,
    ResponseCompletedEvent,
    ResponseStartedEvent,
    SessionDegradedEvent,
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
    requested: bool = False
    interrupted: bool = False
    done: bool = False
    sequence: int = 0
    output_samples: int = 0
    output_items: set[str] = field(default_factory=set[str])
    deleted_items: set[str] = field(default_factory=set[str])
    assistant_transcript_chars: int = 0
    user_transcript_chars: int = 0


class OpenAIEventMapper:
    """Bounded correlation using local commits and echoed response metadata only."""

    def __init__(self, session_id: UUID) -> None:
        self.session_id = session_id
        self.turns: OrderedDict[UUID, OpenAITurn] = OrderedDict()
        self.pending_commits: deque[UUID] = deque()
        self._seen: OrderedDict[str, None] = OrderedDict()

    def register(self, generation_id: UUID) -> OpenAITurn:
        if generation_id in self.turns:
            raise OpenAIRealtimeError("openai_realtime_reused_generation")
        if len(self.turns) >= MAX_TURNS:
            oldest, turn = next(iter(self.turns.items()))
            if oldest in self.pending_commits or not (turn.done or turn.interrupted):
                raise OpenAIRealtimeError("openai_realtime_correlation_capacity")
            self.turns.pop(oldest)
        turn = OpenAITurn(generation_id)
        self.turns[generation_id] = turn
        return turn

    def metadata(self, turn: OpenAITurn) -> dict[str, str]:
        return {"cw_session_id": str(self.session_id), "cw_generation_id": str(turn.generation_id)}

    def response_turn(self, response_id: str) -> OpenAITurn | None:
        return next((t for t in self.turns.values() if t.response_id == response_id), None)

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
            if (prior is not None and prior is not turn) or turn.response_id not in (
                None,
                response_id,
            ):
                raise OpenAIRealtimeError("openai_realtime_response_rebinding")
            if turn.response_id is not None:
                return []
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
        if event_type == "response.output_item.added":
            item = object_value(event.get("item"))
            if item.get("type") != "message" or item.get("role") != "assistant":
                raise OpenAIRealtimeError("openai_realtime_unsupported_output_item")
            turn.output_items.add(string_value(item.get("id")))
            if len(turn.output_items) > 16:
                raise OpenAIRealtimeError("openai_realtime_output_item_capacity")
        if event_type == "response.done":
            if turn.done:
                return []
            turn.done = True
            if turn.interrupted or response.get("status") == "cancelled":
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
            final = event_type.endswith(".done")
            text = event.get("transcript" if final else "delta")
            if text == "":
                return []
            text = string_value(text)
            if not final:
                turn.assistant_transcript_chars += len(text)
                if turn.assistant_transcript_chars > MAX_TEXT:
                    raise OpenAIRealtimeError("openai_realtime_transcript_capacity")
            return [
                AssistantTranscriptEvent(
                    RealtimeTranscriptCandidate(
                        self.session_id,
                        turn.generation_id,
                        "assistant",
                        "final" if final else "delta",
                        text,
                        provider_response_id=response_id,
                    ),
                    event_id=event_id,
                )
            ]
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
