"""Bounded native QQ expressions preserve text, pairing and group authority."""
# pyright: reportPrivateUsage=false

from uuid import uuid4

import pytest
from chatwaifu_protocol.base import JsonObject, JsonValue
from chatwaifu_runtime.external_channels.adapters.qq_napcat.expressions import native_text_segments
from chatwaifu_runtime.external_channels.adapters.qq_napcat.messages import (
    normalize,
    normalize_group_inbound,
    normalize_inbound,
)
from test_qq_channels import ACCOUNT, OWNER, _event
from test_qq_group_runtime import ALICE, GROUP, _group_event


def _face(value: JsonValue = "14") -> JsonObject:
    return {"type": "face", "data": {"id": value}}


def test_native_owner_face_is_context_but_cannot_pair() -> None:
    event = _event("", 991)
    event["message"] = [_face()]
    connection = uuid4()
    assert normalize(event, connection_id=connection, account=ACCOUNT, owner=None) is None
    inbound = normalize_inbound(event, connection_id=connection, account=ACCOUNT, owner=OWNER)
    assert inbound is not None
    assert inbound.message.text == "[QQ表情:微笑]"
    assert not inbound.images and inbound.record is None


@pytest.mark.parametrize("bad", [True, -1, 1.5, "014", "../face", "1234567", None])
def test_invalid_native_face_never_admits(bad: JsonValue) -> None:
    event = _event("hello", 992)
    event["message"] = [_face(bad), {"type": "text", "data": {"text": "hello"}}]
    assert normalize_inbound(event, connection_id=uuid4(), account=ACCOUNT, owner=OWNER) is None


def test_only_explicit_unicode_faces_render_native_and_code_stays_literal() -> None:
    assert native_text_segments("收到🙂 好的❤️") == [
        {"type": "text", "data": {"text": "收到"}},
        {"type": "face", "data": {"id": "14"}},
        {"type": "text", "data": {"text": " 好的"}},
        {"type": "face", "data": {"id": "66"}},
    ]
    for literal in ("`🙂`", "[CQ:face,id=14]", "[QQ表情:微笑]", "🙂x" * 129):
        assert native_text_segments(literal) == [{"type": "text", "data": {"text": literal}}]


def test_group_face_requires_a_real_mention_and_granted_sender() -> None:
    event = _group_event(993, "")
    event["message"] = [{"type": "at", "data": {"qq": ACCOUNT}}, _face()]
    connection = uuid4()
    inbound = normalize_group_inbound(
        event,
        connection_id=connection,
        account=ACCOUNT,
        group_id=GROUP,
        allowed_senders=frozenset({ALICE}),
    )
    assert inbound is not None and inbound.text == "[QQ表情:微笑]"
    event["message"] = [_face()]
    assert (
        normalize_group_inbound(
            event,
            connection_id=connection,
            account=ACCOUNT,
            group_id=GROUP,
            allowed_senders=frozenset({ALICE}),
        )
        is None
    )
    event["message"] = [{"type": "at", "data": {"qq": ACCOUNT}}, _face()]
    assert (
        normalize_group_inbound(
            event,
            connection_id=connection,
            account=ACCOUNT,
            group_id=GROUP,
            allowed_senders=frozenset(),
        )
        is None
    )
