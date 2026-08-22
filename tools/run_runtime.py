"""Run the Runtime from a source checkout without relying on editable-install .pth files."""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "packages" / "protocol-python" / "src"))
sys.path.insert(0, str(ROOT / "services" / "runtime" / "src"))

from chatwaifu_runtime.main import run  # noqa: E402

if __name__ == "__main__":
    run()
