"""Provider-private iLink data models.

None of these values cross the ChatWaifu protocol boundary. In particular,
``bot_token``, ``context_token`` and the opaque update cursor stay inside this
adapter and its secure credential/checkpoint stores.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import cast


class WeixinAuthorizationState(StrEnum):
    WAIT = "wait"
    SCANNED = "scaned"
    CONFIRMED = "confirmed"
    EXPIRED = "expired"
    REDIRECT = "scaned_but_redirect"
    VERIFICATION_REQUIRED = "need_verifycode"
    VERIFICATION_BLOCKED = "verify_code_blocked"
    ALREADY_BOUND = "binded_redirect"


@dataclass(frozen=True, slots=True)
class WeixinAuthorizationStart:
    qrcode: str
    qr_code_content: str


@dataclass(frozen=True, slots=True)
class WeixinAuthorizationPoll:
    state: WeixinAuthorizationState
    bot_token: str | None = None
    bot_id: str | None = None
    user_id: str | None = None
    base_url: str | None = None
    redirect_base_url: str | None = None


@dataclass(frozen=True, slots=True)
class WeixinPendingContext:
    context_token: str
    recipient_user_id: str


@dataclass(frozen=True, slots=True)
class WeixinCredentials:
    bot_token: str
    bot_id: str
    user_id: str
    base_url: str
    gateway_access_token: str
    pending_contexts: dict[str, WeixinPendingContext] = field(
        default_factory=lambda: _empty_pending_contexts()
    )

    def to_json(self) -> str:
        return json.dumps(
            {
                "schema_version": "1.0",
                "bot_token": self.bot_token,
                "bot_id": self.bot_id,
                "user_id": self.user_id,
                "base_url": self.base_url,
                "gateway_access_token": self.gateway_access_token,
                "pending_contexts": {
                    key: {
                        "context_token": value.context_token,
                        "recipient_user_id": value.recipient_user_id,
                    }
                    for key, value in self.pending_contexts.items()
                },
            },
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )

    @classmethod
    def from_json(cls, serialized: str) -> WeixinCredentials:
        try:
            raw: object = json.loads(serialized)
        except json.JSONDecodeError as error:
            raise ValueError("stored WeChat credentials are invalid") from error
        if not isinstance(raw, dict):
            raise ValueError("stored WeChat credentials are invalid")
        data = cast(dict[str, object], raw)
        if data.get("schema_version") != "1.0":
            raise ValueError("stored WeChat credential schema is unsupported")
        contexts_raw = data.get("pending_contexts", {})
        if not isinstance(contexts_raw, dict):
            raise ValueError("stored WeChat pending contexts are invalid")
        typed_contexts = cast(dict[object, object], contexts_raw)
        if len(typed_contexts) > 16:
            raise ValueError("stored WeChat pending contexts are invalid")
        contexts: dict[str, WeixinPendingContext] = {}
        for key, value in typed_contexts.items():
            if not isinstance(key, str) or not isinstance(value, dict):
                raise ValueError("stored WeChat pending contexts are invalid")
            item = cast(dict[str, object], value)
            contexts[_required_string(key, "external_message_id", 512)] = WeixinPendingContext(
                context_token=_required_string(item.get("context_token"), "context_token", 8_192),
                recipient_user_id=_required_string(
                    item.get("recipient_user_id"), "recipient_user_id", 512
                ),
            )
        return cls(
            bot_token=_required_string(data.get("bot_token"), "bot_token", 16_384),
            bot_id=_required_string(data.get("bot_id"), "bot_id", 512),
            user_id=_required_string(data.get("user_id"), "user_id", 512),
            base_url=_required_string(data.get("base_url"), "base_url", 2_048),
            gateway_access_token=_required_string(
                data.get("gateway_access_token"), "gateway_access_token", 1_024
            ),
            pending_contexts=contexts,
        )


@dataclass(frozen=True, slots=True)
class WeixinInboundText:
    external_message_id: str
    sender_user_id: str
    recipient_bot_id: str
    text: str
    context_token: str
    received_at: datetime


@dataclass(frozen=True, slots=True)
class WeixinUpdates:
    cursor: str
    messages: tuple[WeixinInboundText, ...]


def _required_string(value: object, name: str, max_length: int) -> str:
    if not isinstance(value, str) or not value or len(value) > max_length:
        raise ValueError(f"stored WeChat {name} is invalid")
    return value


def _empty_pending_contexts() -> dict[str, WeixinPendingContext]:
    return {}
