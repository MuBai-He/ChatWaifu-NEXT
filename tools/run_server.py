"""Start the source Runtime independently of a desktop, with durable server settings."""

from __future__ import annotations

import argparse
import json
import os
import secrets
import sys
from collections.abc import Generator
from contextlib import contextmanager
from pathlib import Path

from nltk_resources import configure_nltk_data_environment, ensure_punkt_tab

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_STATE_DIR = Path.home() / ".local" / "share" / "chatwaifu-server"


@contextmanager
def state_lease(state_dir: Path) -> Generator[None]:
    """Release the single-server lease on normal exit, signals, or process death."""
    state_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    descriptor = os.open(state_dir / "server.lock", os.O_RDWR | os.O_CREAT, 0o600)
    with os.fdopen(descriptor, "a+b") as lease:
        try:
            if sys.platform == "win32":
                import msvcrt

                msvcrt.locking(lease.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(lease.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            raise SystemExit(
                "This state directory is already in use or cannot be locked."
            ) from None
        yield


def _create_once(path: Path, content: str) -> None:
    """Never replace an operator's settings or rotate an existing credential."""
    try:
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        return
    with os.fdopen(descriptor, "w", encoding="utf-8") as output:
        output.write(content)
        output.flush()
        os.fsync(output.fileno())


def initialize(state_dir: Path) -> None:
    state_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    for name in ("config", "data"):
        (state_dir / name).mkdir(exist_ok=True, mode=0o700)
    paths = (
        f"config_dir = {json.dumps(str(state_dir / 'config'))}\n"
        f"data_dir = {json.dumps(str(state_dir / 'data'))}\n\n"
    )
    _create_once(state_dir / "runtime.toml", paths + (ROOT / "config/server.toml").read_text())
    _create_once(
        state_dir / "server.env",
        "# Private server credentials. Do not commit or publish this file.\n"
        f"CHATWAIFU_SECURITY__ADMIN_TOKEN={secrets.token_urlsafe(32)}\n",
    )


def _unit_quote(value: str, *, command: bool = False) -> str:
    if any(char in value for char in "\n\r\x00"):
        raise ValueError("service paths must not contain line breaks")
    escaped = value.replace("\\", "\\\\").replace('"', '\\"').replace("%", "%%")
    if command:
        escaped = escaped.replace("$", "$$")
    return f'"{escaped}"'


def systemd_unit(state_dir: Path) -> str:
    # Keep the venv executable path: resolving its symlink would bypass that venv.
    arguments = (
        sys.executable,
        str(Path(__file__).resolve()),
        "run",
        "--state-dir",
        str(state_dir),
    )
    command = " ".join(_unit_quote(arg, command=True) for arg in arguments)
    return (
        "[Unit]\n"
        "Description=ChatWaifu source Runtime\n"
        "StartLimitIntervalSec=120\n"
        "StartLimitBurst=5\n\n"
        "[Service]\n"
        "Type=simple\n"
        f"WorkingDirectory={_unit_quote(str(ROOT))}\n"
        f"ExecStart={command}\n"
        "Restart=on-failure\n"
        "RestartSec=5\n"
        "TimeoutStopSec=90\n"
        "KillMode=control-group\n"
        "UMask=0077\n"
        "Environment=PYTHONUNBUFFERED=1\n"
        "StandardOutput=journal\n"
        "StandardError=journal\n\n"
        "[Install]\n"
        "WantedBy=default.target\n"
    )


def run(state_dir: Path) -> None:
    # Same process/lifespan as the existing Runtime; no parent-EOF watchdog,
    # desktop bootstrap, model-worker spawning, or frontend build dependency.
    for relative in (
        "packages/model-worker-sdk-python/src",
        "packages/protocol-python/src",
        "services/runtime/src",
    ):
        sys.path.insert(0, str(ROOT / relative))

    from chatwaifu_runtime.config.settings import load_settings

    try:
        settings = load_settings(state_dir / "runtime.toml", state_dir / "server.env")
        token = settings.security.admin_token
        if token is None or len(token.get_secret_value()) < 32:
            raise ValueError("missing server access token")
    except ValueError:
        # Pydantic errors can include submitted secret values; do not echo them.
        raise SystemExit(
            "Invalid server configuration. Check runtime.toml and server.env; "
            "CHATWAIFU_SECURITY__ADMIN_TOKEN must contain at least 32 characters."
        ) from None

    nltk_root = configure_nltk_data_environment(state_dir / "nltk_data")
    ensure_punkt_tab(nltk_root)

    import uvicorn
    from chatwaifu_runtime.main import create_app
    from chatwaifu_runtime.observability.logging import configure_logging

    configure_logging(settings.log_level)
    print(f"Server state: {state_dir}", file=sys.stderr)
    print(f"Access credential: {state_dir / 'server.env'} (not printed)", file=sys.stderr)
    uvicorn.run(
        create_app(settings),
        host=settings.runtime.host,
        port=settings.runtime.port,
        log_config=None,
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "command", choices=("init", "run", "systemd-unit"), nargs="?", default="run"
    )
    parser.add_argument("--state-dir", type=Path, default=DEFAULT_STATE_DIR)
    args = parser.parse_args()
    state_dir = args.state_dir.expanduser().resolve()
    if args.command == "systemd-unit":
        print(systemd_unit(state_dir), end="")
    else:
        with state_lease(state_dir):
            initialize(state_dir)
            if args.command == "init":
                print(f"Server configuration ready: {state_dir}")
                print("Edit runtime.toml and server.env to configure a real chat model.")
            else:
                run(state_dir)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
