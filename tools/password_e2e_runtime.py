"""Disposable loopback Runtime for the real password connection acceptance flow."""

import argparse
from pathlib import Path
from tempfile import TemporaryDirectory

import uvicorn
from chatwaifu_runtime.config.settings import Settings
from chatwaifu_runtime.main import create_app


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=18886)
    parser.add_argument("--web-dir", type=Path)
    args = parser.parse_args()
    if args.port == 8765 or not 1024 <= args.port <= 65535:
        parser.error("choose an unused test port other than the ordinary Runtime port")
    # Always use a fresh directory; never load operator state or existing passwords.
    with TemporaryDirectory(prefix="cw2-password-e2e-") as directory:
        state = Path(directory)
        settings = Settings.model_validate(
            {
                "config_dir": state / "config",
                "data_dir": state,
                "storage": {"database_path": state / "runtime.db"},
                "llm": {"provider": "demo", "demo_chunk_delay_ms": 0},
                "tts": {"provider": "fake"},
                "runtime": {"web_client_dir": args.web_dir},
                "security": {
                    "capability_token": "disposable-password-e2e-access-token-1234567890",
                    "allowed_origins": ["http://127.0.0.1:14173"],
                },
            }
        )
        uvicorn.run(create_app(settings), host="127.0.0.1", port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
