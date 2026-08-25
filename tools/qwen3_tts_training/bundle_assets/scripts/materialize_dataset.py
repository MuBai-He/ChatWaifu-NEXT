"""Materialize the compact Opus bundle into Qwen3-TTS 24 kHz WAV JSONL files."""

from __future__ import annotations

import argparse
import concurrent.futures
import hashlib
import json
import os
import shutil
import subprocess
import wave
from collections import Counter
from pathlib import Path
from typing import Any


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bundle-root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--ffmpeg", default="ffmpeg")
    parser.add_argument("--workers", type=int, default=min(4, os.cpu_count() or 1))
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()

    root = args.bundle_root.resolve()
    manifest = _read_json(root / "bundle_manifest.json")
    records = _read_jsonl(root / "metadata" / "selected_records.jsonl")
    data = root / "data"
    wavs = data / "wavs"
    wavs.mkdir(parents=True, exist_ok=True)

    def materialize(record: dict[str, Any]) -> tuple[str, str]:
        stem = _required_string(record, "stem")
        source_audio = root / "source_audio" / _required_string(record, "source_audio")
        destination = wavs / f"{stem}.wav"
        if destination.exists() and not args.force:
            _verify_wav(destination)
            return stem, _sha256_file(destination)
        temporary = destination.with_suffix(".wav.partial")
        command = [
            args.ffmpeg,
            "-nostdin",
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-i",
            str(source_audio),
            "-vn",
            "-ac",
            "1",
            "-ar",
            "24000",
            "-c:a",
            "pcm_s16le",
            "-f",
            "wav",
            str(temporary),
        ]
        result = subprocess.run(command, check=False, capture_output=True, text=True)
        if result.returncode != 0:
            raise RuntimeError(f"ffmpeg failed for {stem}: {result.stderr.strip()}")
        _verify_wav(temporary)
        temporary.replace(destination)
        return stem, _sha256_file(destination)

    with concurrent.futures.ThreadPoolExecutor(max_workers=max(1, args.workers)) as executor:
        converted = dict(executor.map(materialize, records))

    reference = manifest.get("reference")
    if not isinstance(reference, dict):
        raise ValueError("bundle_manifest.json has no reference object")
    reference_stem = _required_string(reference, "stem")
    reference_wav = wavs / f"{reference_stem}.wav"
    fixed_reference = data / "ref.wav"
    shutil.copyfile(reference_wav, fixed_reference)
    _verify_wav(fixed_reference)

    split_counts: Counter[str] = Counter()
    for split in ("train", "validation", "test"):
        target = data / f"{split}_raw.jsonl"
        with target.open("w", encoding="utf-8") as output:
            for record in records:
                if record.get("split") != split:
                    continue
                stem = _required_string(record, "stem")
                payload = {
                    "audio": str((wavs / f"{stem}.wav").resolve()),
                    "text": _required_string(record, "text"),
                    "ref_audio": str(fixed_reference.resolve()),
                    "language": "Japanese",
                    "source_stem": stem,
                }
                output.write(json.dumps(payload, ensure_ascii=False) + "\n")
                split_counts[split] += 1

    report = {
        "schema_version": "1.0",
        "wav_sample_rate": 24000,
        "wav_channels": 1,
        "wav_sample_width_bytes": 2,
        "reference_stem": reference_stem,
        "reference_sha256": _sha256_file(fixed_reference),
        "converted": len(converted),
        "splits": dict(sorted(split_counts.items())),
        "wav_sha256": dict(sorted(converted.items())),
    }
    (data / "materialization_report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps({"data": str(data), "splits": report["splits"]}, ensure_ascii=False))
    return 0


def _verify_wav(path: Path) -> None:
    with wave.open(str(path), "rb") as audio:
        if audio.getnchannels() != 1:
            raise ValueError(f"{path} is not mono")
        if audio.getframerate() != 24_000:
            raise ValueError(f"{path} is not 24 kHz")
        if audio.getsampwidth() != 2:
            raise ValueError(f"{path} is not PCM16")
        if audio.getnframes() == 0:
            raise ValueError(f"{path} is empty")


def _read_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"Expected an object in {path}")
    return payload


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        payload = json.loads(line)
        if not isinstance(payload, dict):
            raise ValueError(f"Expected an object at {path}:{line_number}")
        records.append(payload)
    return records


def _required_string(payload: dict[str, Any], key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value:
        raise ValueError(f"Missing string field {key!r}")
    return value


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        while block := source.read(1024 * 1024):
            digest.update(block)
    return digest.hexdigest()


if __name__ == "__main__":
    raise SystemExit(main())
