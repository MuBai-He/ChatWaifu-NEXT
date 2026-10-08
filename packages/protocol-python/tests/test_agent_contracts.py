"""Cross-language Agent fixtures and authority boundary rejection."""

import json
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest
from chatwaifu_protocol.agent import (
    AgentTask,
    AgentTaskCreate,
    ArtifactRef,
    CapabilityPage,
    DecisionRecord,
)
from chatwaifu_protocol.channels import ChannelDeliverySnapshot
from pydantic import ValidationError


def test_agent_fixtures_roundtrip_without_losing_authority_or_evidence() -> None:
    root = Path(__file__).resolve().parents[3]
    fixture = json.loads((root / "tests/fixtures/protocol/v1/agent-contracts.json").read_text())
    for key, model in {
        "task": AgentTask,
        "artifact": ArtifactRef,
        "capabilities": CapabilityPage,
        "decision": DecisionRecord,
    }.items():
        assert model.model_validate(fixture[key]).model_dump(mode="json") == fixture[key]
    create: dict[str, Any] = {
        "session_id": fixture["task"]["session_id"],
        "goal": "read",
        "authorization": fixture["task"]["authorization"],
        "channel_binding": {},
    }
    with pytest.raises(ValidationError):
        AgentTaskCreate.model_validate(create)
    with pytest.raises(ValidationError):
        DecisionRecord.model_validate({"action": "respond", "reason": "source missing"})


def test_task_delivery_uses_explicit_12_source_and_rejects_mixed_sources() -> None:
    from datetime import UTC, datetime

    value = dict(
        schema_version="1.2",
        delivery_id=uuid4(),
        connection_id=uuid4(),
        task_delivery_id=uuid4(),
        status="pending",
        part_count=1,
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )
    assert ChannelDeliverySnapshot.model_validate(value).task_delivery_id is not None
    for patch in (
        {"schema_version": "1.0"},
        {"channel_turn_id": uuid4()},
        {"outbound_intent_id": uuid4()},
    ):
        with pytest.raises(ValidationError):
            ChannelDeliverySnapshot.model_validate({**value, **patch})
