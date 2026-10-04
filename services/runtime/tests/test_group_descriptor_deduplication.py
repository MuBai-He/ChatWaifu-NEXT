"""Provider redelivery has a stable content digest despite local arrival time."""

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from chatwaifu_runtime.external_channels.group_models import ChannelGroupInboundDescriptor


def test_group_redelivery_does_not_use_the_local_arrival_time_as_content() -> None:
    message = ChannelGroupInboundDescriptor(
        uuid4(), "10001", "20001", "30001", "-40001", "你好", datetime.now(UTC)
    )
    repeated = replace(message, received_at=message.received_at + timedelta(seconds=10))
    assert repeated.content_sha256 == message.content_sha256
    for changed in (
        replace(message, sender_key="30002"),
        replace(message, group_id="20002"),
        replace(message, account_key="10002"),
        replace(message, text="另一条消息"),
    ):
        assert changed.content_sha256 != message.content_sha256


@pytest.mark.parametrize("message_id", ["0", "00", "01", "-0", "-01"])
def test_group_descriptor_rejects_noncanonical_provider_ids(message_id: str) -> None:
    with pytest.raises(ValueError, match="raw provider message ID"):
        ChannelGroupInboundDescriptor(
            uuid4(), "10001", "20001", "30001", message_id, "你好", datetime.now(UTC)
        )
