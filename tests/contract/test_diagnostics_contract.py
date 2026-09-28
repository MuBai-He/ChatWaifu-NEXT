"""Versioned owner diagnostics fixtures and explicit page bounds."""

import json
from pathlib import Path

import pytest
from chatwaifu_protocol.diagnostics import InteractionTraceDetail, InteractionTracePage
from pydantic import ValidationError

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "protocol" / "v1"


def test_diagnostics_fixtures_round_trip_without_content_fields() -> None:
    page = InteractionTracePage.model_validate_json(
        (FIXTURES / "interaction-trace-page.json").read_text()
    )
    detail = InteractionTraceDetail.model_validate_json(
        (FIXTURES / "interaction-trace-detail.json").read_text()
    )
    assert page.items[0] == detail.summary
    serialized = json.dumps(detail.model_dump(mode="json"))
    for forbidden in ("user_text", "assistant_text", "prompt_text", "api_key"):
        assert forbidden not in serialized


def test_diagnostics_page_bounds_are_enforced() -> None:
    page = InteractionTracePage.model_validate_json(
        (FIXTURES / "interaction-trace-page.json").read_text()
    )
    with pytest.raises(ValidationError):
        InteractionTracePage(items=page.items * 51)
