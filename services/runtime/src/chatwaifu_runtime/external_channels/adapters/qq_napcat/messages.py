"""Normalize only stable, owner-direct structured messages."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from uuid import UUID

from chatwaifu_protocol.base import JsonObject
from chatwaifu_protocol.channels import ChannelInboundTextMessage

from .client import validate_image_file_ref


@dataclass(frozen=True, slots=True)
class NapCatImageReference:
    file_ref: str = field(repr=False)
    file_size: int | None = None
    invalid_reason: str | None = None


@dataclass(frozen=True, slots=True)
class NapCatRecordReference:
    file_ref: str = field(repr=False)
    file_size: int | None = None
    invalid_reason: str | None = None


@dataclass(frozen=True, slots=True)
class NapCatInboundMessage:
    message: ChannelInboundTextMessage
    images: tuple[NapCatImageReference, ...] = field(default=(), repr=False)
    record: NapCatRecordReference | None = field(default=None, repr=False)


def normalize(
    event: JsonObject, *, connection_id: UUID, account: str, owner: str | None
) -> ChannelInboundTextMessage | None:
    """Keep pairing restricted to structured text, without accepting media."""
    inbound = _normalize(
        event, connection_id=connection_id, account=account, owner=owner, allow_media=False
    )
    return inbound.message if inbound is not None else None


def normalize_inbound(
    event: JsonObject, *, connection_id: UUID, account: str, owner: str
) -> NapCatInboundMessage | None:
    """Admit owner-private text/images or one record, never provider URLs."""
    return _normalize(
        event, connection_id=connection_id, account=account, owner=owner, allow_media=True
    )


def _normalize(
    event: JsonObject,
    *,
    connection_id: UUID,
    account: str,
    owner: str | None,
    allow_media: bool,
) -> NapCatInboundMessage | None:
    if event.get("post_type") != "message" or event.get("message_type") != "private":
        return None
    sender = event.get("user_id")
    message_id = event.get("message_id")
    if (
        type(sender) not in {str, int}
        or not re.fullmatch(r"[0-9]{1,20}", str(sender))
        or int(str(sender)) <= 0
        or str(sender) == account
    ):
        return None
    if owner is not None and str(sender) != owner:
        return None
    if (
        str(event.get("self_id")) != account
        or type(message_id) not in {str, int}
        or not re.fullmatch(r"-?[0-9]{1,20}", str(message_id))
    ):
        return None
    segments = event.get("message")
    if not isinstance(segments, list) or len(segments) > 128:
        return None
    texts: list[str] = []
    images: list[NapCatImageReference] = []
    record: NapCatRecordReference | None = None
    reply: str | None = None
    for segment in segments:
        if not isinstance(segment, dict):
            return None
        data = segment.get("data")
        if not isinstance(data, dict):
            return None
        if segment.get("type") == "text" and isinstance(data.get("text"), str):
            value = data["text"]
            assert isinstance(value, str)
            texts.append(value)
        elif segment.get("type") == "reply" and type(data.get("id")) in {str, int}:
            reply = str(data["id"])
            if not re.fullmatch(r"-?[0-9]{1,20}", reply):
                return None
        elif allow_media and segment.get("type") == "image":
            file_ref = data.get("file")
            if not isinstance(file_ref, str):
                return None
            try:
                validate_image_file_ref(file_ref)
            except ValueError:
                return None
            raw_size = data.get("file_size")
            file_size: int | None = None
            invalid_reason: str | None = None
            if raw_size is not None:
                if type(raw_size) in {str, int} and re.fullmatch(r"[0-9]{1,12}", str(raw_size)):
                    file_size = int(str(raw_size))
                else:
                    invalid_reason = "invalid_size"
            images.append(NapCatImageReference(file_ref, file_size, invalid_reason))
        elif allow_media and segment.get("type") == "record":
            if record is not None:
                return None
            file_ref = data.get("file")
            try:
                valid_ref = validate_image_file_ref(file_ref)
            except ValueError:
                return None
            raw_size = data.get("file_size")
            file_size = None
            invalid_reason = None
            if raw_size is not None:
                if type(raw_size) in {str, int} and re.fullmatch(r"[0-9]{1,12}", str(raw_size)):
                    file_size = int(str(raw_size))
                else:
                    invalid_reason = "invalid_size"
            record = NapCatRecordReference(valid_ref, file_size, invalid_reason)
        else:
            # Mixed media must not be silently presented as complete text understanding.
            return None
    text = "".join(texts).strip()
    if record is not None:
        if texts or images:
            return None
        text = "[语音]"
    if not text and images:
        text = "[图片]"
    if not text or len(text) > 20_000:
        return None
    message = ChannelInboundTextMessage(
        connection_id=connection_id,
        account_key=account,
        external_message_id=str(message_id),
        conversation_key=f"direct:{sender}",
        sender_key=str(sender),
        principal_scope="local",
        text=text,
        received_at=datetime.now(UTC),
        reply_to_external_message_id=reply,
    )
    return NapCatInboundMessage(message=message, images=tuple(images), record=record)
