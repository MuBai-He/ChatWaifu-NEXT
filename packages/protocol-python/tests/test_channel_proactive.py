"""Public proactive contracts preserve opt-in and operator route boundaries."""

from uuid import uuid4

import pytest
from chatwaifu_protocol.channel_proactive import (
    ChannelOutboundIntentCancelRequest,
    ChannelProactivePolicy,
    ChannelProactivePolicySnapshot,
    ChannelProactivePolicyUpdate,
)
from pydantic import ValidationError


def test_absent_policy_is_disabled_and_does_not_invent_a_binding() -> None:
    snapshot = ChannelProactivePolicySnapshot(connection_id=uuid4())
    assert snapshot.revision == 0
    assert snapshot.binding_id is None
    assert snapshot.policy.enabled is False
    assert snapshot.policy.source == "idle_check_in"


@pytest.mark.parametrize(
    "override",
    [
        {"timezone": "invalid/not-a-time-zone"},
        {"quiet_start": "24:00"},
        {"quiet_end": "08:60"},
        {"ttl_minutes": 61},
        {"ttl_minutes": 0},
        {"idle_minutes": 0},
        {"daily_budget": 21},
        {"source": "calendar_event"},
        {"recipient": "untrusted-model-target"},
    ],
)
def test_policy_rejects_unbounded_or_untrusted_inputs(override: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        ChannelProactivePolicy.model_validate(override)


def test_policy_and_cancel_require_observed_revision() -> None:
    with pytest.raises(ValidationError):
        ChannelProactivePolicyUpdate.model_validate({"policy": {"enabled": True}})
    with pytest.raises(ValidationError):
        ChannelOutboundIntentCancelRequest.model_validate({})
    update = ChannelProactivePolicyUpdate(
        expected_revision=0, policy=ChannelProactivePolicy(enabled=True)
    )
    assert ChannelProactivePolicyUpdate.model_validate_json(update.model_dump_json()) == update
