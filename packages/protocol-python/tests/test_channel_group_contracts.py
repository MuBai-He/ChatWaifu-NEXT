"""Strict operator inputs and typed group targets, without generated artifacts."""

from uuid import uuid4

import pytest
from chatwaifu_protocol.channel_groups import (
    ChannelGroupAudienceRequest,
    ChannelGroupDeliveryTarget,
    ChannelGroupRouteCreate,
    ChannelGroupRouteUpdate,
    ChannelParticipantLinkUpdate,
)
from chatwaifu_protocol.schema_export import SCHEMAS, ProtocolCatalog
from pydantic import ValidationError


@pytest.mark.parametrize("value", [True, 1.0, "1", 0, -1])
def test_revisions_are_strict_positive_integers(value: object) -> None:
    with pytest.raises(ValidationError):
        ChannelParticipantLinkUpdate.model_validate(dict(enabled=True, expected_revision=value))


@pytest.mark.parametrize("value", [1, "true", None])
def test_enabling_is_strict_boolean(value: object) -> None:
    with pytest.raises(ValidationError):
        ChannelParticipantLinkUpdate.model_validate(dict(enabled=value, expected_revision=1))


def test_create_has_no_enable_or_scope_input() -> None:
    raw = dict(observation_id=str(uuid4()), display_name="fixture", speaker_sender_keys=["111"])
    assert ChannelGroupRouteCreate.model_validate(raw).speaker_sender_keys == ["111"]
    for field, value in (("enabled", True), ("principal_scope", "local"), ("scene_id", "old")):
        with pytest.raises(ValidationError):
            ChannelGroupRouteCreate.model_validate(dict(raw, **{field: value}))


@pytest.mark.parametrize("speakers", [[], ["111"]])
def test_enable_requires_observation_and_speaker(speakers: list[str]) -> None:
    with pytest.raises(ValidationError):
        ChannelGroupRouteUpdate(
            expected_revision=1, enabled=True, observation_id=None, speaker_sender_keys=speakers
        )


@pytest.mark.parametrize("group_id", ["0", "001", "123/456", " 123", "x", 123])
def test_opaque_group_id_is_canonical_numeric(group_id: object) -> None:
    with pytest.raises(ValidationError):
        ChannelGroupAudienceRequest.model_validate(dict(group_id=group_id))


def test_target_roundtrip_and_extra_recipient_rejected() -> None:
    target = ChannelGroupDeliveryTarget(
        connection_id=uuid4(),
        account_key="900",
        group_id="500",
        route_id=uuid4(),
        route_revision=1,
        channel_turn_id=uuid4(),
        scene_id="scene",
        audience_fingerprint="a" * 64,
    )
    assert ChannelGroupDeliveryTarget.model_validate_json(target.model_dump_json()) == target
    with pytest.raises(ValidationError):
        ChannelGroupDeliveryTarget.model_validate(dict(target.model_dump(), sender_key="owner"))


def test_all_group_contracts_in_catalog_without_exporting_files() -> None:
    schema = ProtocolCatalog.model_json_schema()
    assert "channel_group_delivery_target" in schema["properties"]
    assert SCHEMAS["channel-group-route-create"] is ChannelGroupRouteCreate


def test_group_voice_defaults_off_and_omitted_update_preserves_the_setting() -> None:
    created = ChannelGroupRouteCreate(observation_id=uuid4(), display_name="fixture")
    assert created.allow_requested_voice is False
    update = ChannelGroupRouteUpdate(enabled=False, expected_revision=1, speaker_sender_keys=[])
    assert update.allow_requested_voice is None
    for value in (1, "true"):
        with pytest.raises(ValidationError):
            ChannelGroupRouteUpdate.model_validate(
                {**update.model_dump(), "allow_requested_voice": value}
            )
