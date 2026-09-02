"""The Runtime package root must not eagerly load provider or media graphs."""

import subprocess
import sys
from pathlib import Path

RUNTIME_SOURCE = Path(__file__).resolve().parents[1] / "src"


def test_package_import_keeps_main_lazy() -> None:
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            f"import sys; sys.path.insert(0, {str(RUNTIME_SOURCE)!r}); "
            "import chatwaifu_runtime; "
            "assert 'chatwaifu_runtime.main' not in sys.modules",
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )

    assert result.returncode == 0, result.stderr
