# ruff: noqa: RUF001
"""Unit and integration tests for tools/audit_character_facts.py (Q02)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from tools.audit_character_facts import (
    DEFAULT_FIXTURES_PATH,
    compute_file_hash,
    main,
    match_pattern,
    run_fact_audit,
)

_VALID_USER_TEXT = (
    "关于电子设备中提到的移动电源，乘坐国内航班时有哪些具体的民航携带规定？请详细列出三条明确规则。"
)


def _make_sample_record(
    *,
    scenario_id: str = "detailed_answer",
    turn_id: int = 2,
    repeat_index: int = 0,
    variant: str = "baseline",
    sample_key: str | None = None,
    user_text: str = _VALID_USER_TEXT,
    raw_reply: str = "普通回复，未提及相关法规。",
    model: str = "test-model",
    provider: str = "test-provider",
    extra_fields: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Creates a sample record matching results.jsonl schema."""
    if sample_key is None:
        sample_key = f"{scenario_id}:r{repeat_index}:t{turn_id}:{variant}"

    record: dict[str, Any] = {
        "sample_key": sample_key,
        "scenario_id": scenario_id,
        "scenario_name": "明确要求详细回答",
        "turn_id": turn_id,
        "repeat_index": repeat_index,
        "variant": variant,
        "persona_hash": "test_hash",
        "provider": provider,
        "model": model,
        "presentation_profile": "instant_message",
        "user_text": user_text,
        "raw_reply": raw_reply,
        "latency_ms": 1200,
        "tokens_prompt": 100,
        "tokens_completion": 50,
        "finish_reason": "stop",
        "timestamp": "2026-09-30T00:00:00.000000+00:00",
    }
    if extra_fields:
        record.update(extra_fields)
    return record


def _write_jsonl(path: Path, records: list[dict[str, Any]]) -> Path:
    """Writes records as a JSONL file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        for r in records:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    return path


def test_both_concepts_present(tmp_path: Path) -> None:
    """Target reply mentioning both 3C and recalled models passes coverage."""
    reply = (
        "国内航班携带充电宝有明确规定：\n"
        "1. 必须随身携带，严禁托运；\n"
        "2. 自2025年6月28日起，严禁携带没有3C认证标志或3C标志不清晰的充电宝乘机；\n"
        "3. 被国家市场监管部门通报的召回型号或召回批次充电宝一律禁止携带登机；\n"
        "4. 额定能量不超过100Wh，超过160Wh严禁携带。"
    )
    record = _make_sample_record(raw_reply=reply)
    results_file = _write_jsonl(tmp_path / "results.jsonl", [record])

    report = run_fact_audit([results_file], DEFAULT_FIXTURES_PATH)

    assert report["summary"]["total_target_samples"] == 1
    assert report["summary"]["fully_covered_samples"] == 1
    assert report["summary"]["missing_target_samples"] == 0
    assert report["summary"]["coverage_rate"] == 1.0
    assert report["summary"]["check_passed"] is True

    sample = report["samples"][0]
    assert sample["covered"] is True
    assert sample["concept_covered"] is True
    assert sample["concept_coverage"]["ccc_certification"] is True
    assert sample["concept_coverage"]["recalled_models_batches"] is True
    assert sample["missing_concepts"] == []


def test_only_one_concept_present(tmp_path: Path) -> None:
    """Target reply mentioning only one concept fails full coverage."""
    # Case A: Mentions 3C but missing recall
    reply_3c_only = (
        "民航携带充电宝规定：\n"
        "1. 必须随身携带，严禁托运；\n"
        "2. 必须具备清晰的3C认证标志，无标志禁止登机；\n"
        "3. 额定能量不得超过100Wh。"
    )
    rec_3c = _make_sample_record(repeat_index=0, raw_reply=reply_3c_only)

    # Case B: Mentions recall but missing 3C
    reply_recall_only = (
        "民航携带充电宝规定：\n"
        "1. 必须随身携带，严禁托运；\n"
        "2. 被厂家或质检部门召回型号或召回批次的充电宝严禁携带乘机；\n"
        "3. 额定能量不得超过100Wh。"
    )
    rec_recall = _make_sample_record(repeat_index=1, raw_reply=reply_recall_only)

    results_file = _write_jsonl(tmp_path / "results.jsonl", [rec_3c, rec_recall])
    report = run_fact_audit([results_file], DEFAULT_FIXTURES_PATH)

    assert report["summary"]["total_target_samples"] == 2
    assert report["summary"]["fully_covered_samples"] == 0
    assert report["summary"]["missing_target_samples"] == 2
    assert report["summary"]["coverage_rate"] == 0.0
    assert report["summary"]["check_passed"] is False

    s0 = report["samples"][0]
    assert s0["covered"] is False
    assert s0["concept_coverage"]["ccc_certification"] is True
    assert s0["concept_coverage"]["recalled_models_batches"] is False
    assert s0["missing_concepts"] == ["recalled_models_batches"]

    s1 = report["samples"][1]
    assert s1["covered"] is False
    assert s1["concept_coverage"]["ccc_certification"] is False
    assert s1["concept_coverage"]["recalled_models_batches"] is True
    assert s1["missing_concepts"] == ["ccc_certification"]


def test_both_concepts_missing(tmp_path: Path) -> None:
    """Historical reply missing both concepts is marked as coverage failure."""
    reply = (
        "国内航班携带充电宝规则：\n"
        "1. 严禁放入托运行李，必须随身携带；\n"
        "2. 额定能量不超过100Wh无需审批，100Wh至160Wh需航空公司批准；\n"
        "3. 飞行全程严禁使用充电宝为电子设备充电。"
    )
    record = _make_sample_record(raw_reply=reply)
    results_file = _write_jsonl(tmp_path / "results.jsonl", [record])

    report = run_fact_audit([results_file], DEFAULT_FIXTURES_PATH)

    assert report["summary"]["total_target_samples"] == 1
    assert report["summary"]["fully_covered_samples"] == 0
    assert report["summary"]["missing_target_samples"] == 1
    assert report["summary"]["coverage_rate"] == 0.0

    sample = report["samples"][0]
    assert sample["covered"] is False
    assert sample["concept_coverage"]["ccc_certification"] is False
    assert sample["concept_coverage"]["recalled_models_batches"] is False
    assert set(sample["missing_concepts"]) == {"ccc_certification", "recalled_models_batches"}


def test_ccc_chinese_english_equivalence() -> None:
    """3C/CCC matching accepts Chinese and English equivalent variants."""
    assert match_pattern("3C", "外壳具有清晰的3C认证标志")
    assert match_pattern("3C", "外壳具有清晰的3c标志")
    assert match_pattern("CCC", "充电宝必须通过CCC认证")
    assert match_pattern("CCC", "充电宝必须通过ccc认证")
    assert match_pattern("中国强制认证", "属于国家中国强制认证范围")
    assert match_pattern("强制性产品认证", "必须获得强制性产品认证标志")

    # Word boundary prevents false positives inside unrelated words
    assert not match_pattern("3C", "型号为3CH4的高能电池")
    assert not match_pattern("CCC", "型号为CCC99的设备")


def test_five_volt_wh_cues_flagged_for_human_review_without_automatic_conviction(
    tmp_path: Path,
) -> None:
    """Mentions of 5V flag human review cue without automatically convicting calculation error."""
    reply_with_5v = (
        "国内民航充电宝规定：\n"
        "1. 必须随身携带，严禁托运；\n"
        "2. 额定能量不超过100Wh（常见5V电压下约20000mAh）；\n"
        "3. 标识必须清晰完整。"
    )
    record = _make_sample_record(raw_reply=reply_with_5v)
    results_file = _write_jsonl(tmp_path / "results.jsonl", [record])

    report = run_fact_audit([results_file], DEFAULT_FIXTURES_PATH)
    sample = report["samples"][0]

    assert sample["needs_human_review"] is True
    assert "five_volt_wh_calc" in sample["human_review_cues"]
    assert report["summary"]["human_review_cues_flagged"] == 1

    reply_clean = (
        "国内民航充电宝规定：\n"
        "1. 必须随身携带，严禁托运；\n"
        "2. 额定能量不超过100Wh（按标称电压3.7V换算约27000mAh）；\n"
        "3. 标识必须清晰完整。"
    )
    record_clean = _make_sample_record(repeat_index=1, raw_reply=reply_clean)
    results_clean = _write_jsonl(tmp_path / "results_clean.jsonl", [record_clean])

    report_clean = run_fact_audit([results_clean], DEFAULT_FIXTURES_PATH)
    sample_clean = report_clean["samples"][0]

    assert sample_clean["needs_human_review"] is False
    assert sample_clean["human_review_cues"] == []
    assert report_clean["summary"]["human_review_cues_flagged"] == 0


def test_five_volt_correct_warning_still_flags_review_without_conviction(tmp_path: Path) -> None:
    """A correct warning mentioning 5V flags review without automatic conviction."""
    reply_with_correct_warning = (
        "国内民航充电宝规定：\n"
        "1. 必须随身携带，严禁托运；\n"
        "2. 计算额定能量时请注意按锂电芯标称电压3.7V计算，切记不要用USB输出端5V计算；\n"
        "3. 标识必须清晰完整，禁止携带三无产品。"
    )
    record = _make_sample_record(raw_reply=reply_with_correct_warning)
    results_file = _write_jsonl(tmp_path / "results.jsonl", [record])

    report = run_fact_audit([results_file], DEFAULT_FIXTURES_PATH)
    sample = report["samples"][0]

    assert sample["needs_human_review"] is True
    assert "five_volt_wh_calc" in sample["human_review_cues"]
    assert report["summary"]["human_review_cues_flagged"] == 1
    # Notice it is flagged for review, not marked as a hard syntax or validation error
    assert sample["sample_key"] == "detailed_answer:r0:t2:baseline"


def test_keyword_coverage_proves_mention_not_legal_correctness(tmp_path: Path) -> None:
    """Reverse statement mentioning keywords passes mention coverage but is legally wrong.

    Demonstrates that keyword coverage only proves mention, NOT factual correctness.
    """
    reply_reversed_statement = (
        "国内航班携带充电宝规则：\n"
        "1. 3C和召回不影响登机，即使没有3C或属于召回批次，只要容量小于20000mAh即可带上飞机；\n"
        "2. 额定能量不超过100Wh；\n"
        "3. 必须随身携带。"
    )
    record = _make_sample_record(raw_reply=reply_reversed_statement)
    results_file = _write_jsonl(tmp_path / "results.jsonl", [record])

    report = run_fact_audit([results_file], DEFAULT_FIXTURES_PATH)
    sample = report["samples"][0]

    # Both concepts are mentioned, so keyword coverage passes
    assert sample["concept_coverage"]["ccc_certification"] is True
    assert sample["concept_coverage"]["recalled_models_batches"] is True
    assert sample["covered"] is True

    # But the disclaimer explicitly clarifies that keyword presence proves mention only
    disclaimer = report["audit_metadata"]["disclaimer"]
    assert "Keyword presence proves mention only, not legal correctness" in disclaimer
    assert report["audit_metadata"]["audit_type"] in {
        "fact_coverage_audit",
        "retrospective_fact_coverage_audit",
    }


def test_validation_errors_duplicate_key_same_identity(tmp_path: Path) -> None:
    """Duplicate sample identity within the same file is flagged as a validation error."""
    rec1 = _make_sample_record(repeat_index=0)
    rec2 = _make_sample_record(repeat_index=0)  # Same identity
    results_file = _write_jsonl(tmp_path / "results.jsonl", [rec1, rec2])

    report = run_fact_audit([results_file], DEFAULT_FIXTURES_PATH)

    assert report["summary"]["validation_errors_count"] >= 1
    assert report["summary"]["invalid_count"] >= 1
    dup_errors = [
        e for e in report["validation_errors"] if e["error_type"] == "duplicate_sample_identity"
    ]
    assert len(dup_errors) == 1
    assert dup_errors[0]["sample_key"] == "detailed_answer:r0:t2:baseline"
    assert dup_errors[0]["line_number"] == 2
    assert report["summary"]["check_passed"] is False


def test_validation_errors_invalid_sample_key_format(tmp_path: Path) -> None:
    """sample_key not matching expected format is flagged without leaking raw key content."""
    bad_key_record = _make_sample_record(
        sample_key="INVALID_ARBITRARY_UNVERIFIED_KEY_XYZ",
    )
    results_file = _write_jsonl(tmp_path / "results.jsonl", [bad_key_record])

    report = run_fact_audit([results_file], DEFAULT_FIXTURES_PATH)

    assert report["summary"]["validation_errors_count"] >= 1
    assert report["summary"]["invalid_count"] >= 1
    key_errors = [
        e for e in report["validation_errors"] if e["error_type"] == "invalid_sample_key_format"
    ]
    assert len(key_errors) == 1
    # Untrusted raw key string must NOT be placed in sample_key
    assert key_errors[0]["sample_key"] is None
    assert key_errors[0]["line_number"] == 1
    assert report["summary"]["check_passed"] is False


def test_validation_errors_sample_key_alignment(tmp_path: Path) -> None:
    """Mismatch between sample_key and record metadata is flagged with safe details."""
    bad_key_record = _make_sample_record(
        sample_key="detailed_answer:r0:t1:baseline",
        turn_id=2,
    )
    results_file = _write_jsonl(tmp_path / "results.jsonl", [bad_key_record])

    report = run_fact_audit([results_file], DEFAULT_FIXTURES_PATH)

    assert report["summary"]["validation_errors_count"] >= 1
    align_errors = [
        e for e in report["validation_errors"] if e["error_type"] == "invalid_sample_key_alignment"
    ]
    assert len(align_errors) == 1
    assert align_errors[0]["details"]["mismatched_fields"] == ["turn_id"]
    assert align_errors[0]["sample_key"] == "detailed_answer:r0:t1:baseline"
    assert report["summary"]["check_passed"] is False


def test_validation_errors_user_text_mismatch(tmp_path: Path) -> None:
    """user_text not matching fixture definition is flagged without copying raw text."""
    secret_mismatched_text = "SECRET_UNTRUSTED_USER_TEXT_12345"
    mismatched_record = _make_sample_record(user_text=secret_mismatched_text)
    results_file = _write_jsonl(tmp_path / "results.jsonl", [mismatched_record])

    report = run_fact_audit([results_file], DEFAULT_FIXTURES_PATH)

    assert report["summary"]["validation_errors_count"] >= 1
    text_errors = [
        e for e in report["validation_errors"] if e["error_type"] == "user_text_mismatch"
    ]
    assert len(text_errors) == 1
    assert text_errors[0]["sample_key"] == "detailed_answer:r0:t2:baseline"
    assert text_errors[0]["line_number"] == 1
    # Verify raw secret user_text was not copied into details or message
    report_json_str = json.dumps(report, ensure_ascii=False)
    assert secret_mismatched_text not in report_json_str
    assert report["summary"]["check_passed"] is False


def test_unknown_fixture_key_does_not_copy_untrusted_scenario(tmp_path: Path) -> None:
    secret = "SECRET_UNTRUSTED_SCENARIO_123"
    record = _make_sample_record(scenario_id=secret)
    results_file = _write_jsonl(tmp_path / "results.jsonl", [record])
    report = run_fact_audit([results_file], DEFAULT_FIXTURES_PATH)
    assert report["summary"]["invalid_count"] == 1
    assert secret not in json.dumps(report)
    assert report["validation_errors"][0]["sample_key"] is None


def test_validation_errors_missing_or_invalid_raw_reply(tmp_path: Path) -> None:
    """Missing or non-string raw_reply is flagged and does not count toward coverage."""
    # Case A: Missing raw_reply
    rec_missing = _make_sample_record(repeat_index=0)
    del rec_missing["raw_reply"]

    # Case B: Non-string raw_reply
    rec_non_str = _make_sample_record(repeat_index=1, extra_fields={"raw_reply": 12345})

    results_file = _write_jsonl(tmp_path / "results.jsonl", [rec_missing, rec_non_str])
    report = run_fact_audit([results_file], DEFAULT_FIXTURES_PATH)

    assert report["summary"]["validation_errors_count"] == 2
    assert report["summary"]["invalid_count"] == 2
    assert report["summary"]["total_target_samples"] == 0
    assert report["summary"]["check_passed"] is False

    errs = [
        e for e in report["validation_errors"] if e["error_type"] == "missing_or_invalid_raw_reply"
    ]
    assert len(errs) == 2


def test_invalid_records_do_not_count_as_coverage(tmp_path: Path) -> None:
    """Records failing validation do not get included in audited_samples."""
    bad_record = _make_sample_record(user_text="不匹配的用户问题")
    results_file = _write_jsonl(tmp_path / "results.jsonl", [bad_record])

    report = run_fact_audit([results_file], DEFAULT_FIXTURES_PATH)

    assert report["summary"]["invalid_count"] == 1
    assert report["summary"]["total_target_samples"] == 0
    assert len(report["samples"]) == 0
    assert report["summary"]["check_passed"] is False


def test_multiple_files_aggregation_different_models(tmp_path: Path) -> None:
    """Different models with same sample_key across files are both preserved."""
    rec1 = _make_sample_record(model="model-a", repeat_index=0)
    rec2 = _make_sample_record(model="model-b", repeat_index=0)

    f1 = _write_jsonl(tmp_path / "model_a.jsonl", [rec1])
    f2 = _write_jsonl(tmp_path / "model_b.jsonl", [rec2])

    report = run_fact_audit([f1, f2], DEFAULT_FIXTURES_PATH)

    assert report["summary"]["total_target_samples"] == 2
    assert len(report["samples"]) == 2
    assert report["summary"]["invalid_count"] == 0
    sources = {s["source_file"] for s in report["samples"]}
    assert sources == {str(f1), str(f2)}
    models = {s["model"] for s in report["samples"]}
    assert models == {"model-a", "model-b"}


def test_cross_file_duplicate_identity_rejected(tmp_path: Path) -> None:
    """Same provider + model + sample_key across different files is rejected as duplicate."""
    rec1 = _make_sample_record(provider="test-p", model="test-m", repeat_index=0)
    rec2 = _make_sample_record(provider="test-p", model="test-m", repeat_index=0)

    f1 = _write_jsonl(tmp_path / "f1.jsonl", [rec1])
    f2 = _write_jsonl(tmp_path / "f2.jsonl", [rec2])

    report = run_fact_audit([f1, f2], DEFAULT_FIXTURES_PATH)

    assert report["summary"]["total_target_samples"] == 1
    assert report["summary"]["invalid_count"] == 1
    dup_errors = [
        e for e in report["validation_errors"] if e["error_type"] == "duplicate_sample_identity"
    ]
    assert len(dup_errors) == 1
    assert dup_errors[0]["source_file"] == str(f2)


def test_duplicate_input_files_not_audited_twice(tmp_path: Path) -> None:
    """Specifying the same input file twice triggers validation error and does not re-audit."""
    rec = _make_sample_record(repeat_index=0)
    f1 = _write_jsonl(tmp_path / "f1.jsonl", [rec])

    report = run_fact_audit([f1, f1], DEFAULT_FIXTURES_PATH)

    assert report["summary"]["total_target_samples"] == 1
    assert report["summary"]["invalid_count"] == 1
    dup_file_errs = [
        e for e in report["validation_errors"] if e["error_type"] == "duplicate_input_file"
    ]
    assert len(dup_file_errs) == 1


def test_output_contains_no_raw_reply_or_sensitive_fields(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Generated JSON report and stdout do not leak raw replies, user texts, or secrets."""
    secret_key = "SECRET_UNTRUSTED_KEY_VAL_8888"
    secret_user = "SECRET_USER_PROMPT_9999"
    secret_reply = "SECRET_MODEL_RAW_PROSE_7777"
    secret_cred = "SECRET_API_KEY_TOKEN_6666"

    # Line 1: Valid sample with secrets in reply and extra fields
    rec_valid = _make_sample_record(
        repeat_index=0,
        raw_reply=f"普通回复但包含敏感词 {secret_reply}",
        extra_fields={
            "api_key": secret_cred,
            "provider_tokens_reasoning": 300,
        },
    )
    # Line 2: Invalid JSON containing secret
    # Line 3: User text mismatch containing secret
    rec_user_mismatch = _make_sample_record(
        repeat_index=1,
        user_text=secret_user,
    )
    # Line 4: Invalid key format containing secret
    rec_bad_key = _make_sample_record(
        repeat_index=2,
        sample_key=secret_key,
    )

    results_file = tmp_path / "results_sensitive.jsonl"
    with results_file.open("w", encoding="utf-8") as f:
        f.write(json.dumps(rec_valid, ensure_ascii=False) + "\n")
        f.write('{"corrupted_json": true, "leak": "SECRET_CORRUPTED_JSON_5555"\n')
        f.write(json.dumps(rec_user_mismatch, ensure_ascii=False) + "\n")
        f.write(json.dumps(rec_bad_key, ensure_ascii=False) + "\n")

    report_file = tmp_path / "audit_report.json"

    exit_code = main(
        [
            "--results",
            str(results_file),
            "--output",
            str(report_file),
        ]
    )
    assert exit_code == 0
    assert report_file.exists()

    report_text = report_file.read_text(encoding="utf-8")
    report_json: dict[str, Any] = json.loads(report_text)

    # Assert no secrets in report file
    for secret in (
        secret_key,
        secret_user,
        secret_reply,
        secret_cred,
        "SECRET_CORRUPTED_JSON_5555",
    ):
        assert secret not in report_text

    # Test stdout as well
    capsys.readouterr()  # clear buffer
    main(["--results", str(results_file)])
    captured = capsys.readouterr()
    for secret in (
        secret_key,
        secret_user,
        secret_reply,
        secret_cred,
        "SECRET_CORRUPTED_JSON_5555",
    ):
        assert secret not in captured.out

    # Verify samples contain only non-sensitive audit status
    for s in report_json["samples"]:
        assert "raw_reply" not in s
        assert "user_text" not in s
        assert "api_key" not in s


def test_check_flag_exit_code_and_report_persistence(tmp_path: Path) -> None:
    """--check exits non-zero on coverage failure but always preserves the report."""
    # Truly missing both 3C and recall
    failing_record = _make_sample_record(
        raw_reply="国内航班携带充电宝规则：必须随身携带，严禁托运，额定能量不超过100Wh，飞行全程严禁使用。"
    )
    results_file = _write_jsonl(tmp_path / "failing_results.jsonl", [failing_record])
    report_file = tmp_path / "report_on_failure.json"

    exit_code = main(
        [
            "--results",
            str(results_file),
            "--output",
            str(report_file),
            "--check",
        ]
    )

    # Exited non-zero due to --check failure
    assert exit_code != 0
    # Crucially, report was still written and contains valid data
    assert report_file.exists()
    saved_report = json.loads(report_file.read_text(encoding="utf-8"))
    assert saved_report["summary"]["check_passed"] is False
    assert saved_report["summary"]["missing_target_samples"] == 1

    # Now test passing case with --check
    passing_record = _make_sample_record(
        raw_reply="严格要求3C认证标识，同时严禁携带召回型号充电宝登机。"
    )
    passing_file = _write_jsonl(tmp_path / "passing_results.jsonl", [passing_record])
    passing_report_file = tmp_path / "report_on_pass.json"

    pass_exit_code = main(
        [
            "--results",
            str(passing_file),
            "--output",
            str(passing_report_file),
            "--check",
        ]
    )
    assert pass_exit_code == 0
    assert passing_report_file.exists()
    saved_pass_report = json.loads(passing_report_file.read_text(encoding="utf-8"))
    assert saved_pass_report["summary"]["check_passed"] is True


def test_retrospective_audit_metadata_distinction(tmp_path: Path) -> None:
    """Report distinguishes retrospective vs non-retrospective disclaimer."""
    record = _make_sample_record(raw_reply="普通回复")
    results_file = _write_jsonl(tmp_path / "results.jsonl", [record])

    # Case A: retrospective=True (default)
    report_retro = run_fact_audit([results_file], DEFAULT_FIXTURES_PATH, retrospective=True)
    meta_retro = report_retro["audit_metadata"]
    assert meta_retro["retrospective_audit"] is True
    assert meta_retro["audit_type"] == "retrospective_fact_coverage_audit"
    assert "Retrospective audit explicitly notes" in meta_retro["disclaimer"]

    # Case B: retrospective=False
    report_current = run_fact_audit([results_file], DEFAULT_FIXTURES_PATH, retrospective=False)
    meta_current = report_current["audit_metadata"]
    assert meta_current["retrospective_audit"] is False
    assert meta_current["audit_type"] == "fact_coverage_audit"
    assert "Retrospective audit" not in meta_current["disclaimer"]


def test_source_snapshot_caac_metadata_accuracy(tmp_path: Path) -> None:
    """Source snapshot in fixture and report contains accurate CAAC metadata."""
    record = _make_sample_record(raw_reply="普通回复")
    results_file = _write_jsonl(tmp_path / "results.jsonl", [record])

    report = run_fact_audit([results_file], DEFAULT_FIXTURES_PATH)
    meta = report["audit_metadata"]
    snapshot = report["source_snapshot"]

    assert meta["effective_date"] == "2025-06-28"
    assert meta["verified_date"] == "2026-09-30"
    assert meta["snapshot_valid_as_of"] == "2026-09-30"
    assert meta["current_fixtures_hash"] == compute_file_hash(DEFAULT_FIXTURES_PATH)

    sources = snapshot["sources"]
    assert len(sources) == 2
    # Verify corrected real title of 2025 notice
    assert sources[0]["title"] == "民航局：禁止旅客携带无3C标识及被召回的充电宝乘坐境内航班"
    assert sources[0]["effective_date"] == "2025-06-28"

    # Verify old notice effective date is unknown, not assumed
    assert sources[1]["effective_date"] == "unknown"

    # Verify scope note clarifying selected audit sources
    assert "scope_note" in snapshot
    assert "仅覆盖" in snapshot["scope_note"]
    assert "expected_user_text" not in snapshot
    assert report["summary"]["factual_correctness"] == "not_assessed"
    assert report["summary"]["release_approved"] is False


def test_fixture_validation_failures(tmp_path: Path) -> None:
    """Fixture empty, malformed, or missing fact specs fails CLI and preserves report."""
    rec = _make_sample_record()
    results_file = _write_jsonl(tmp_path / "results.jsonl", [rec])

    # Case A: Empty fixture
    empty_fixture = tmp_path / "empty_fixture.json"
    empty_fixture.write_text("[]", encoding="utf-8")
    report_a = tmp_path / "report_empty.json"

    exit_a = main(
        [
            "--results",
            str(results_file),
            "--fixture",
            str(empty_fixture),
            "--output",
            str(report_a),
        ]
    )
    assert exit_a != 0
    assert report_a.exists()
    data_a = json.loads(report_a.read_text(encoding="utf-8"))
    assert any(e["error_type"] == "empty_fixture" for e in data_a["validation_errors"])

    # Case B: Fixture with scenarios but no fact specs
    no_specs_fixture = tmp_path / "no_specs.json"
    no_specs_fixture.write_text(
        json.dumps([{"id": "dummy", "turns": [{"turn_id": 1, "user_text": "hello"}]}]),
        encoding="utf-8",
    )
    report_b = tmp_path / "report_no_specs.json"

    exit_b = main(
        [
            "--results",
            str(results_file),
            "--fixture",
            str(no_specs_fixture),
            "--output",
            str(report_b),
        ]
    )
    assert exit_b != 0
    assert report_b.exists()
    data_b = json.loads(report_b.read_text(encoding="utf-8"))
    assert any(e["error_type"] == "no_fact_specs" for e in data_b["validation_errors"])


def test_adjacent_metadata_fixtures_hash_extraction(tmp_path: Path) -> None:
    """Reads original fixtures hash from adjacent metadata.json without copying full metadata."""
    rec = _make_sample_record(repeat_index=0)
    res_dir = tmp_path / "run_eval"
    res_dir.mkdir(parents=True)
    results_file = _write_jsonl(res_dir / "results.jsonl", [rec])

    metadata_file = res_dir / "metadata.json"
    metadata_file.write_text(
        json.dumps(
            {
                "identity": {
                    "tool": "evaluate_character_scenarios",
                    "fixtures_hash": "original_hex_1234",
                    "cost_policy": "secret_internal_cost_details",
                }
            }
        ),
        encoding="utf-8",
    )

    report = run_fact_audit([results_file], DEFAULT_FIXTURES_PATH)
    assert len(report["input_files"]) == 1
    entry = report["input_files"][0]
    assert entry["path"] == str(results_file)
    assert entry["file_hash"] == compute_file_hash(results_file)
    assert entry["original_fixtures_hash"] == "original_hex_1234"
    # Ensure full metadata was NOT copied
    assert "cost_policy" not in entry
    assert "secret_internal_cost_details" not in json.dumps(report)
