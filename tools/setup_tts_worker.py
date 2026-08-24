"""Provision the isolated Kokoro worker and its verified public model cache."""

import hashlib
import os
import shutil
import subprocess
import sys
import tarfile
import tempfile
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WORKER = ROOT / "workers" / "tts-sherpa-kokoro"
MODEL_NAME = "kokoro-multi-lang-v1_1"
MODEL_ROOT = ROOT / ".local" / "models" / "kokoro"
MODEL_DIR = MODEL_ROOT / MODEL_NAME
DOWNLOAD_DIR = ROOT / ".local" / "downloads"
ARCHIVE = DOWNLOAD_DIR / f"{MODEL_NAME}.tar.bz2"
MODEL_URL = (
    f"https://github.com/k2-fsa/sherpa-onnx/releases/download/tts-models/{MODEL_NAME}.tar.bz2"
)
MODEL_SHA256 = "a3f4c73d043860e3fd2e5b06f36795eb81de0fc8e8de6df703245edddd87dbad"
REQUIRED_FILES = (
    "model.onnx",
    "voices.bin",
    "tokens.txt",
    "lexicon-us-en.txt",
    "lexicon-zh.txt",
    "espeak-ng-data",
)


def main() -> int:
    uv = shutil.which("uv")
    if uv is None:
        raise RuntimeError("uv is required to install the local TTS worker")
    environment = os.environ.copy()
    environment.pop("VIRTUAL_ENV", None)
    result = subprocess.run(
        [
            uv,
            "sync",
            "--project",
            str(WORKER),
            "--all-groups",
            "--no-editable",
            "--refresh-package",
            "chatwaifu-model-worker-sdk",
        ],
        cwd=ROOT,
        env=environment,
        check=False,
    )
    if result.returncode != 0:
        return result.returncode
    ensure_model()
    print(f"Local Kokoro TTS worker ready: {worker_python()}")
    print(f"Verified model: {MODEL_DIR}")
    return 0


def ensure_model() -> None:
    if _model_ready(MODEL_DIR):
        return
    DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)
    MODEL_ROOT.mkdir(parents=True, exist_ok=True)
    if not ARCHIVE.exists() or _sha256(ARCHIVE) != MODEL_SHA256:
        ARCHIVE.unlink(missing_ok=True)
        partial = ARCHIVE.with_suffix(f"{ARCHIVE.suffix}.part")
        partial.unlink(missing_ok=True)
        print("Downloading Kokoro v1.1 model (about 365 MB)...", flush=True)
        digest = hashlib.sha256()
        with (
            urllib.request.urlopen(MODEL_URL, timeout=60) as response,
            partial.open("wb") as output,
        ):
            while chunk := response.read(1024 * 1024):
                output.write(chunk)
                digest.update(chunk)
        if digest.hexdigest() != MODEL_SHA256:
            partial.unlink(missing_ok=True)
            raise RuntimeError("downloaded Kokoro model checksum did not match the pinned release")
        partial.replace(ARCHIVE)
    with tempfile.TemporaryDirectory(prefix="kokoro-extract-", dir=MODEL_ROOT) as temporary:
        temporary_root = Path(temporary)
        with tarfile.open(ARCHIVE, "r:bz2") as bundle:
            bundle.extractall(temporary_root, filter="data")
        extracted = temporary_root / MODEL_NAME
        if not _model_ready(extracted):
            raise RuntimeError("Kokoro archive did not contain the expected model layout")
        if MODEL_DIR.exists():
            shutil.rmtree(MODEL_DIR)
        extracted.replace(MODEL_DIR)


def worker_python() -> Path:
    executable = "python.exe" if sys.platform == "win32" else "python"
    directory = "Scripts" if executable.endswith(".exe") else "bin"
    return WORKER / ".venv" / directory / executable


def _model_ready(root: Path) -> bool:
    return all((root / relative).exists() for relative in REQUIRED_FILES)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        while chunk := source.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


if __name__ == "__main__":
    raise SystemExit(main())
