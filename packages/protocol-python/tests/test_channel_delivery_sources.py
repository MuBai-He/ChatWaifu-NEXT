"""Only outbound delivery snapshots opt into schema 1.1; legacy ACK stays inbound."""

from datetime import UTC, datetime
from uuid import uuid4

import pytest
from chatwaifu_protocol.channels import (
    ChannelDeliveryClaimRequest,
    ChannelDeliveryPlanSnapshot,
    ChannelDeliverySnapshot,
)
from pydantic import ValidationError


@pytest.mark.parametrize("model", [ChannelDeliverySnapshot, ChannelDeliveryPlanSnapshot])
@pytest.mark.parametrize(
    "version,inbound,outbound,valid",
    [
        ("1.0", True, False, True),
        ("1.1", False, True, True),
        ("1.0", False, True, False),
        ("1.1", True, False, False),
        ("1.1", True, True, False),
        ("1.0", False, False, False),
    ],
)
def test_explicit_delivery_source_version_pairing(
    model: type[ChannelDeliverySnapshot] | type[ChannelDeliveryPlanSnapshot],
    version: str,
    inbound: bool,
    outbound: bool,
    valid: bool,
) -> None:
    data = dict(
        schema_version=version,
        delivery_id=uuid4(),
        connection_id=uuid4(),
        status="pending",
        part_count=1,
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
        channel_turn_id=uuid4() if inbound else None,
        outbound_intent_id=uuid4() if outbound else None,
    )
    if valid:
        snapshot = model.model_validate(data)
        assert snapshot.schema_version == version
    else:
        with pytest.raises(ValidationError):
            model.model_validate(data)


def test_legacy_claim_cannot_expand_to_outbound_schema() -> None:
    with pytest.raises(ValidationError):
        ChannelDeliveryClaimRequest.model_validate(
            dict(
                schema_version="1.1",
                delivery_id=uuid4(),
                channel_turn_id=None,
                outbound_intent_id=uuid4(),
                lease_id=uuid4(),
            )
        )
