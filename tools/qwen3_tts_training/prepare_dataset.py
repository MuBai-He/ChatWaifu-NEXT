"""Build a deterministic, local-only Nene Qwen3-TTS Colab training bundle."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import shutil
import subprocess
import tempfile
import zipfile
from collections import Counter
from collections.abc import Callable, Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass
from pathlib import Path, PurePosixPath

from tools.qwen3_tts_training.core import (
    AudioMetrics,
    audio_policy,
    combine_policy,
    normalize_text,
    pcm16_metrics,
    reference_score,
    scene_key,
    split_for_scene,
    text_policy,
)

SOURCE_DATASET_URL = "https://huggingface.co/datasets/KitsuneX07/Datasets_for_Sabbat_of_the_Witch"
SOURCE_REPOSITORY_URL = (
    "https://github.com/KitsuneX07/Dataset_Maker_for_Galgames/tree/main/"
    "%5Byuzusoft%5D%E9%AD%94%E5%A5%B3%E7%9A%84%E5%A4%9C%E5%AE%B4_"
    "Sabbat_of_the_Witch"
)
QWEN_REPOSITORY_COMMIT = "022e286b98fbec7e1e916cb940cdf532cd9f488e"
BUNDLE_SCHEMA_VERSION = "1.0"
DEFAULT_REFERENCE_STEM = "nen104_084"
ASSETS = Path(__file__).with_name("bundle_assets")

DecodeAudio = Callable[[Path], bytes]


@dataclass(frozen=True, slots=True)
class SourcePair:
    stem: str
    audio_member: str
    label_member: str
    original_text: str
    normalized_text: str


@dataclass(frozen=True, slots=True)
class AnalysedRecord:
    stem: str
    scene: str
    text: str
    source_audio: str
    split: str | None
    status: str
    reasons: tuple[str, ...]
    audio_sha256: str
    metrics: AudioMetrics | None

    def as_json(self) -> dict[str, object]:
        payload: dict[str, object] = {
            "stem": self.stem,
            "scene": self.scene,
            "text": self.text,
            "source_audio": self.source_audio,
            "split": self.split,
            "status": self.status,
            "reasons": list(self.reasons),
            "audio_sha256": self.audio_sha256,
        }
        payload["metrics"] = None if self.metrics is None else asdict(self.metrics)
        return payload


@dataclass(frozen=True, slots=True)
class BuildResult:
    bundle_directory: Path
    bundle_archive: Path
    manifest_path: Path
    selected_count: int
    review_count: int
    rejected_count: int
    duration_hours: float


def build_training_bundle(
    *,
    archive: Path,
    output: Path,
    decoder: DecodeAudio,
    speaker: str = "nen",
    reference_stem: str = DEFAULT_REFERENCE_STEM,
    jobs: int = 4,
    force: bool = False,
) -> BuildResult:
    """Create a conservative source-audio bundle that materializes WAVs in Colab."""

    archive = archive.resolve()
    output = output.resolve()
    if not archive.is_file():
        raise FileNotFoundError(f"Dataset archive does not exist: {archive}")
    if output.exists():
        if not force:
            raise FileExistsError(f"Output already exists: {output}; pass --force to replace it")
        _assert_safe_output(output)
        shutil.rmtree(output)

    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=f".{output.name}-", dir=output.parent) as temporary:
        stage = Path(temporary) / output.name
        work_audio = Path(temporary) / "work-audio"
        stage_audio = stage / "source_audio"
        metadata = stage / "metadata"
        reports = stage / "reports"
        stage_audio.mkdir(parents=True)
        metadata.mkdir(parents=True)
        reports.mkdir(parents=True)
        work_audio.mkdir(parents=True)

        with zipfile.ZipFile(archive) as source_zip:
            pairs = discover_pairs(source_zip, speaker=speaker)
            for pair in pairs:
                target = work_audio / f"{pair.stem}.opus"
                with source_zip.open(pair.audio_member) as source, target.open("wb") as destination:
                    shutil.copyfileobj(source, destination)

        analyses = _analyse_pairs(pairs, work_audio, decoder, max(1, jobs))
        records = _apply_duplicate_policy(analyses)
        selected = [record for record in records if record.status == "selected"]
        if not selected:
            raise RuntimeError("No samples survived the conservative training policy")

        selected = [
            AnalysedRecord(
                stem=record.stem,
                scene=record.scene,
                text=record.text,
                source_audio=record.source_audio,
                split=split_for_scene(record.scene),
                status=record.status,
                reasons=record.reasons,
                audio_sha256=record.audio_sha256,
                metrics=record.metrics,
            )
            for record in selected
        ]
        selected_by_stem = {record.stem: record for record in selected}
        records = [selected_by_stem.get(record.stem, record) for record in records]

        for record in selected:
            shutil.copyfile(work_audio / f"{record.stem}.opus", stage_audio / record.source_audio)

        reference_candidates = [
            record for record in selected if record.split == "train" and record.metrics is not None
        ]
        preferred_reference = next(
            (record for record in reference_candidates if record.stem == reference_stem),
            None,
        )
        reference = preferred_reference or min(
            reference_candidates,
            key=lambda record: reference_score(
                record.text,
                _required_metrics(record),
                record.stem,
            ),
        )
        _copy_assets(stage)
        _write_jsonl(metadata / "all_records.jsonl", records)
        _write_jsonl(metadata / "selected_records.jsonl", selected)
        _write_jsonl(
            metadata / "review_records.jsonl",
            [record for record in records if record.status == "review"],
        )
        _write_jsonl(
            metadata / "rejected_records.jsonl",
            [record for record in records if record.status == "rejected"],
        )
        _write_review_csv(reports / "review.csv", records)

        split_counts = Counter(record.split for record in selected)
        reason_counts = Counter(reason for record in records for reason in record.reasons)
        selected_seconds = sum(
            record.metrics.duration_seconds for record in selected if record.metrics is not None
        )
        manifest = {
            "schema_version": BUNDLE_SCHEMA_VERSION,
            "character": "Ayachi Nene",
            "speaker_code": speaker,
            "language": "ja",
            "local_only": True,
            "source": {
                "dataset_url": SOURCE_DATASET_URL,
                "repository_url": SOURCE_REPOSITORY_URL,
                "archive_name": archive.name,
                "archive_sha256": _sha256_file(archive),
                "declared_dataset_license": "CC-BY-NC-ND-4.0",
                "rights_review_required": True,
            },
            "qwen": {
                "repository_commit": QWEN_REPOSITORY_COMMIT,
                "default_base_model": "Qwen/Qwen3-TTS-12Hz-1.7B-Base",
                "speaker_name": "ayachi_nene_local",
            },
            "audio": {
                "bundle_format": "opus",
                "materialized_format": "mono PCM16 WAV",
                "materialized_sample_rate": 24_000,
            },
            "policy": {
                "mode": "conservative",
                "review_samples_included_in_training": False,
                "split_unit": "source scene",
                "split_seed": "chatwaifu-nene-v1",
            },
            "counts": {
                "source_pairs": len(pairs),
                "selected": len(selected),
                "review": sum(record.status == "review" for record in records),
                "rejected": sum(record.status == "rejected" for record in records),
                "splits": dict(sorted(split_counts.items())),
                "reasons": dict(sorted(reason_counts.items())),
            },
            "selected_duration_hours": selected_seconds / 3600.0,
            "reference": {
                "stem": reference.stem,
                "source_audio": reference.source_audio,
                "text": reference.text,
                "selection": (
                    "preferred_clean_reference"
                    if preferred_reference is not None
                    else "deterministic_quality_ranking"
                ),
            },
        }
        manifest_path = stage / "bundle_manifest.json"
        manifest_path.write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        stage.rename(output)

    bundle_archive = output.with_suffix(".zip")
    if bundle_archive.exists():
        if not force:
            raise FileExistsError(
                f"Bundle archive already exists: {bundle_archive}; pass --force to replace it"
            )
        bundle_archive.unlink()
    _write_deterministic_zip(output, bundle_archive)
    archive_digest = _sha256_file(bundle_archive)
    checksum_path = bundle_archive.with_suffix(bundle_archive.suffix + ".sha256")
    checksum_path.write_text(f"{archive_digest}  {bundle_archive.name}\n", encoding="utf-8")

    return BuildResult(
        bundle_directory=output,
        bundle_archive=bundle_archive,
        manifest_path=output / "bundle_manifest.json",
        selected_count=len(selected),
        review_count=sum(record.status == "review" for record in records),
        rejected_count=sum(record.status == "rejected" for record in records),
        duration_hours=selected_seconds / 3600.0,
    )


def discover_pairs(source_zip: zipfile.ZipFile, *, speaker: str) -> list[SourcePair]:
    """Discover exact Opus/LAB pairs for one speaker without extracting other voices."""

    audio: dict[str, str] = {}
    labels: dict[str, str] = {}
    for name in source_zip.namelist():
        path = PurePosixPath(name)
        if path.name.startswith(".") or "__MACOSX" in path.parts:
            continue
        if len(path.parts) < 2 or path.parent.name != speaker:
            continue
        if path.suffix.lower() == ".opus":
            audio[path.stem] = name
        elif path.suffix.lower() == ".lab":
            labels[path.stem] = name

    stems = sorted(audio.keys() & labels.keys())
    missing_audio = sorted(labels.keys() - audio.keys())
    missing_labels = sorted(audio.keys() - labels.keys())
    if missing_audio or missing_labels:
        raise ValueError(
            "Dataset contains unpaired speaker files: "
            f"missing_audio={len(missing_audio)}, missing_labels={len(missing_labels)}"
        )
    if not stems:
        raise ValueError(f"No paired .opus/.lab files found for speaker {speaker!r}")

    pairs: list[SourcePair] = []
    for stem in stems:
        raw_label = source_zip.read(labels[stem])
        original_text = _decode_label(raw_label)
        pairs.append(
            SourcePair(
                stem=stem,
                audio_member=audio[stem],
                label_member=labels[stem],
                original_text=original_text,
                normalized_text=normalize_text(original_text),
            )
        )
    return pairs


def ffmpeg_decoder(executable: Path) -> DecodeAudio:
    """Create a decoder that returns 24 kHz mono little-endian PCM16."""

    executable = executable.resolve()
    if not executable.is_file():
        raise FileNotFoundError(f"ffmpeg executable does not exist: {executable}")

    def decode(path: Path) -> bytes:
        result = subprocess.run(
            [
                str(executable),
                "-nostdin",
                "-hide_banner",
                "-loglevel",
                "error",
                "-i",
                str(path),
                "-vn",
                "-ac",
                "1",
                "-ar",
                "24000",
                "-c:a",
                "pcm_s16le",
                "-f",
                "s16le",
                "pipe:1",
            ],
            check=False,
            capture_output=True,
        )
        if result.returncode != 0:
            error = result.stderr.decode("utf-8", errors="replace").strip()
            raise RuntimeError(f"ffmpeg failed for {path.name}: {error}")
        return result.stdout

    return decode


def _analyse_pairs(
    pairs: Sequence[SourcePair],
    work_audio: Path,
    decoder: DecodeAudio,
    jobs: int,
) -> list[AnalysedRecord]:
    def analyse(pair: SourcePair) -> AnalysedRecord:
        audio_path = work_audio / f"{pair.stem}.opus"
        audio_hash = _sha256_file(audio_path)
        try:
            metrics = pcm16_metrics(decoder(audio_path))
            decision = combine_policy(text_policy(pair.normalized_text), audio_policy(metrics))
            return AnalysedRecord(
                stem=pair.stem,
                scene=scene_key(pair.stem),
                text=pair.normalized_text,
                source_audio=f"{pair.stem}.opus",
                split=None,
                status=decision.status,
                reasons=decision.reasons,
                audio_sha256=audio_hash,
                metrics=metrics,
            )
        except (OSError, RuntimeError, ValueError):
            return AnalysedRecord(
                stem=pair.stem,
                scene=scene_key(pair.stem),
                text=pair.normalized_text,
                source_audio=f"{pair.stem}.opus",
                split=None,
                status="rejected",
                reasons=("decode_failed",),
                audio_sha256=audio_hash,
                metrics=None,
            )

    with ThreadPoolExecutor(max_workers=jobs) as executor:
        analysed: list[AnalysedRecord] = []
        for index, record in enumerate(executor.map(analyse, pairs), start=1):
            analysed.append(record)
            if index % 250 == 0 or index == len(pairs):
                print(f"analysed={index}/{len(pairs)}", flush=True)
        return analysed


def _apply_duplicate_policy(records: Sequence[AnalysedRecord]) -> list[AnalysedRecord]:
    seen_audio: set[str] = set()
    result: list[AnalysedRecord] = []
    for record in records:
        if record.audio_sha256 in seen_audio and record.status != "rejected":
            result.append(
                AnalysedRecord(
                    stem=record.stem,
                    scene=record.scene,
                    text=record.text,
                    source_audio=record.source_audio,
                    split=None,
                    status="rejected",
                    reasons=(*record.reasons, "duplicate_audio"),
                    audio_sha256=record.audio_sha256,
                    metrics=record.metrics,
                )
            )
            continue
        seen_audio.add(record.audio_sha256)
        result.append(record)
    return result


def _copy_assets(stage: Path) -> None:
    if not ASSETS.is_dir():
        raise FileNotFoundError(f"Training bundle assets are missing: {ASSETS}")
    for source in sorted(ASSETS.rglob("*")):
        if not source.is_file():
            continue
        relative = source.relative_to(ASSETS)
        target = stage / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)


def _write_jsonl(path: Path, records: Sequence[AnalysedRecord]) -> None:
    with path.open("w", encoding="utf-8") as output:
        for record in records:
            output.write(json.dumps(record.as_json(), ensure_ascii=False, sort_keys=True) + "\n")


def _write_review_csv(path: Path, records: Sequence[AnalysedRecord]) -> None:
    with path.open("w", encoding="utf-8", newline="") as output:
        writer = csv.writer(output)
        writer.writerow(
            [
                "stem",
                "status",
                "reasons",
                "duration_seconds",
                "rms_dbfs",
                "clipping_ratio",
                "silence_ratio",
                "text",
            ]
        )
        for record in records:
            metrics = record.metrics
            writer.writerow(
                [
                    record.stem,
                    record.status,
                    ";".join(record.reasons),
                    "" if metrics is None else f"{metrics.duration_seconds:.3f}",
                    "" if metrics is None else f"{metrics.rms_dbfs:.3f}",
                    "" if metrics is None else f"{metrics.clipping_ratio:.8f}",
                    "" if metrics is None else f"{metrics.silence_ratio:.8f}",
                    record.text,
                ]
            )


def _write_deterministic_zip(source: Path, destination: Path) -> None:
    root_name = source.name
    with zipfile.ZipFile(destination, "w", compression=zipfile.ZIP_STORED) as output:
        for path in sorted(item for item in source.rglob("*") if item.is_file()):
            relative = path.relative_to(source)
            archive_name = str(PurePosixPath(root_name, *relative.parts))
            info = zipfile.ZipInfo(archive_name, date_time=(1980, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_STORED
            info.external_attr = 0o100644 << 16
            output.writestr(info, path.read_bytes())


def _decode_label(payload: bytes) -> str:
    for encoding in ("utf-8-sig", "cp932"):
        try:
            return payload.decode(encoding)
        except UnicodeDecodeError:
            continue
    raise ValueError("Label is neither UTF-8 nor CP932")


def _required_metrics(record: AnalysedRecord) -> AudioMetrics:
    if record.metrics is None:
        raise ValueError(f"Selected record has no audio metrics: {record.stem}")
    return record.metrics


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        while block := source.read(1024 * 1024):
            digest.update(block)
    return digest.hexdigest()


def _assert_safe_output(path: Path) -> None:
    resolved = path.resolve()
    if resolved == Path(resolved.anchor) or len(resolved.parts) < 4:
        raise ValueError(f"Refusing to replace broad output path: {resolved}")


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--archive", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--speaker", default="nen")
    parser.add_argument("--reference-stem", default=DEFAULT_REFERENCE_STEM)
    parser.add_argument("--jobs", type=int, default=min(4, os.cpu_count() or 1))
    parser.add_argument("--ffmpeg", type=Path)
    parser.add_argument("--force", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    ffmpeg = args.ffmpeg
    if ffmpeg is None:
        discovered = shutil.which("ffmpeg")
        if discovered is None:
            raise RuntimeError("ffmpeg is required; pass its path with --ffmpeg")
        ffmpeg = Path(discovered)
    result = build_training_bundle(
        archive=args.archive,
        output=args.output,
        decoder=ffmpeg_decoder(ffmpeg),
        speaker=args.speaker,
        reference_stem=args.reference_stem,
        jobs=args.jobs,
        force=args.force,
    )
    print(
        json.dumps(
            {
                "bundle": str(result.bundle_archive),
                "selected": result.selected_count,
                "review": result.review_count,
                "rejected": result.rejected_count,
                "duration_hours": round(result.duration_hours, 3),
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
