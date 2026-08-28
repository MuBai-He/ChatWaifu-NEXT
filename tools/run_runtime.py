"""Run the Runtime from a source checkout without relying on editable-install .pth files."""

import sys
from pathlib import Path

from nltk_resources import configure_nltk_data_environment, ensure_punkt_tab

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "packages" / "model-worker-sdk-python" / "src"))
sys.path.insert(0, str(ROOT / "packages" / "protocol-python" / "src"))
sys.path.insert(0, str(ROOT / "services" / "runtime" / "src"))

# Pipecat imports its NLTK sentence splitter while the Runtime module graph is loaded.
# Prepare and expose the pinned local tables before that import so startup never falls
# back to NLTK's network downloader.
configure_nltk_data_environment()
ensure_punkt_tab()

from chatwaifu_runtime.main import run  # noqa: E402

if __name__ == "__main__":
    run()
