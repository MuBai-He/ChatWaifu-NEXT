"""Build and describe the self-contained PyInstaller Runtime sidecar."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import subprocess
import sys
from pathlib import Path

from nltk_resources import configure_nltk_data_environment, ensure_punkt_tab

ROOT = Path(__file__).resolve().parents[1]
SPEC = ROOT / "packaging" / "windows" / "chatwaifu-runtime.spec"


def main() -> int:
    arguments = _parser().parse_args()
    platform_name = arguments.platform or ("windows" if os.name == "nt" else "macos")
    if platform_name == "windows" and os.name != "nt":
        raise RuntimeError("Windows Runtime sidecars must be frozen on Windows")
    output_root = ROOT / "dist" / platform_name
    work_root = ROOT / "build" / "pyinstaller" / platform_name
    nltk_root = configure_nltk_data_environment()
    ensure_punkt_tab(nltk_root)
    _verify_pinned_nltk_root(nltk_root)
    environment = os.environ.copy()
    environment["NLTK_DATA"] = str(nltk_root)
    subprocess.run(
        [
            sys.executable,
            "-m",
            "PyInstaller",
            "--clean",
            "--noconfirm",
            "--distpath",
            str(output_root),
            "--workpath",
            str(work_root),
            str(SPEC),
        ],
        cwd=ROOT,
        env=environment,
        check=True,
    )
    bundle = output_root / "runtime-sidecar"
    executable = bundle / ("chatwaifu-runtime.exe" if os.name == "nt" else "chatwaifu-runtime")
    _verify_bundle(bundle, executable)
    manifest_path = bundle / "chatwaifu-runtime.manifest.json"
    manifest_path.write_text(
        json.dumps(_manifest(bundle, executable), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(executable)
    return 0


def _verify_bundle(bundle: Path, executable: Path) -> None:
    required = (
        executable,
        bundle / "_internal" / "config" / "default.toml",
        bundle / "_internal" / "characters" / "default" / "character.yaml",
        bundle / "_internal" / "skills" / "builtin" / "runtime-status" / "chatwaifu.yaml",
        bundle
        / "_internal"
        / "nltk_data"
        / "tokenizers"
        / "punkt_tab"
        / "english"
        / "abbrev_types.txt",
        bundle / "_internal" / "pipecat" / "audio" / "vad" / "data" / "silero_vad.onnx",
    )
    missing = [str(path) for path in required if not path.is_file()]
    if missing:
        raise RuntimeError(f"Frozen Runtime is missing required files: {missing}")
    _verify_pinned_nltk_root(bundle / "_internal" / "nltk_data")


def _verify_pinned_nltk_root(root: Path) -> None:
    entries = {path.name for path in root.iterdir()}
    if entries != {"tokenizers"}:
        raise RuntimeError(f"NLTK bundle must contain only pinned tokenizers, received {entries}")
    tokenizer_entries = {path.name for path in (root / "tokenizers").iterdir()}
    if tokenizer_entries != {"punkt_tab"}:
        raise RuntimeError(
            "NLTK tokenizer bundle must contain only punkt_tab, "
            f"received {tokenizer_entries}"
        )


def _manifest(bundle: Path, executable: Path) -> dict[str, object]:
    files = [path for path in bundle.rglob("*") if path.is_file()]
    return {
        "schema_version": "1.0",
        "component": "chatwaifu-runtime-sidecar",
        "python_version": platform.python_version(),
        "platform": platform.platform(),
        "architecture": platform.machine(),
        "file_count": len(files),
        "total_bytes": sum(path.stat().st_size for path in files),
        "executable_sha256": _sha256(executable),
    }


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--platform", choices=("windows", "macos", "linux"))
    return parser


if __name__ == "__main__":
    raise SystemExit(main())
