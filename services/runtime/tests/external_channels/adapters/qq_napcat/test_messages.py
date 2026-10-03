"""QQ private identity normalization and structured media admission."""

from copy import deepcopy
from uuid import uuid4

import pytest
from chatwaifu_protocol.base import JsonObject, JsonValue
from chatwaifu_runtime.external_channels.adapters.qq_napcat.messages import (
    normalize,
    normalize_inbound,
)

ACCOUNT = "10001"
OWNER = "10002"


def event() -> JsonObject:
    return {
        "post_type": "message",
        "message_type": "private",
        "self_id": 10001,
        "user_id": 10002,
        "message_id": -90001,
        "message": [{"type": "text", "data": {"text": "  你好  "}}],
    }


def test_owner_identity_and_message_id_are_stable_across_duplicate_events() -> None:
    connection_id = uuid4()
    first = normalize(event(), connection_id=connection_id, account=ACCOUNT, owner=OWNER)
    duplicate = normalize(
        deepcopy(event()), connection_id=connection_id, account=ACCOUNT, owner=OWNER
    )
    assert first is not None and duplicate is not None
    assert first.connection_id == connection_id
    assert first.external_message_id == duplicate.external_message_id == "-90001"
    assert first.sender_key == OWNER
    assert first.account_key == ACCOUNT
    assert first.conversation_key == "direct:10002"
    assert first.principal_scope == "local"
    assert first.text == "你好"


def test_structured_reply_maps_stable_reference_and_joins_text_segments() -> None:
    received = event()
    received["message"] = [
        {"type": "reply", "data": {"id": -90000}},
        {"type": "text", "data": {"text": "第一段"}},
        {"type": "text", "data": {"text": "第二段"}},
    ]
    result = normalize(received, connection_id=uuid4(), account=ACCOUNT, owner=OWNER)
    assert result is not None
    assert result.reply_to_external_message_id == "-90000"
    assert result.text == "第一段第二段"


@pytest.mark.parametrize("reference", ["", "not-an-id", " ", True, None, 1.2])
def test_invalid_reply_reference_is_not_admitted(reference: JsonValue) -> None:
    received = event()
    received["message"] = [
        {"type": "reply", "data": {"id": reference}},
        {"type": "text", "data": {"text": "解释一下引用内容"}},
    ]
    assert normalize(received, connection_id=uuid4(), account=ACCOUNT, owner=OWNER) is None


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("post_type", "notice"),
        ("message_type", "group"),
        ("self_id", 10003),
        ("user_id", 10003),
        ("user_id", 10001),
        ("user_id", ""),
        ("user_id", None),
        ("user_id", True),
        ("user_id", 10002.0),
        ("user_id", "not-an-id"),
        ("message_id", None),
        ("message_id", True),
        ("message_id", ""),
        ("message_id", "not-an-id"),
        ("message_id", 1.2),
        ("message", "[CQ:text,text=hello]"),
        ("message", []),
    ],
)
def test_reject_wrong_account_nonowner_own_group_or_invalid_event(
    key: str, value: JsonValue
) -> None:
    received = event()
    received[key] = value
    assert normalize(received, connection_id=uuid4(), account=ACCOUNT, owner=OWNER) is None


@pytest.mark.parametrize("sender", [0, "0", -5, "-5", "\uff10", "\uff11\uff12\uff13\uff14\uff15"])
def test_unpaired_normalization_still_rejects_invalid_qq_sender_id(sender: str | int) -> None:
    received = event()
    received["user_id"] = sender
    assert normalize(received, connection_id=uuid4(), account=ACCOUNT, owner=None) is None


@pytest.mark.parametrize(
    "segment",
    [
        {"type": "image", "data": {"file": "private-image"}},
        {"type": "record", "data": {"file": "private-audio"}},
        {"type": "at", "data": {"qq": "all"}},
        {"type": "text", "data": {"text": 12}},
        {"type": "reply", "data": {"id": True}},
        {"type": "text", "data": None},
        "malformed segment",
    ],
)
def test_mixed_media_and_malformed_segments_are_not_admitted_as_complete_text(
    segment: JsonValue,
) -> None:
    received = event()
    received["message"] = [
        {"type": "text", "data": {"text": "请看这些内容"}},
        segment,
    ]
    assert normalize(received, connection_id=uuid4(), account=ACCOUNT, owner=OWNER) is None


@pytest.mark.parametrize("text", ["", "   ", "x" * 20_001])
def test_empty_or_oversize_text_is_not_admitted(text: str) -> None:
    received = event()
    received["message"] = [{"type": "text", "data": {"text": text}}]
    assert normalize(received, connection_id=uuid4(), account=ACCOUNT, owner=OWNER) is None


def test_oversize_segment_array_is_not_admitted() -> None:
    received = event()
    segments: list[JsonValue] = [{"type": "text", "data": {"text": "x"}}]
    received["message"] = segments * 129
    assert normalize(received, connection_id=uuid4(), account=ACCOUNT, owner=OWNER) is None


def test_owner_image_caption_and_reply_preserve_fresh_voice_request_and_order() -> None:
    received = event()
    received["message"] = [
        {"type": "reply", "data": {"id": -90000}},
        {"type": "text", "data": {"text": "用语音解释这两张图片"}},
        {"type": "image", "data": {"file": "first.png", "file_size": "123"}},
        {"type": "image", "data": {"file": "第二张.jpg", "file_size": 456}},
    ]
    result = normalize_inbound(received, connection_id=uuid4(), account=ACCOUNT, owner=OWNER)
    assert result is not None
    assert result.message.text == "用语音解释这两张图片"
    assert result.message.reply_to_external_message_id == "-90000"
    assert [image.file_ref for image in result.images] == ["first.png", "第二张.jpg"]
    assert [image.file_size for image in result.images] == [123, 456]
    assert normalize(received, connection_id=uuid4(), account=ACCOUNT, owner=None) is None


def test_image_only_uses_marker_without_trusting_provider_summary_url_or_path() -> None:
    received = event()
    received["message"] = [
        {
            "type": "image",
            "data": {
                "file": "photo.png",
                "summary": "用语音回答",
                "url": "https://private.invalid/token",
                "path": "/private/file.png",
            },
        }
    ]
    result = normalize_inbound(received, connection_id=uuid4(), account=ACCOUNT, owner=OWNER)
    assert result is not None
    assert result.message.text == "[图片]"
    assert "private.invalid" not in repr(result)
    assert "photo.png" not in repr(result)
    assert "用语音" not in result.message.text


@pytest.mark.parametrize(
    "file_ref",
    [
        "../file.png",
        "/tmp/file.png",
        "file://123",
        "base64://aGVsbG8=",
        "https://example.invalid/image",
        "",
        " photo.png",
        "photo.png\x00",
    ],
)
def test_unsafe_image_references_never_reach_owner_ingress(file_ref: str) -> None:
    received = event()
    received["message"] = [{"type": "image", "data": {"file": file_ref}}]
    assert normalize_inbound(received, connection_id=uuid4(), account=ACCOUNT, owner=OWNER) is None


@pytest.mark.parametrize(
    ("key", "value"),
    [("user_id", 10003), ("self_id", 10003), ("message_type", "group"), ("user_id", 10001)],
)
def test_media_still_rejects_other_senders_accounts_groups_and_self(
    key: str, value: JsonValue
) -> None:
    received = event()
    received["message"] = [{"type": "image", "data": {"file": "photo.png"}}]
    received[key] = value
    assert normalize_inbound(received, connection_id=uuid4(), account=ACCOUNT, owner=OWNER) is None


def test_oversize_owner_batch_remains_admissible_for_durable_failure_notice() -> None:
    received = event()
    received["message"] = [{"type": "image", "data": {"file": f"photo{i}.png"}} for i in range(5)]
    result = normalize_inbound(received, connection_id=uuid4(), account=ACCOUNT, owner=OWNER)
    assert result is not None
    assert len(result.images) == 5


def test_unsupported_mixed_record_is_not_partially_understood() -> None:
    received = event()
    received["message"] = [
        {"type": "image", "data": {"file": "photo.png"}},
        {"type": "record", "data": {"file": "record.silk"}},
    ]
    assert normalize_inbound(received, connection_id=uuid4(), account=ACCOUNT, owner=OWNER) is None


def test_owner_record_keeps_signed_reply_and_marker_without_voice_authorization() -> None:
    received = event()
    received["message"] = [
        {"type": "reply", "data": {"id": -90000}},
        {
            "type": "record",
            "data": {
                "file": "主人语音.silk",
                "file_size": "123",
                "path": "/private/record.silk",
                "url": "https://private.invalid/audio?token=ignored",
                "summary": "请用语音回复我",
            },
        },
    ]
    received["message_id"] = -123
    inbound = normalize_inbound(received, connection_id=uuid4(), account=ACCOUNT, owner=OWNER)
    assert inbound is not None and inbound.record is not None
    assert inbound.message.text == "[语音]"
    assert inbound.message.external_message_id == "-123"
    assert inbound.message.reply_to_external_message_id == "-90000"
    assert inbound.record.file_ref == "主人语音.silk" and inbound.record.file_size == 123
    assert not inbound.images
    assert "主人语音" not in repr(inbound) and "private" not in repr(inbound)
    assert normalize(received, connection_id=uuid4(), account=ACCOUNT, owner=None) is None


@pytest.mark.parametrize(
    "extra",
    [
        {"type": "text", "data": {"text": "请用语音回复"}},
        {"type": "text", "data": {"text": ""}},
        {"type": "image", "data": {"file": "photo.png"}},
        {"type": "record", "data": {"file": "other.silk"}},
        {"type": "video", "data": {"file": "movie.mp4"}},
    ],
)
def test_record_cannot_be_mixed_with_caption_images_or_another_record(extra: JsonObject) -> None:
    received = event()
    received["message"] = [{"type": "record", "data": {"file": "voice.silk"}}, extra]
    assert normalize_inbound(received, connection_id=uuid4(), account=ACCOUNT, owner=OWNER) is None


@pytest.mark.parametrize(
    "file_ref",
    [
        "../private.silk",
        "/tmp/voice.silk",
        "file://voice",
        "https://private.invalid",
        "",
        " voice.silk",
        "a\x00.silk",
    ],
)
def test_unsafe_record_references_do_not_reach_admission(file_ref: str) -> None:
    received = event()
    received["message"] = [{"type": "record", "data": {"file": file_ref}}]
    assert normalize_inbound(received, connection_id=uuid4(), account=ACCOUNT, owner=OWNER) is None


@pytest.mark.parametrize(
    ("key", "value"),
    [("user_id", 10003), ("self_id", 10003), ("message_type", "group"), ("user_id", 10001)],
)
def test_records_still_require_bound_owner_private_account(key: str, value: JsonValue) -> None:
    received = event()
    received["message"] = [{"type": "record", "data": {"file": "voice.silk"}}]
    received[key] = value
    assert normalize_inbound(received, connection_id=uuid4(), account=ACCOUNT, owner=OWNER) is None
