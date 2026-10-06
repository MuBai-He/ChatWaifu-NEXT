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
        {"timezone": "Factory"},
        {"timezone": "localtime"},
        {"timezone": "posixrules"},
        {"quiet_start": "24:00"},
        {"quiet_end": "08:60"},
        {"ttl_minutes": 61},
        {"ttl_minutes": 0},
        {"idle_minutes": 0},
        {"daily_budget": 21},
        {"source": "calendar_event"},
        {"recipient": "untrusted-model-target"},
        {"idle_minutes": True},
        {"idle_minutes": "45"},
        {"enabled": "false"},
        {"quiet_hours_enabled": 1},
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


@pytest.mark.parametrize("revision", [True, "0", 0.5])
def test_operator_mutations_reject_coerced_revisions(revision: object) -> None:
    with pytest.raises(ValidationError):
        ChannelProactivePolicyUpdate.model_validate({"expected_revision": revision, "policy": {}})
    with pytest.raises(ValidationError):
        ChannelOutboundIntentCancelRequest.model_validate({"expected_revision": revision})


def test_integer_valued_json_numbers_match_javascript() -> None:
    policy = ChannelProactivePolicy.model_validate({"idle_minutes": 45.0})
    assert policy.idle_minutes == 45
    assert (
        ChannelProactivePolicyUpdate.model_validate(
            {"expected_revision": 0.0, "policy": policy}
        ).expected_revision
        == 0
    )
    assert (
        ChannelOutboundIntentCancelRequest.model_validate(
            {"expected_revision": 0.0}
        ).expected_revision
        == 0
    )
