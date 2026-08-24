"""Create/update the isolated faster-whisper worker environment."""

import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WORKER = ROOT / "workers" / "asr-faster-whisper"


def main() -> int:
    uv = shutil.which("uv")
    if uv is None:
        raise RuntimeError("uv is required to install the local STT worker")
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
    if result.returncode == 0:
        print(f"Local STT worker ready: {worker_python()}")
    return result.returncode


def worker_python() -> Path:
    executable = "python.exe" if sys.platform == "win32" else "python"
    directory = "Scripts" if executable.endswith(".exe") else "bin"
    return WORKER / ".venv" / directory / executable


if __name__ == "__main__":
    raise SystemExit(main())
