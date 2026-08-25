"""Extract Qwen3-TTS 12 Hz audio codes with a configurable bounded batch size."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from qwen_tts import Qwen3TTSTokenizer


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--device", default="cuda:0")
    parser.add_argument(
        "--tokenizer_model_path",
        default="Qwen/Qwen3-TTS-Tokenizer-12Hz",
    )
    parser.add_argument("--input_jsonl", type=Path, required=True)
    parser.add_argument("--output_jsonl", type=Path, required=True)
    parser.add_argument("--batch_size", type=int, default=8)
    args = parser.parse_args()
    if args.batch_size < 1:
        raise ValueError("--batch_size must be positive")

    tokenizer = Qwen3TTSTokenizer.from_pretrained(
        args.tokenizer_model_path,
        device_map=args.device,
    )
    records = _read_jsonl(args.input_jsonl)
    temporary = args.output_jsonl.with_suffix(args.output_jsonl.suffix + ".partial")
    with temporary.open("w", encoding="utf-8") as output:
        for start in range(0, len(records), args.batch_size):
            batch = records[start : start + args.batch_size]
            paths = [_required_string(record, "audio") for record in batch]
            encoded = tokenizer.encode(paths)
            for code, record in zip(encoded.audio_codes, batch, strict=True):
                record["audio_codes"] = code.cpu().tolist()
                output.write(json.dumps(record, ensure_ascii=False) + "\n")
            print(f"encoded={min(start + len(batch), len(records))}/{len(records)}", flush=True)
    temporary.replace(args.output_jsonl)
    return 0


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        payload = json.loads(line)
        if not isinstance(payload, dict):
            raise ValueError(f"Expected object at {path}:{line_number}")
        records.append(payload)
    if not records:
        raise ValueError(f"No records found in {path}")
    return records


def _required_string(payload: dict[str, Any], key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value:
        raise ValueError(f"Missing string field {key!r}")
    return value


if __name__ == "__main__":
    raise SystemExit(main())
