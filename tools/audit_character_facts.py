"""Audit fact coverage and manual review cues in evaluated character scenarios (Q02).

This tool performs an independent, offline fact coverage audit on evaluated scenario
`results.jsonl` files against the source snapshot metadata defined in
`character_scenarios.json`.

Key principles:
- Only reads samples from local files; does not call models, access credentials,
  or perform network requests.
- Validates sample_key, scenario/turn, user_text, and raw_reply against fixture definitions.
- Distinguishes fact coverage audit from persona preference blind judging.
- Hitting keywords proves mention only, not legal correctness.
- Missing required concepts constitutes coverage failure.
- Suspicious cues (e.g. 5V/Wh calculation) are flagged for human review without
  relying on fragile regex to automatically convict physical calculation errors.
- Output JSON contains auditable source snapshot metadata, sample keys, and
  non-sensitive audit status; it never copies raw model replies, user text, or unverified input.
- Supports retrospective audits on historical runs, explicitly distinguished by parameter.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

DEFAULT_FIXTURES_PATH = (
    Path(__file__).resolve().parents[1]
    / "tests"
    / "fixtures"
    / "conversation"
    / "character_scenarios.json"
)

_SAMPLE_KEY_REGEX = re.compile(
    r"^(?P<scenario>[a-zA-Z0-9_-]+):r(?P<repeat>\d+):t(?P<turn>\d+):(?P<variant>[a-zA-Z0-9_-]+)$"
)


def compute_file_hash(path: Path) -> str:
    """Computes SHA-256 hexdigest truncated to 16 characters.

    Consistent with evaluate_character_scenarios.
    """
    if not path.exists():
        return "missing"
    return hashlib.sha256(path.read_bytes()).hexdigest()[:16]


def _as_str(val: object, default: str = "") -> str:
    return val if isinstance(val, str) else default


def _as_int(val: object, default: int = 0) -> int:
    return val if isinstance(val, int) else default


def _as_dict(val: object) -> dict[str, Any] | None:
    if isinstance(val, dict):
        return cast(dict[str, Any], val)
    return None


def _as_list(val: object) -> list[Any] | None:
    if isinstance(val, list):
        return cast(list[Any], val)
    return None


def _as_str_list(val: object) -> list[str]:
    if isinstance(val, list):
        items = cast(list[object], val)
        return [str(x) for x in items if isinstance(x, str)]
    return []


@dataclass(frozen=True, slots=True)
class FactSource:
    title: str
    url: str
    effective_date: str
    verified_date: str
    notes: str = ""


@dataclass(frozen=True, slots=True)
class ConceptDefinition:
    id: str
    name: str
    description: str
    patterns: list[str]


@dataclass(frozen=True, slots=True)
class HumanReviewCueDefinition:
    id: str
    name: str
    description: str
    patterns: list[str]


@dataclass(frozen=True, slots=True)
class TurnFactSpec:
    scenario_id: str
    turn_id: int
    title: str
    effective_date: str
    verified_date: str
    snapshot_valid_as_of: str
    scope_note: str
    sources: list[FactSource]
    required_concepts: list[ConceptDefinition]
    human_review_cues: list[HumanReviewCueDefinition]


@dataclass(frozen=True, slots=True)
class ValidationError:
    error_type: str
    message: str
    line_number: int | None = None
    sample_key: str | None = None
    source_file: str | None = None
    details: dict[str, Any] = field(default_factory=dict[str, Any])


def _is_regulatory_spec(spec: TurnFactSpec) -> bool:
    """Keep this regulatory audit scoped when technical source snapshots coexist."""
    return any(
        concept.id in {"ccc_certification", "recalled_models_batches"}
        for concept in spec.required_concepts
    )


def match_pattern(pattern: str, text: str) -> bool:
    """Matches a concept or cue pattern against visible text.

    Supports:
    - Alphanumeric patterns (e.g. '3C', 'CCC', '5V') with word/character boundaries
      so that '3C' matches in Chinese contexts ('3C认证') but does not match inside
      longer alphanumeric words (e.g. '3CH4').
    - Spaced patterns like '5 v' / '5 V'.
    - Chinese substring and phrase matching.
    """
    pat = pattern.strip()
    if not pat or not text:
        return False

    # Normalize patterns with internal whitespace like '5 v'
    if pat.lower() in {"5 v", "5v"}:
        return bool(re.search(r"(?i)(?<![a-zA-Z0-9])5\s*v(?![a-zA-Z0-9])", text))

    if re.fullmatch(r"[a-zA-Z0-9]+", pat):
        rx = rf"(?i)(?<![a-zA-Z0-9]){re.escape(pat)}(?![a-zA-Z0-9])"
        return bool(re.search(rx, text))

    return bool(re.search(re.escape(pat), text, re.IGNORECASE))


def load_fact_specs(
    fixtures_path: Path,
) -> tuple[dict[tuple[str, int], TurnFactSpec], dict[tuple[str, int], str], list[ValidationError]]:
    """Loads fact specifications and turn user_texts from the scenarios fixture.

    Returns:
        specs: map of (scenario_id, turn_id) -> TurnFactSpec for turns with source_snapshot.
        fixture_turns: map of (scenario_id, turn_id) -> user_text for all fixture turns.
        errors: list of ValidationError if fixture file is missing, invalid, or empty.
    """
    errors: list[ValidationError] = []
    specs: dict[tuple[str, int], TurnFactSpec] = {}
    fixture_turns: dict[tuple[str, int], str] = {}

    if not fixtures_path.exists():
        errors.append(
            ValidationError(
                error_type="fixture_not_found",
                message="Fixture file does not exist",
                source_file=str(fixtures_path),
            )
        )
        return specs, fixture_turns, errors

    try:
        raw_json: object = json.loads(fixtures_path.read_text(encoding="utf-8"))
    except Exception:
        errors.append(
            ValidationError(
                error_type="fixture_json_error",
                message="Fixture file is not valid JSON",
                source_file=str(fixtures_path),
            )
        )
        return specs, fixture_turns, errors

    raw_scenarios = _as_list(raw_json)
    if raw_scenarios is None:
        errors.append(
            ValidationError(
                error_type="fixture_shape_error",
                message="Fixture root must be a list of scenario objects",
                source_file=str(fixtures_path),
            )
        )
        return specs, fixture_turns, errors

    if not raw_scenarios:
        errors.append(
            ValidationError(
                error_type="empty_fixture",
                message="Fixture contains no scenarios",
                source_file=str(fixtures_path),
            )
        )
        return specs, fixture_turns, errors

    for scenario_item in raw_scenarios:
        scenario = _as_dict(scenario_item)
        if scenario is None:
            continue
        scenario_id = _as_str(scenario.get("id"))
        if not scenario_id:
            continue
        raw_turns = _as_list(scenario.get("turns")) or []

        for turn_item in raw_turns:
            turn = _as_dict(turn_item)
            if turn is None:
                continue
            turn_id = _as_int(turn.get("turn_id"), default=-1)
            user_text = _as_str(turn.get("user_text"))
            if turn_id <= 0:
                continue

            fixture_turns[(scenario_id, turn_id)] = user_text

            source_snapshot = _as_dict(turn.get("source_snapshot"))
            if source_snapshot is None:
                continue

            sources: list[FactSource] = []
            for src_item in _as_list(source_snapshot.get("sources")) or []:
                src = _as_dict(src_item)
                if src is not None:
                    sources.append(
                        FactSource(
                            title=_as_str(src.get("title")),
                            url=_as_str(src.get("url")),
                            effective_date=_as_str(src.get("effective_date")),
                            verified_date=_as_str(src.get("verified_date")),
                            notes=_as_str(src.get("notes")),
                        )
                    )

            required_concepts: list[ConceptDefinition] = []
            for req_item in _as_list(source_snapshot.get("required_concepts")) or []:
                req = _as_dict(req_item)
                if req is not None:
                    required_concepts.append(
                        ConceptDefinition(
                            id=_as_str(req.get("id")),
                            name=_as_str(req.get("name")),
                            description=_as_str(req.get("description")),
                            patterns=_as_str_list(req.get("patterns")),
                        )
                    )

            human_review_cues: list[HumanReviewCueDefinition] = []
            for cue_item in _as_list(source_snapshot.get("human_review_cues")) or []:
                cue = _as_dict(cue_item)
                if cue is not None:
                    human_review_cues.append(
                        HumanReviewCueDefinition(
                            id=_as_str(cue.get("id")),
                            name=_as_str(cue.get("name")),
                            description=_as_str(cue.get("description")),
                            patterns=_as_str_list(cue.get("patterns")),
                        )
                    )

            specs[(scenario_id, turn_id)] = TurnFactSpec(
                scenario_id=scenario_id,
                turn_id=turn_id,
                title=_as_str(source_snapshot.get("title")),
                effective_date=_as_str(source_snapshot.get("effective_date")),
                verified_date=_as_str(source_snapshot.get("verified_date")),
                snapshot_valid_as_of=_as_str(source_snapshot.get("snapshot_valid_as_of")),
                scope_note=_as_str(source_snapshot.get("scope_note")),
                sources=sources,
                required_concepts=required_concepts,
                human_review_cues=human_review_cues,
            )

    if not specs:
        errors.append(
            ValidationError(
                error_type="no_fact_specs",
                message="Fixture does not contain any turn fact specifications",
                source_file=str(fixtures_path),
            )
        )

    return specs, fixture_turns, errors


def validate_sample_record(
    record: dict[str, Any],
    source_file: Path,
    line_number: int,
    fixture_turns: dict[tuple[str, int], str],
) -> tuple[list[ValidationError], str | None]:
    """Validates a sample record from results.jsonl against alignment rules.

    Does NOT copy raw untrusted inputs (e.g. unverified keys, arbitrary user text)
    into error messages or details.
    """
    errors: list[ValidationError] = []
    file_str = str(source_file)

    sample_key_raw = record.get("sample_key")
    if not isinstance(sample_key_raw, str) or not sample_key_raw.strip():
        errors.append(
            ValidationError(
                error_type="missing_sample_key",
                message="Sample record is missing a valid sample_key",
                line_number=line_number,
                source_file=file_str,
            )
        )
        return errors, None

    sample_key = sample_key_raw.strip()
    match = _SAMPLE_KEY_REGEX.match(sample_key)
    if not match:
        errors.append(
            ValidationError(
                error_type="invalid_sample_key_format",
                message=(
                    "sample_key does not match expected format "
                    "'<scenario>:r<repeat>:t<turn>:<variant>'"
                ),
                line_number=line_number,
                source_file=file_str,
            )
        )
        return errors, None

    key_scenario = match.group("scenario")
    key_repeat = int(match.group("repeat"))
    key_turn = int(match.group("turn"))
    key_variant = match.group("variant")
    safe_key = sample_key if (key_scenario, key_turn) in fixture_turns else None

    rec_scenario = record.get("scenario_id")
    rec_repeat = record.get("repeat_index")
    rec_turn = record.get("turn_id")
    rec_variant = record.get("variant")

    mismatched_fields: list[str] = []
    if rec_scenario != key_scenario:
        mismatched_fields.append("scenario_id")
    if rec_repeat != key_repeat:
        mismatched_fields.append("repeat_index")
    if rec_turn != key_turn:
        mismatched_fields.append("turn_id")
    if rec_variant != key_variant:
        mismatched_fields.append("variant")

    if mismatched_fields:
        errors.append(
            ValidationError(
                error_type="invalid_sample_key_alignment",
                message="sample_key internal fields do not match record attributes",
                line_number=line_number,
                sample_key=safe_key,
                source_file=file_str,
                details={"mismatched_fields": mismatched_fields},
            )
        )

    raw_reply_val = record.get("raw_reply")
    if raw_reply_val is None or not isinstance(raw_reply_val, str):
        errors.append(
            ValidationError(
                error_type="missing_or_invalid_raw_reply",
                message="raw_reply is missing or not a string",
                line_number=line_number,
                sample_key=safe_key,
                source_file=file_str,
            )
        )

    turn_key = (key_scenario, key_turn)
    if turn_key not in fixture_turns:
        errors.append(
            ValidationError(
                error_type="unknown_fixture_turn",
                message="Turn not defined in fixture",
                line_number=line_number,
                sample_key=safe_key,
                source_file=file_str,
            )
        )
    else:
        expected_text = fixture_turns[turn_key]
        actual_text = record.get("user_text")
        if actual_text != expected_text:
            errors.append(
                ValidationError(
                    error_type="user_text_mismatch",
                    message="user_text does not match expected fixture definition",
                    line_number=line_number,
                    sample_key=safe_key,
                    source_file=file_str,
                    details={
                        "field": "user_text",
                        "scenario_id": key_scenario,
                        "turn_id": key_turn,
                    },
                )
            )

    return errors, safe_key


def run_fact_audit(
    results_paths: list[Path],
    fixtures_path: Path = DEFAULT_FIXTURES_PATH,
    retrospective: bool = True,
) -> dict[str, Any]:
    """Runs fact coverage audit on results files against fixture specifications."""
    specs, fixture_turns, fixture_errors = load_fact_specs(fixtures_path)
    target_specs = {turn_key: spec for turn_key, spec in specs.items() if _is_regulatory_spec(spec)}

    validation_errors: list[ValidationError] = list(fixture_errors)
    audited_samples: list[dict[str, Any]] = []
    invalid_count = len(fixture_errors)

    current_fixtures_hash = compute_file_hash(fixtures_path)

    # De-duplicate input files and gather input metadata
    unique_paths: list[Path] = []
    seen_canonical_files: set[Path] = set()
    input_file_entries: list[dict[str, Any]] = []

    for path in results_paths:
        canonical = path.resolve()
        if canonical in seen_canonical_files:
            validation_errors.append(
                ValidationError(
                    error_type="duplicate_input_file",
                    message="Duplicate input file specified",
                    source_file=str(path),
                )
            )
            invalid_count += 1
            continue
        seen_canonical_files.add(canonical)
        unique_paths.append(path)

        file_entry: dict[str, Any] = {
            "path": str(path),
            "file_hash": compute_file_hash(path),
        }
        meta_json_path = path.parent / "metadata.json"
        if meta_json_path.exists():
            try:
                meta_obj: object = json.loads(meta_json_path.read_text(encoding="utf-8"))
                meta_dict = _as_dict(meta_obj)
                if meta_dict is not None:
                    identity_dict = _as_dict(meta_dict.get("identity"))
                    if identity_dict is not None:
                        orig_hash = _as_str(identity_dict.get("fixtures_hash"))
                        if orig_hash:
                            file_entry["original_fixtures_hash"] = orig_hash
            except Exception:
                pass
        input_file_entries.append(file_entry)

    # A sample key is reused across presentation profiles by the evaluator. Keep
    # the profile in the identity so a combined multi-presentation audit does not
    # mistake the same scenario turn for a duplicate sample.
    seen_identities: set[tuple[str, str, str, str]] = set()

    # If fixture loading had errors, skip scanning results files
    if not fixture_errors:
        for path in unique_paths:
            if not path.exists():
                validation_errors.append(
                    ValidationError(
                        error_type="file_not_found",
                        message="Results file does not exist",
                        source_file=str(path),
                    )
                )
                invalid_count += 1
                continue

            with path.open("r", encoding="utf-8") as f:
                for line_no, line in enumerate(f, start=1):
                    clean_line = line.strip()
                    if not clean_line:
                        continue
                    try:
                        record_obj: object = json.loads(clean_line)
                        record = _as_dict(record_obj)
                        if record is None:
                            raise ValueError("Record is not a valid JSON dictionary")
                    except Exception:
                        validation_errors.append(
                            ValidationError(
                                error_type="json_decode_error",
                                message="Line contains invalid JSON",
                                line_number=line_no,
                                source_file=str(path),
                            )
                        )
                        invalid_count += 1
                        continue

                    line_errors, safe_key = validate_sample_record(
                        record=record,
                        source_file=path,
                        line_number=line_no,
                        fixture_turns=fixture_turns,
                    )
                    if line_errors or safe_key is None:
                        validation_errors.extend(line_errors)
                        invalid_count += 1
                        continue

                    provider = _as_str(record.get("provider"), default="unknown")
                    model = _as_str(record.get("model"), default="unknown")
                    presentation_profile = _as_str(
                        record.get("presentation_profile"), default="unknown"
                    )
                    identity = (provider, model, presentation_profile, safe_key)

                    if identity in seen_identities:
                        validation_errors.append(
                            ValidationError(
                                error_type="duplicate_sample_identity",
                                message="Duplicate sample identity encountered",
                                line_number=line_no,
                                sample_key=safe_key,
                                source_file=str(path),
                                details={
                                    "provider": provider,
                                    "model": model,
                                    "presentation_profile": presentation_profile,
                                },
                            )
                        )
                        invalid_count += 1
                        continue
                    seen_identities.add(identity)

                    scenario_id = _as_str(record.get("scenario_id"))
                    turn_id = _as_int(record.get("turn_id"), default=-1)
                    turn_key = (scenario_id, turn_id)
                    if turn_key not in target_specs:
                        # Non-target turn, skipped from fact coverage audit
                        continue

                    spec = target_specs[turn_key]
                    raw_reply = _as_str(record.get("raw_reply"))

                    # Check required concepts
                    concept_results: dict[str, bool] = {}
                    for concept in spec.required_concepts:
                        is_present = any(match_pattern(pat, raw_reply) for pat in concept.patterns)
                        concept_results[concept.id] = is_present

                    covered = all(concept_results.values()) if concept_results else False
                    missing_concepts = [
                        cid for cid, is_present in concept_results.items() if not is_present
                    ]

                    # Check human review cues (e.g. 5V/Wh calculation confusion)
                    triggered_cues: list[str] = []
                    for cue in spec.human_review_cues:
                        if any(match_pattern(pat, raw_reply) for pat in cue.patterns):
                            triggered_cues.append(cue.id)

                    needs_human_review = len(triggered_cues) > 0

                    audited_samples.append(
                        {
                            "sample_key": safe_key,
                            "source_file": str(path),
                            "scenario_id": scenario_id,
                            "turn_id": turn_id,
                            "repeat_index": _as_int(record.get("repeat_index")),
                            "variant": _as_str(record.get("variant")),
                            "model": model,
                            "provider": provider,
                            "presentation_profile": presentation_profile,
                            "concept_covered": covered,
                            "covered": covered,
                            "concept_coverage": concept_results,
                            "missing_concepts": missing_concepts,
                            "needs_human_review": needs_human_review,
                            "human_review_cues": triggered_cues,
                        }
                    )

    # Compute summary aggregates
    total_target_samples = len(audited_samples)
    fully_covered_samples = sum(1 for s in audited_samples if s["covered"])
    missing_target_samples = total_target_samples - fully_covered_samples
    coverage_rate = (
        round(fully_covered_samples / total_target_samples, 4) if total_target_samples > 0 else 0.0
    )
    human_review_cues_flagged = sum(1 for s in audited_samples if s["needs_human_review"])

    # Aggregate breakdown by concept and cue across all specs
    concept_breakdown: dict[str, dict[str, Any]] = {}
    human_review_breakdown: dict[str, dict[str, Any]] = {}

    for spec in target_specs.values():
        for concept in spec.required_concepts:
            cov_cnt = sum(
                1 for s in audited_samples if s["concept_coverage"].get(concept.id, False)
            )
            concept_breakdown[concept.id] = {
                "name": concept.name,
                "covered": cov_cnt,
                "missing": total_target_samples - cov_cnt,
            }
        for cue in spec.human_review_cues:
            flag_cnt = sum(1 for s in audited_samples if cue.id in s["human_review_cues"])
            human_review_breakdown[cue.id] = {
                "name": cue.name,
                "flagged": flag_cnt,
            }

    check_passed = (
        total_target_samples > 0
        and missing_target_samples == 0
        and len(validation_errors) == 0
        and invalid_count == 0
    )

    # The audit targets the CAAC regulatory snapshot. Do not rely on fixture
    # insertion order now that technical-source snapshots can coexist with it.
    primary_spec = next(iter(target_specs.values()), None)

    if retrospective:
        disclaimer = (
            "Fact coverage audit verifies presence of required regulatory concepts as of "
            "the 2026-09-30 source snapshot. Keyword presence proves mention only, not legal "
            "correctness. Absence indicates coverage failure. Human review cues (e.g. 5V/Wh "
            "calculation) flag items for manual inspection without automatic conviction. "
            "Retrospective audit explicitly notes that historical evaluations ran prior to "
            "the addition of this source snapshot standard."
        )
    else:
        disclaimer = (
            "Fact coverage audit verifies presence of required regulatory concepts as of "
            "the 2026-09-30 source snapshot. Keyword presence proves mention only, not legal "
            "correctness. Absence indicates coverage failure. Human review cues (e.g. 5V/Wh "
            "calculation) flag items for manual inspection without automatic conviction."
        )

    report: dict[str, Any] = {
        "audit_metadata": {
            "audit_type": (
                "retrospective_fact_coverage_audit" if retrospective else "fact_coverage_audit"
            ),
            "retrospective_audit": retrospective,
            "audit_timestamp": datetime.now(UTC).isoformat(),
            "audit_scope": (
                f"{primary_spec.scenario_id}:turn_{primary_spec.turn_id}:fact_coverage"
                if primary_spec
                else "none"
            ),
            "fixture_path": str(fixtures_path),
            "current_fixtures_hash": current_fixtures_hash,
            "effective_date": primary_spec.effective_date if primary_spec else "",
            "verified_date": primary_spec.verified_date if primary_spec else "",
            "snapshot_valid_as_of": primary_spec.snapshot_valid_as_of if primary_spec else "",
            "metadata_policy": "independent_audit_snapshot_does_not_modify_original_run_metadata",
            "disclaimer": disclaimer,
        },
        # Keep the long-standing `source_snapshot` object shape for the
        # regulatory audit target. Additional technical snapshots are exposed
        # separately instead of changing the primary report type by fixture order.
        "source_snapshot": asdict(primary_spec) if primary_spec is not None else None,
        "source_snapshots": [asdict(s) for s in specs.values()],
        "input_files": input_file_entries,
        "summary": {
            "factual_correctness": "not_assessed",
            "release_approved": False,
            "total_target_samples": total_target_samples,
            "fully_covered_samples": fully_covered_samples,
            "missing_target_samples": missing_target_samples,
            "coverage_rate": coverage_rate,
            "human_review_cues_flagged": human_review_cues_flagged,
            "concept_breakdown": concept_breakdown,
            "human_review_breakdown": human_review_breakdown,
            "invalid_count": invalid_count,
            "validation_errors_count": len(validation_errors),
            "check_passed": check_passed,
        },
        "validation_errors": [asdict(err) for err in validation_errors],
        "samples": audited_samples,
    }

    return report


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Audit fact coverage and review cues in character scenarios (Q02)."
    )
    parser.add_argument(
        "--results",
        type=Path,
        nargs="+",
        required=True,
        help="One or more paths to results.jsonl files.",
    )
    parser.add_argument(
        "--fixtures",
        "--fixture",
        dest="fixtures",
        type=Path,
        default=DEFAULT_FIXTURES_PATH,
        help="Path to character_scenarios.json fixture.",
    )
    parser.add_argument(
        "--output",
        "-o",
        type=Path,
        default=None,
        help="Path to output JSON audit report.",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="Exit with non-zero code if any concept is missing or validation errors occur.",
    )
    parser.add_argument(
        "--retrospective",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Mark audit as retrospective (default: True).",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_arg_parser()
    args = parser.parse_args(argv)

    report = run_fact_audit(
        results_paths=args.results,
        fixtures_path=args.fixtures,
        retrospective=args.retrospective,
    )

    report_json = json.dumps(report, ensure_ascii=False, indent=2)

    if args.output is not None:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(report_json + "\n", encoding="utf-8")
    else:
        sys.stdout.write(report_json + "\n")

    summary = report.get("summary", {})
    check_passed = bool(summary.get("check_passed", False))
    validation_errors_count = int(summary.get("validation_errors_count", 0))

    # If fixture was invalid or no fact specs exist, CLI fails even without --check
    fixture_error_types = {
        "fixture_not_found",
        "fixture_json_error",
        "fixture_shape_error",
        "empty_fixture",
        "no_fact_specs",
    }
    if validation_errors_count > 0 and any(
        err.get("error_type") in fixture_error_types for err in report.get("validation_errors", [])
    ):
        return 1

    if args.check and not check_passed:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
