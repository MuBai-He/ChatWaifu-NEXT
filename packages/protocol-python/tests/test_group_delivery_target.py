"""A group target cannot be attached to a proactive or mismatched delivery."""

from datetime import UTC, datetime
from uuid import uuid4

import pytest
from chatwaifu_protocol.channel_groups import ChannelGroupDeliveryTarget
from chatwaifu_protocol.channels import ChannelDeliveryPlanSnapshot
from pydantic import ValidationError


def plan_payload() -> dict[str, object]:
    now = datetime.now(UTC)
    delivery_id, turn_id, connection_id = uuid4(), uuid4(), uuid4()
    return {
        "schema_version": "1.0",
        "delivery_id": delivery_id,
        "channel_turn_id": turn_id,
        "connection_id": connection_id,
        "status": "pending",
        "part_count": 1,
        "created_at": now,
        "updated_at": now,
        "group_target": {
            "connection_id": connection_id,
            "account_key": "10001",
            "group_id": "20001",
            "route_id": uuid4(),
            "route_revision": 1,
            "channel_turn_id": turn_id,
            "scene_id": str(uuid4()),
            "audience_fingerprint": "a" * 64,
        },
        "parts": [
            {
                "part_id": uuid4(),
                "delivery_id": delivery_id,
                "ordinal": 0,
                "kind": "text",
                "payload": {"kind": "text", "text": "群回复"},
                "status": "pending",
                "provider_client_id": "fixed-part",
                "created_at": now,
                "updated_at": now,
            }
        ],
    }


def test_group_target_roundtrip_remains_public_through_channel_groups() -> None:
    snapshot = ChannelDeliveryPlanSnapshot.model_validate(plan_payload())
    assert isinstance(snapshot.group_target, ChannelGroupDeliveryTarget)
    assert ChannelDeliveryPlanSnapshot.model_validate_json(snapshot.model_dump_json()) == snapshot
    assert snapshot.schema_version == "1.0" and snapshot.outbound_intent_id is None


@pytest.mark.parametrize(
    "change",
    [
        "connection",
        "turn",
        "outbound",
        "missing_part",
        "extra_part",
        "part_delivery",
        "ordinal",
        "optional",
    ],
)
def test_group_plan_rejects_mismatched_or_proactive_targets(change: str) -> None:
    payload = plan_payload()
    if change == "connection":
        payload["connection_id"] = uuid4()
    elif change == "turn":
        payload["channel_turn_id"] = uuid4()
    elif change == "outbound":
        payload.update(schema_version="1.1", channel_turn_id=None, outbound_intent_id=uuid4())
    else:
        parts = payload["parts"]
        assert isinstance(parts, list) and isinstance(parts[0], dict)
        if change == "missing_part":
            payload["parts"] = []
        elif change == "extra_part":
            payload["parts"] = [parts[0], parts[0]]
            payload["part_count"] = 2
        elif change == "part_delivery":
            parts[0]["delivery_id"] = uuid4()
        elif change == "ordinal":
            parts[0]["ordinal"] = 1
        else:
            parts[0]["required"] = False
    with pytest.raises(ValidationError, match="fixed inbound target"):
        ChannelDeliveryPlanSnapshot.model_validate(payload)


@pytest.mark.parametrize("field", ["account_key", "group_id"])
@pytest.mark.parametrize("value", ["0", "01", "-1", " 10001", "123456789012345678901"])
def test_group_target_requires_canonical_qq_identifiers(field: str, value: str) -> None:
    target = plan_payload()["group_target"]
    assert isinstance(target, dict)
    target[field] = value
    with pytest.raises(ValidationError):
        ChannelGroupDeliveryTarget.model_validate(target)
