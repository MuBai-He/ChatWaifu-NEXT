"""Version-aware wire parser registry for events and commands."""

import json
from collections.abc import Mapping
from typing import cast

from pydantic import BaseModel

from chatwaifu_protocol.commands import (
    CommandModel,
    ConversationInterruptCommand,
    PlaybackAckCommand,
    SessionStartCommand,
    TextSendCommand,
)
from chatwaifu_protocol.events import (
    GENERIC_CORE_EVENT_TYPES,
    AssistantGenerationStartedEvent,
    AssistantPlaybackProgressEvent,
    AssistantPlaybackStartedEvent,
    AssistantPlaybackStoppedEvent,
    AssistantSpokenTextCommittedEvent,
    AvatarCueEmittedEvent,
    ErrorRaisedEvent,
    EventModel,
    GenericCoreEvent,
    SessionCreatedEvent,
    UserSpeechStartedEvent,
    UserSpeechStoppedEvent,
    UserTranscriptFinalEvent,
    UserTranscriptPartialEvent,
    UserTurnCommittedEvent,
)
from chatwaifu_protocol.version import SUPPORTED_SCHEMA_MAJOR

type WireObject = dict[str, object]
type WireInput = str | bytes | Mapping[str, object]


class UnsupportedSchemaVersion(ValueError):
    """The message uses a protocol major this package cannot safely interpret."""


class UnknownMessageType(ValueError):
    """The message type is not part of the registered protocol."""


class SchemaRegistry:
    def __init__(self) -> None:
        self._events: dict[str, type[BaseModel]] = {}
        self._commands: dict[str, type[BaseModel]] = {}

    def register_event(self, event_type: str, model: type[BaseModel]) -> None:
        self._events[event_type] = model

    def register_command(self, command_type: str, model: type[BaseModel]) -> None:
        self._commands[command_type] = model

    def parse_event(self, value: WireInput) -> EventModel:
        raw = self._load(value)
        self._require_supported_version(raw)
        event_type = raw.get("event_type")
        model = self._events.get(str(event_type))
        if model is None:
            raise UnknownMessageType(f"unknown event_type: {event_type}")
        return cast(EventModel, model.model_validate(raw))

    def parse_command(self, value: WireInput) -> CommandModel:
        raw = self._load(value)
        self._require_supported_version(raw)
        command_type = raw.get("command_type")
        model = self._commands.get(str(command_type))
        if model is None:
            raise UnknownMessageType(f"unknown command_type: {command_type}")
        return cast(CommandModel, model.model_validate(raw))

    def upgrade(self, value: Mapping[str, object]) -> WireObject:
        upgraded = dict(value)
        self._require_supported_version(upgraded)
        return upgraded

    @staticmethod
    def _load(value: WireInput) -> WireObject:
        if isinstance(value, Mapping):
            return dict(value)
        loaded: object = json.loads(value)
        if not isinstance(loaded, dict):
            raise ValueError("protocol message must be a JSON object")
        return cast(WireObject, loaded)

    @staticmethod
    def _require_supported_version(value: Mapping[str, object]) -> None:
        version = str(value.get("schema_version", ""))
        if version.split(".", maxsplit=1)[0] != SUPPORTED_SCHEMA_MAJOR:
            raise UnsupportedSchemaVersion(f"unsupported schema_version: {version}")


def create_default_registry() -> SchemaRegistry:
    registry = SchemaRegistry()
    registry.register_event("session.created", SessionCreatedEvent)
    registry.register_event("user.turn_committed", UserTurnCommittedEvent)
    registry.register_event("user.speech_started", UserSpeechStartedEvent)
    registry.register_event("user.speech_stopped", UserSpeechStoppedEvent)
    registry.register_event("user.transcript_partial", UserTranscriptPartialEvent)
    registry.register_event("user.transcript_final", UserTranscriptFinalEvent)
    registry.register_event("assistant.generation_started", AssistantGenerationStartedEvent)
    registry.register_event("assistant.playback_started", AssistantPlaybackStartedEvent)
    registry.register_event("assistant.playback_progress", AssistantPlaybackProgressEvent)
    registry.register_event("assistant.playback_stopped", AssistantPlaybackStoppedEvent)
    registry.register_event("assistant.spoken_text_committed", AssistantSpokenTextCommittedEvent)
    registry.register_event("avatar.cue_emitted", AvatarCueEmittedEvent)
    registry.register_event("system.error_raised", ErrorRaisedEvent)
    for event_type in GENERIC_CORE_EVENT_TYPES:
        registry.register_event(event_type, GenericCoreEvent)
    registry.register_command("cmd.session.start", SessionStartCommand)
    registry.register_command("cmd.text.send", TextSendCommand)
    registry.register_command("cmd.conversation.interrupt", ConversationInterruptCommand)
    registry.register_command("cmd.playback.ack", PlaybackAckCommand)
    return registry
