from __future__ import annotations

import ast
import hashlib
import io
import json
import math
import zipfile
from array import array
from pathlib import Path

import pytest

from tools.qwen3_tts_training.core import (
    audio_policy,
    normalize_text,
    pcm16_metrics,
    scene_key,
    split_for_scene,
    text_policy,
)
from tools.qwen3_tts_training.prepare_dataset import (
    ASSETS,
    DEFAULT_BASE_MODEL,
    QWEN_REPOSITORY_COMMIT,
    build_training_bundle,
    discover_pairs,
)


def test_normalizes_layout_without_erasing_japanese_prosody() -> None:
    assert normalize_text("  深い意味は\\nありませんよ……  ") == "深い意味はありませんよ……"
    assert normalize_text(r"深い意味は\\nありませんよ") == "深い意味はありませんよ"
    assert normalize_text("えっ、\nそんなこと――") == "えっ、そんなこと――"


def test_routes_ambiguous_and_adult_transcripts_out_of_automatic_training() -> None:
    assert text_policy("今日は一緒に帰りましょう。").status == "selected"
    assert text_policy("何かあったら、いつでも来て下さい。").status == "selected"
    assert text_policy("んっ、少し待ってください……").status == "review"
    adult = text_policy("そのとびっこは何ですか？")
    assert adult.status == "rejected"
    assert "adult_term" in adult.reasons


def test_scene_split_is_deterministic_and_keeps_adjacent_lines_together() -> None:
    assert scene_key("nen001_003") == "nen001"
    assert split_for_scene(scene_key("nen001_003")) == split_for_scene(scene_key("nen001_099"))
    assert split_for_scene("nen001") == split_for_scene("nen001")


def test_pcm_metrics_detect_clean_audio_and_silence() -> None:
    sample_rate = 24_000
    clean = array(
        "h",
        (
            int(7000 * math.sin(2 * math.pi * 220 * index / sample_rate))
            for index in range(sample_rate * 2)
        ),
    ).tobytes()
    metrics = pcm16_metrics(clean)
    assert metrics.duration_seconds == pytest.approx(2.0)
    assert metrics.clipping_ratio == 0
    assert metrics.rms_dbfs > -20
    assert audio_policy(metrics).status == "selected"

    silent = pcm16_metrics(array("h", [0] * sample_rate).tobytes())
    assert audio_policy(silent).status == "rejected"


def test_discovers_only_exact_speaker_pairs() -> None:
    archive = io.BytesIO()
    with zipfile.ZipFile(archive, "w") as output:
        output.writestr("dataset/nen/nen001_001.opus", b"opus")
        output.writestr("dataset/nen/nen001_001.lab", "こんにちは。")
        output.writestr("dataset/kne/kne001_001.opus", b"child")
        output.writestr("dataset/kne/kne001_001.lab", "こんにちは。")
    archive.seek(0)
    with zipfile.ZipFile(archive) as source:
        pairs = discover_pairs(source, speaker="nen")
    assert [pair.stem for pair in pairs] == ["nen001_001"]


def test_builds_reproducible_local_only_colab_bundle(tmp_path: Path) -> None:
    archive = tmp_path / "source.zip"
    clean_stems = _stems_for_split_coverage()
    labels = {
        clean_stems[0]: "今日は一緒に帰りましょう。",
        clean_stems[1]: "明日も会えると嬉しいです。",
        clean_stems[2]: "もう少しだけお話ししませんか？",
        "nen900_001": "んっ、少し待ってください……",
        "nen901_001": "そのとびっこは何ですか？",
    }
    with zipfile.ZipFile(archive, "w") as output:
        for stem, label in labels.items():
            output.writestr(f"root/nen/{stem}.opus", stem.encode())
            output.writestr(f"root/nen/{stem}.lab", label)

    clean_pcm = _sine_pcm(duration_seconds=2.0)
    output = tmp_path / "nene-qwen3-0.6b-pilot"
    first = build_training_bundle(
        archive=archive,
        output=output,
        decoder=lambda _: clean_pcm,
        jobs=2,
    )
    assert first.selected_count == 3
    assert first.review_count == 1
    assert first.rejected_count == 1
    assert (output / "train_qwen3_tts_colab.ipynb").is_file()
    assert len(list((output / "source_audio").glob("*.opus"))) == 3
    manifest = json.loads((output / "bundle_manifest.json").read_text(encoding="utf-8"))
    assert manifest["qwen"]["default_base_model"] == DEFAULT_BASE_MODEL
    assert manifest["qwen"]["default_training_mode"] == "pilot"
    assert manifest["qwen"]["default_max_steps"] == 200
    assert manifest["qwen"]["default_attention_implementation"] == "sdpa"
    assert first.bundle_archive.name == "nene-qwen3-0.6b-pilot.zip"
    first_hash = hashlib.sha256(first.bundle_archive.read_bytes()).hexdigest()

    second = build_training_bundle(
        archive=archive,
        output=output,
        decoder=lambda _: clean_pcm,
        jobs=2,
        force=True,
    )
    second_hash = hashlib.sha256(second.bundle_archive.read_bytes()).hexdigest()
    assert first_hash == second_hash


def test_colab_notebook_is_pinned_and_all_code_cells_parse() -> None:
    notebook = json.loads((ASSETS / "train_qwen3_tts_colab.ipynb").read_text(encoding="utf-8"))
    source = "\n".join("".join(cell.get("source", [])) for cell in notebook["cells"])
    assert QWEN_REPOSITORY_COMMIT in source
    assert 'MODEL_SIZE = "0.6B"' in source
    assert "Qwen/Qwen3-TTS-12Hz-0.6B-Base" in source
    assert "Qwen/Qwen3-TTS-12Hz-1.7B-Base" in source
    assert "PILOT_MODE = True" in source
    assert "MAX_STEPS = 200 if PILOT_MODE else 0" in source
    assert 'ATTENTION_IMPLEMENTATION = "sdpa"' in source
    assert "qwen3-tts-base-{MODEL_SIZE.lower()}" in source
    assert "nene-qwen3-{MODEL_SIZE.lower()}-{run_mode}-output" in source
    for cell in notebook["cells"]:
        if cell.get("cell_type") == "code":
            ast.parse("".join(cell.get("source", [])))


def test_training_driver_contains_upstream_regression_fixes() -> None:
    driver = (ASSETS / "scripts" / "sft_12hz_chatwaifu.py").read_text(encoding="utf-8")
    assert "model.talker.text_projection(" in driver
    assert "labels=codec_0_labels[:, 1:]" in driver
    assert "speaker_encoder.requires_grad_(False)" in driver


def _sine_pcm(*, duration_seconds: float) -> bytes:
    sample_rate = 24_000
    return array(
        "h",
        (
            int(5000 * math.sin(2 * math.pi * 220 * index / sample_rate))
            for index in range(int(sample_rate * duration_seconds))
        ),
    ).tobytes()


def _stems_for_split_coverage() -> tuple[str, str, str]:
    by_split: dict[str, str] = {}
    for index in range(1, 1000):
        scene = f"nen{index:03d}"
        by_split.setdefault(split_for_scene(scene), f"{scene}_001")
        if set(by_split) == {"train", "validation", "test"}:
            return by_split["train"], by_split["validation"], by_split["test"]
    raise AssertionError("Could not find deterministic split coverage")
