"""Normalize only stable, owner-direct structured messages."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from uuid import UUID

from chatwaifu_protocol.base import JsonObject
from chatwaifu_protocol.channels import ChannelInboundTextMessage

from .client import validate_image_file_ref
from .expressions import face_context
from .groups import NapCatGroupInboundMessage, qq_group_identifier


@dataclass(frozen=True, slots=True)
class NapCatImageReference:
    file_ref: str = field(repr=False)
    file_size: int | None = None
    invalid_reason: str | None = None
    expected_md5: str | None = None


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


def normalize_group_inbound(
    event: JsonObject,
    *,
    connection_id: UUID,
    account: str,
    group_id: str,
    allowed_senders: frozenset[str] | None,
    allow_unmentioned_images: bool = False,
    allow_unmentioned_text: bool = False,
    allow_audio: bool = False,
) -> NapCatGroupInboundMessage | None:
    """Normalize a structured mention, including its optional reply envelope.

    A supplied transport whitelist also filters senders. With None, the result
    remains unresolved and must pass the group's durable application admission.
    Reply metadata neither grants a trigger nor loads provider-quoted content;
    Conversation continues to use its existing scope-checked group history.
    """
    sender = qq_group_identifier(event.get("user_id"))
    message_id = event.get("message_id")
    details = event.get("sender")
    if (
        qq_group_identifier(account) != account
        or qq_group_identifier(group_id) != group_id
        or event.get("post_type") != "message"
        or event.get("message_type") != "group"
        or event.get("sub_type") != "normal"
        or event.get("anonymous") is not None
        or qq_group_identifier(event.get("self_id")) != account
        or qq_group_identifier(event.get("group_id")) != group_id
        or sender is None
        or sender == account
        or (allowed_senders is not None and sender not in allowed_senders)
        or not isinstance(details, dict)
        or qq_group_identifier(details.get("user_id")) != sender
        or type(message_id) not in {str, int}
        or not re.fullmatch(r"-?[0-9]{1,20}", str(message_id))
        or str(int(str(message_id))) != str(message_id)
        or int(str(message_id)) == 0
    ):
        return None
    segments = event.get("message")
    if not isinstance(segments, list) or not 1 <= len(segments) <= 128:
        return None
    texts: list[str] = []
    images: list[NapCatImageReference] = []
    record: NapCatRecordReference | None = None
    mentions = 0
    has_reply = False
    size = 0
    for segment in segments:
        if not isinstance(segment, dict) or not isinstance(segment.get("data"), dict):
            return None
        data = segment["data"]
        assert isinstance(data, dict)
        if segment.get("type") == "at":
            if qq_group_identifier(data.get("qq")) != account:
                return None
            mentions += 1
        elif segment.get("type") == "text" and isinstance(data.get("text"), str):
            value = data["text"]
            assert isinstance(value, str)
            size += len(value)
            if size > 20_000:
                return None
            texts.append(value)
        elif segment.get("type") == "reply":
            reference = data.get("id")
            if (
                has_reply
                or type(reference) not in {str, int}
                or not re.fullmatch(r"-?[0-9]{1,20}", str(reference))
                or str(int(str(reference))) != str(reference)
                or int(str(reference)) == 0
            ):
                return None
            has_reply = True
        elif segment.get("type") == "face":
            context = face_context(data)
            if context is None:
                return None
            texts.append(context)
        elif allow_audio and segment.get("type") == "record":
            if record is not None:
                return None
            record = _record_reference(data)
            if record is None:
                return None
        elif segment.get("type") == "image":
            image = _image_reference(data)
            if image is None or len(images) >= 4:
                return None
            # NapCat's filename lookup is account-global. Group media must prove
            # its own provider checksum; an opaque name must never read a private asset.
            checksum = re.fullmatch(
                r"(?:([0-9a-fA-F]{32})|\{([0-9a-fA-F]{32})\})(?:\.[a-zA-Z0-9]{1,8})?",
                image.file_ref,
            )
            image = replace(
                image,
                expected_md5=(checksum.group(1) or checksum.group(2)).lower()
                if checksum is not None
                else None,
                invalid_reason=image.invalid_reason
                if checksum is not None
                else "unverifiable_reference",
            )
            images.append(image)
        else:
            return None
    text = "".join(texts).strip()
    if record is not None:
        if text or images:
            return None
        text = "[语音]"
    if not text and images:
        text = "[图片]"
    if (
        (
            mentions != 1
            and not (
                mentions == 0
                and (
                    (allow_unmentioned_images and images) or (allow_unmentioned_text and not images)
                )
            )
        )
        or (not text and mentions != 1)
        or len(text) > 20_000
    ):
        return None
    return NapCatGroupInboundMessage(
        connection_id,
        account,
        group_id,
        sender,
        str(message_id),
        text,
        datetime.now(UTC),
        tuple(images),
        mentions == 1,
        mentions == 1 and not text,
        record,
    )


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
            image = _image_reference(data)
            if image is None:
                return None
            images.append(image)
        elif allow_media and segment.get("type") == "face":
            context = face_context(data)
            if context is None:
                return None
            texts.append(context)
        elif allow_media and segment.get("type") == "record":
            if record is not None:
                return None
            record = _record_reference(data)
            if record is None:
                return None
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


def _image_reference(data: JsonObject) -> NapCatImageReference | None:
    file_ref = data.get("file")
    if not isinstance(file_ref, str) or file_ref == "marketface":
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
    return NapCatImageReference(file_ref, file_size, invalid_reason)


def _record_reference(data: JsonObject) -> NapCatRecordReference | None:
    try:
        file_ref = validate_image_file_ref(data.get("file"))
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
    return NapCatRecordReference(file_ref, file_size, invalid_reason)


def normalize_poke(event: JsonObject, *, account: str) -> JsonObject | None:
    """Map a stable, bot-targeted provider event to an untrusted conversation observation.

    No fabricated command or user-supplied scope is introduced. The stable synthetic
    message ID deduplicates retransmissions through the existing admission/cache path.
    """
    sender = qq_group_identifier(event.get("user_id"))
    timestamp = event.get("time")
    if (
        event.get("post_type") != "notice"
        or event.get("notice_type") != "notify"
        or event.get("sub_type") != "poke"
        or qq_group_identifier(event.get("self_id")) != account
        or qq_group_identifier(event.get("target_id")) != account
        or sender is None
        or sender == account
        or type(timestamp) is not int
        or not datetime.now(UTC).timestamp() - 120
        <= timestamp
        <= datetime.now(UTC).timestamp() + 30
    ):
        return None
    raw_group = event.get("group_id")
    group = qq_group_identifier(raw_group)
    if raw_group is not None and raw_group not in (0, "0") and group is None:
        return None
    encoded = json.dumps(event, sort_keys=True, ensure_ascii=False).encode()
    if len(encoded) > 32_000:
        return None
    message_id = -max(1, int.from_bytes(hashlib.sha256(encoded).digest()[:8], "big") >> 1)
    result: JsonObject = {
        "post_type": "message",
        "message_type": "group" if group is not None else "private",
        "sub_type": "normal" if group is not None else "friend",
        "self_id": account,
        "user_id": sender,
        "sender": {"user_id": sender},
        "message_id": message_id,
        "time": timestamp,
        "message": [
            {
                "type": "text",
                "data": {"text": "[QQ 事件: 当前发言者戳了你一下，并未发出文字请求。]"},
            }
        ],
    }
    if group is not None:
        result["group_id"] = group
    return result
