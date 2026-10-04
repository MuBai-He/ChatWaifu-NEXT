"""Protocol/state guards; these do not approve a model's classification."""

import json

import pytest
from chatwaifu_runtime.agent.source_answer_frame import (
    MAX_FRAME_GAPS,
    MAX_GAP_CHARACTERS,
    FrameSource,
    SourceAnswerFrameError,
    SourceAnswerGap,
    decode_source_answer_frame,
    source_answer_frame_schema,
)
from jsonschema import ValidationError, validate

SOURCE = FrameSource("https://example.org/notice", "a" * 64)
UNKNOWN = "The supplied notice cannot establish all operators' implementation as of 2026-10-04."


def _block(text: str, scope: object) -> dict[str, object]:
    return {
        "kind": "ordered_item",
        "text": text,
        "source_indices": [0],
        "prior_gap_ids": [],
        "unresolved_scope": scope,
    }


def _decode(blocks: list[dict[str, object]], prior: tuple[SourceAnswerGap, ...] = ()):
    return decode_source_answer_frame(
        json.dumps({"version": "1.2", "blocks": blocks}),
        sources=(SOURCE,),
        prior_gaps=prior,
        frame_id="turn3",
    )


def test_precise_unknown_body_is_the_retained_statement_without_second_copy():
    result = _decode([_block("Source date and rule.", False), _block(UNKNOWN, True)])
    assert result.text == "1. Source date and rule.\n\n2. " + UNKNOWN
    assert len(result.gaps) == 1
    gap = result.gaps[0]
    assert gap.statement == UNKNOWN and gap.source_keys == (SOURCE.key,)
    checklist = _decode([_block("Check the same notice.", False)], (gap,))
    assert UNKNOWN in checklist.text and checklist.gaps == (gap,)


def test_empty_new_declaration_cannot_resolve_a_generic_or_more_precise_prior_scope():
    generic = SourceAnswerGap("old:0", (SOURCE.key,), "Other operators remain unverified.")
    third = _decode([_block(UNKNOWN, True)], (generic,))
    assert len(third.gaps) == 2
    last = _decode([_block("Three-point checklist content.", False)], third.gaps)
    assert UNKNOWN in last.text and generic.statement in last.text
    assert last.gaps == third.gaps


def test_exact_restatement_does_not_duplicate_visible_text_or_replace_prior_identity():
    prior = SourceAnswerGap("old:0", (SOURCE.key,), UNKNOWN)
    result = _decode([_block(UNKNOWN, True)], (prior,))
    assert result.text.count(UNKNOWN) == 1 and result.gaps == (prior,)


@pytest.mark.parametrize("value", [None, 0, 1, "true", [], {}])
def test_invalid_scope_classification_rejected_by_schema_and_decoder(value: object):
    block = _block(UNKNOWN, value)
    with pytest.raises(ValidationError):
        validate({"version": "1.2", "blocks": [block]}, source_answer_frame_schema(1))
    with pytest.raises(SourceAnswerFrameError):
        _decode([block])


def test_missing_classification_is_not_silently_treated_as_fully_supported():
    block = _block(UNKNOWN, False)
    del block["unresolved_scope"]
    with pytest.raises(ValidationError):
        validate({"version": "1.2", "blocks": [block]}, source_answer_frame_schema(1))
    with pytest.raises(SourceAnswerFrameError):
        _decode([block])


def test_scope_without_available_source_binding_is_rejected():
    block = _block(UNKNOWN, True)
    block["source_indices"] = []
    with pytest.raises(SourceAnswerFrameError):
        _decode([block])


def test_captured_scope_is_not_silently_clipped_to_fit_state_bound():
    with pytest.raises(SourceAnswerFrameError):
        _decode([_block("x" * (MAX_GAP_CHARACTERS + 1), True)])


def test_unmarked_prose_is_not_parsed_into_control_or_verified_fact():
    result = _decode([_block(UNKNOWN, False)])
    assert UNKNOWN in result.text and result.gaps == ()
    # A provider can still misclassify the block. The full-text quality gate must
    # catch this; schema validity is deliberately not a semantic truth verdict.


def test_legacy_envelope_does_not_bypass_required_classification():
    with pytest.raises(SourceAnswerFrameError):
        decode_source_answer_frame(
            json.dumps({"version": "1.1", "blocks": [_block(UNKNOWN, True)], "new_gaps": []}),
            sources=(SOURCE,),
            frame_id="new",
        )


def test_scope_count_overflow_rejects_whole_frame_instead_of_dropping_unknowns():
    with pytest.raises(SourceAnswerFrameError) as error:
        _decode([_block(f"Unverified scope {i}.", True) for i in range(MAX_FRAME_GAPS + 1)])
    assert error.value.code == "frame_bound"


def test_new_scope_cannot_shadow_a_prior_scope_identity():
    prior = SourceAnswerGap("turn3:block0", (SOURCE.key,), "Another prior scope.")
    with pytest.raises(SourceAnswerFrameError) as error:
        _decode([_block(UNKNOWN, True)], (prior,))
    assert error.value.code == "gap_identity"


def test_equal_wording_with_different_source_identity_is_not_merged():
    other = FrameSource(SOURCE.url, "b" * 64)
    blocks = [_block(UNKNOWN, True), _block(UNKNOWN, True)]
    blocks[1]["source_indices"] = [1]
    result = decode_source_answer_frame(
        json.dumps({"version": "1.2", "blocks": blocks}),
        sources=(SOURCE, other),
        frame_id="new",
    )
    assert len(result.gaps) == 2
    assert result.gaps[0].source_keys == (SOURCE.key,)
    assert result.gaps[1].source_keys == (other.key,)
