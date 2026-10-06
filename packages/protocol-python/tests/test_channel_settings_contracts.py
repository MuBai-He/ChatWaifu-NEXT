"""The UI and Runtime share strict permission and bounded budget contracts."""

import json
from pathlib import Path

import pytest
from chatwaifu_protocol.channel_settings import (
    ChannelRuntimePolicy,
    ChannelRuntimeSettingsResponse,
    ChannelRuntimeSettingsUpdate,
    GroupDiscussionPolicy,
)
from chatwaifu_protocol.schema_export import SCHEMAS
from pydantic import ValidationError


def test_settings_fixture_and_schema_registry() -> None:
    path = (
        Path(__file__).resolve().parents[3]
        / "tests/fixtures/protocol/v1/channel-runtime-settings.json"
    )
    response = ChannelRuntimeSettingsResponse.model_validate_json(path.read_text())
    assert response.revision == 7
    assert response.policy.group_discussion.input_tokens == 4096
    assert response.policy.qq_owner_public_web_enabled is True
    assert response.policy.qq_owner_voice_reply_enabled is False
    assert response.search_provider == "searxng" and response.reader_provider == "crawl4ai"
    assert response.model_dump(mode="json") == json.loads(path.read_text())
    assert SCHEMAS["channel-runtime-settings-response"] is ChannelRuntimeSettingsResponse
    assert SCHEMAS["channel-runtime-settings-update"] is ChannelRuntimeSettingsUpdate
    assert SCHEMAS["group-discussion-policy"] is GroupDiscussionPolicy


@pytest.mark.parametrize(
    "field",
    [
        "qq_owner_public_web_enabled",
        "qq_owner_voice_reply_enabled",
        "qq_owner_voice_input_enabled",
        "qq_native_favorites_enabled",
    ],
)
@pytest.mark.parametrize("value", ["false", "true", 0, 1, None])
def test_permission_values_never_coerce(field: str, value: object) -> None:
    with pytest.raises(ValidationError):
        ChannelRuntimePolicy.model_validate({field: value})


@pytest.mark.parametrize(
    "patch",
    [
        {"input_tokens": "4096"},
        {"input_tokens": 8193},
        {"input_tokens": 127},
        {"member_messages": 33, "cache_messages": 32},
        {"member_characters": 6000, "cache_characters": 3200},
        {"enabled": "true"},
        {"summary_timeout_seconds": 0},
        {"unknown": 1},
    ],
)
def test_budget_and_capacity_are_bounded(patch: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        GroupDiscussionPolicy.model_validate(patch)


def test_updates_require_revision_and_cannot_edit_identity() -> None:
    for revision in (-1, "1", True, None):
        with pytest.raises(ValidationError):
            ChannelRuntimeSettingsUpdate.model_validate(
                {"expected_revision": revision, "policy": {}}
            )
    with pytest.raises(ValidationError):
        ChannelRuntimeSettingsUpdate.model_validate(
            {"expected_revision": 0, "policy": {}, "account_key": "999"}
        )
    defaults = ChannelRuntimePolicy()
    assert defaults.group_discussion == GroupDiscussionPolicy()
    assert defaults.qq_owner_public_web_enabled is False
    assert defaults.qq_owner_voice_reply_enabled is True
