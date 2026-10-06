"""Group admission never resolves nicknames, participant IDs or Runtime scope."""

from copy import deepcopy
from uuid import uuid4

import pytest
from chatwaifu_protocol.base import JsonObject, JsonValue
from chatwaifu_runtime.external_channels.adapters.qq_napcat.groups import normalize_group_notice
from chatwaifu_runtime.external_channels.adapters.qq_napcat.messages import (
    normalize_group_inbound,
    normalize_inbound,
)

ACCOUNT = "10001"
GROUP = "20001"
SPEAKER = "10002"


def message() -> JsonObject:
    return {
        "post_type": "message",
        "message_type": "group",
        "sub_type": "normal",
        "self_id": 10001,
        "group_id": 20001,
        "user_id": 10002,
        "sender": {"user_id": 10002, "nickname": "主人", "card": "管理员"},
        "message_id": -90001,
        "message": [
            {"type": "at", "data": {"qq": "10001"}},
            {"type": "text", "data": {"text": "  你好 "}},
            {"type": "text", "data": {"text": "群聊  "}},
        ],
    }


def admit(event: JsonObject):
    return normalize_group_inbound(
        event,
        connection_id=uuid4(),
        account=ACCOUNT,
        group_id=GROUP,
        allowed_senders=frozenset({SPEAKER, "10003"}),
    )


def test_raw_group_identity_is_stable_and_has_no_granted_scope() -> None:
    event = message()
    first = admit(event)
    assert first is not None
    assert first.account_key == ACCOUNT and first.group_id == GROUP
    assert first.sender_key == SPEAKER and first.external_message_id == "-90001"
    assert first.text == "你好 群聊"
    assert not hasattr(first, "principal_scope") and not hasattr(first, "participant_id")
    assert "你好" not in repr(first)
    changed = deepcopy(event)
    changed["sender"] = {"user_id": SPEAKER, "nickname": "其他人", "card": "同名"}
    duplicate = admit(changed)
    assert duplicate is not None
    assert duplicate.sender_key == first.sender_key
    assert duplicate.external_message_id == first.external_message_id
    assert normalize_inbound(event, connection_id=uuid4(), account=ACCOUNT, owner=SPEAKER) is None


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("post_type", "notice"),
        ("message_type", "private"),
        ("sub_type", "anonymous"),
        ("sub_type", None),
        ("anonymous", {}),
        ("anonymous", False),
        ("self_id", 10003),
        ("self_id", True),
        ("group_id", 20002),
        ("group_id", "020001"),
        ("group_id", 0),
        ("user_id", 10001),
        ("user_id", 10004),
        ("user_id", False),
        ("user_id", "010002"),
        ("sender", None),
        ("sender", {"user_id": 10003, "nickname": "主人"}),
        ("message_id", "unknown"),
        ("message_id", True),
        ("message_id", 1.5),
        ("message_id", 0),
        ("message_id", "-090001"),
        ("message", "[CQ:at,qq=10001] 你好"),
        ("message", [{"type": "text", "data": {"text": "@主人 你好"}}]),
        ("message", [{"type": "text", "data": {"text": "[CQ:at,qq=10001] 你好"}}]),
        ("message", [{"type": "at", "data": {"qq": "all"}}]),
        ("message", [{"type": "at", "data": {"qq": True}}]),
        ("message", [{"type": "at", "data": {"qq": "10003"}}]),
    ],
)
def test_group_identity_and_real_mention_are_required(key: str, value: JsonValue) -> None:
    event = message()
    event[key] = value
    assert admit(event) is None


@pytest.mark.parametrize("kind", ["image", "record", "reply", "file", "face", "forward"])
def test_group_mixed_media_and_quotes_are_rejected(kind: str) -> None:
    event = message()
    event["message"] = [
        {"type": "at", "data": {"qq": ACCOUNT}},
        {"type": "text", "data": {"text": "你好"}},
        {"type": kind, "data": {"file": "safe.png", "id": "90000"}},
    ]
    assert admit(event) is None


@pytest.mark.parametrize("text", ["", " \t\n", "x" * 20_001])
def test_group_requires_bounded_nonempty_text(text: str) -> None:
    event = message()
    event["message"] = [
        {"type": "at", "data": {"qq": ACCOUNT}},
        {"type": "text", "data": {"text": text}},
    ]
    assert admit(event) is None


def test_unknown_sender_cannot_be_admitted_by_matching_display_name() -> None:
    event = message()
    assert (
        normalize_group_inbound(
            event,
            connection_id=uuid4(),
            account=ACCOUNT,
            group_id=GROUP,
            allowed_senders=frozenset({"主人"}),
        )
        is None
    )


def notice(kind: str = "group_increase", subtype: str = "approve") -> JsonObject:
    return {
        "post_type": "notice",
        "self_id": ACCOUNT,
        "group_id": GROUP,
        "user_id": SPEAKER,
        "operator_id": 0,
        "notice_type": kind,
        "sub_type": subtype,
    }


@pytest.mark.parametrize(
    ("kind", "subtype"),
    [
        ("group_increase", "approve"),
        ("group_increase", "invite"),
        ("group_decrease", "leave"),
        ("group_decrease", "kick"),
        ("group_decrease", "kick_me"),
        ("group_admin", "set"),
        ("group_admin", "unset"),
    ],
)
def test_membership_notice_is_typed_and_fixed_to_account_group(kind: str, subtype: str) -> None:
    event = notice(kind, subtype)
    event["untrusted_name"] = "主人"
    observed = normalize_group_notice(event, account=ACCOUNT, group_id=GROUP)
    assert observed is not None and observed.operator_id is None
    assert observed.user_id == SPEAKER and observed.notice_type == kind
    assert "untrusted_name" not in observed.to_event()
    assert normalize_group_notice(event, account="10003", group_id=GROUP) is None
    assert normalize_group_notice(event, account=ACCOUNT, group_id="20002") is None
    assert admit(event) is None


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("post_type", "message"),
        ("notice_type", "group_upload"),
        ("sub_type", "unknown"),
        ("self_id", False),
        ("group_id", -1),
        ("user_id", 0),
        ("user_id", "x"),
        ("operator_id", True),
    ],
)
def test_notice_rejects_unsupported_or_invalid_identities(key: str, value: JsonValue) -> None:
    event = notice()
    event[key] = value
    assert normalize_group_notice(event, account=ACCOUNT, group_id=GROUP) is None
