"""Install the unified worker shim into pre-provisioned local engine environments."""

import os
import shutil
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WORKER = ROOT / "workers" / "tts-neural"
SDK = ROOT / "packages" / "model-worker-sdk-python"
ENVIRONMENTS = {
    "Qwen3-TTS MLX": ROOT / ".local" / "envs" / "qwen3-tts-mlx",
    "GPT-SoVITS CPUFast": ROOT / ".local" / "envs" / "gpt-sovits-cpufast",
}


def main() -> int:
    uv = shutil.which("uv")
    if uv is None:
        raise RuntimeError("uv is required to install the local neural TTS workers")
    environment = os.environ.copy()
    environment.pop("VIRTUAL_ENV", None)
    for label, environment_root in ENVIRONMENTS.items():
        python = _python(environment_root)
        if not python.exists():
            raise RuntimeError(
                f"{label} environment is missing: {python}. See docs/operations/neural-tts.md."
            )
        print(f"Installing unified worker API into {label}...", flush=True)
        result = subprocess.run(
            [
                uv,
                "pip",
                "install",
                "--python",
                str(python),
                "--no-editable",
                str(SDK),
                str(WORKER),
            ],
            cwd=ROOT,
            env=environment,
            check=False,
        )
        if result.returncode != 0:
            return result.returncode
    print("Unified Qwen/GPT-SoVITS worker API is installed in both local environments.")
    return 0


def _python(environment_root: Path) -> Path:
    return environment_root / ("Scripts/python.exe" if os.name == "nt" else "bin/python")


if __name__ == "__main__":
    raise SystemExit(main())
