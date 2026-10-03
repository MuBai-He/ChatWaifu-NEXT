"""Normalize only stable, owner-direct structured messages."""

from __future__ import annotations

import re
from datetime import UTC, datetime
from uuid import UUID

from chatwaifu_protocol.base import JsonObject
from chatwaifu_protocol.channels import ChannelInboundTextMessage

from chatwaifu_runtime.runtime_skills.voice_intent import requests_voice as requests_voice


def normalize(
    event: JsonObject, *, connection_id: UUID, account: str, owner: str | None
) -> ChannelInboundTextMessage | None:
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
        else:
            # Mixed media must not be silently presented as complete text understanding.
            return None
    text = "".join(texts).strip()
    if not text or len(text) > 20_000:
        return None
    return ChannelInboundTextMessage(
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
