"""Optional Linux deployment shim for a pinned, isolated LibreOffice image."""

import argparse
import os
import re
import signal
import subprocess
import sys
from pathlib import Path
from urllib.parse import unquote, urlsplit
from uuid import uuid4


def render(image: str, arguments: list[str]) -> int:
    if not re.fullmatch(r"sha256:[a-f0-9]{64}", image):
        raise ValueError("renderer requires an immutable image ID")
    options = [
        "--env",
        "HOME=/tmp",
        "--env",
        "XDG_CACHE_HOME=/tmp/cache",
        "--env",
        "SAL_USE_VCLPLUGIN=svp",
        "--network",
        "none",
        "--read-only",
        "--cap-drop",
        "ALL",
        "--security-opt",
        "no-new-privileges",
        "--pids-limit",
        "128",
        "--cpus",
        "2",
        "--memory",
        "1g",
        "--user",
        f"{os.getuid()}:{os.getgid()}",
        "--tmpfs",
        "/tmp:rw,nosuid,size=268435456",
        "--init",
        "--rm",
        "--label",
        "cw2.document-renderer=true",
        "--label",
        "com.centurylinklabs.watchtower.enable=false",
    ]
    if arguments != ["--headless", "--version"]:
        if (
            len(arguments) != 7
            or not arguments[0].startswith("-env:UserInstallation=file:///")
            or arguments[1:5] != ["--headless", "--convert-to", "pdf", "--outdir"]
        ):
            raise ValueError("unsupported renderer arguments")
        directory = Path(arguments[5])
        document = Path(arguments[6])
        profile = Path(unquote(urlsplit(arguments[0].split("=", 1)[1]).path))
        if (
            directory != directory.resolve()
            or not directory.name.startswith("cw-document-")
            or document.parent != directory
            or document.name not in {"document.docx", "document.pptx"}
            or document.is_symlink()
            or not document.is_file()
            or profile != directory / "profile"
        ):
            raise ValueError("renderer requires an isolated document directory")
        options.extend(
            [
                "--mount",
                f"type=bind,src={directory},dst={directory}",
                "--workdir",
                str(directory),
                "--env",
                f"HOME={directory}",
                "--env",
                f"FONTCONFIG_FILE={directory / 'fonts.conf'}",
            ]
        )
    name = "cw2-document-" + uuid4().hex
    command = [
        "docker",
        "create",
        "--name",
        name,
        *options,
        "--entrypoint",
        "/usr/bin/timeout",
        image,
        "--signal=TERM",
        "--kill-after=2s",
        "55s",
        "/usr/bin/libreoffice",
        *arguments,
    ]
    process: subprocess.Popen[bytes] | None = None

    def interrupt(_number: int, _frame: object) -> None:
        raise InterruptedError("document render cancelled")

    signal.signal(signal.SIGTERM, interrupt)
    signal.signal(signal.SIGINT, interrupt)
    try:
        process = subprocess.Popen(command, stdout=subprocess.DEVNULL)
        if process.wait(timeout=10):
            return 1
        process = subprocess.Popen(["docker", "start", "--attach", name])
        return process.wait(timeout=58)
    except (InterruptedError, subprocess.TimeoutExpired):
        return 124
    finally:
        signal.signal(signal.SIGTERM, signal.SIG_IGN)
        signal.signal(signal.SIGINT, signal.SIG_IGN)
        if process is not None and process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=0.3)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=0.3)
        try:
            subprocess.run(
                ["docker", "rm", "--force", name],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=1.2,
                check=False,
            )
        except subprocess.TimeoutExpired:
            # Container's own 55-second deadline also covers host SIGKILL.
            pass


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--image", required=True)
    options, arguments = parser.parse_known_args()
    try:
        sys.exit(render(options.image, arguments))
    except ValueError as error:
        sys.exit(str(error))
