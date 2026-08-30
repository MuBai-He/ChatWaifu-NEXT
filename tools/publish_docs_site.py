from __future__ import annotations

import argparse
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
DIST_ROOT = REPOSITORY_ROOT / "docs-site" / ".vitepress" / "dist"
PUBLIC_REPOSITORY = "https://github.com/MuBai-He/ChatWaifu-NEXT-docs.git"
MAX_PUBLIC_FILE_BYTES = 20 * 1024 * 1024
FORBIDDEN_SUFFIXES = {
    ".ckpt",
    ".db",
    ".m4a",
    ".mp3",
    ".pth",
    ".safetensors",
    ".sqlite",
    ".sqlite3",
    ".wav",
}
TEXT_SUFFIXES = {".css", ".html", ".js", ".json", ".map", ".svg", ".txt", ".xml"}
FORBIDDEN_TEXT = {
    "absolute macOS user path": re.compile(r"/Users/[^/\s]+/"),
    "absolute Windows user path": re.compile(r"[A-Za-z]:\\Users\\[^\\\s]+\\"),
    "local file URL": re.compile(r"file://", re.IGNORECASE),
    "private IPv4 address": re.compile(r"(?:10\.\d{1,3}|192\.168)\.\d{1,3}\.\d{1,3}"),
    "credential-shaped token": re.compile(
        r"(?<![A-Za-z0-9])sk-[A-Za-z0-9_-]{20,}"
        r"|(?<![A-Za-z0-9])ms-[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}",
        re.IGNORECASE,
    ),
    "private key": re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
}


def run(*command: str, cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(command, cwd=cwd, check=True, text=True)


def validate_dist(dist_root: Path) -> tuple[int, int]:
    if not (dist_root / "index.html").is_file():
        raise RuntimeError(f"documentation build is missing: {dist_root / 'index.html'}")

    file_count = 0
    total_bytes = 0
    for path in sorted(dist_root.rglob("*")):
        if path.is_symlink():
            raise RuntimeError(f"public documentation contains a symlink: {path}")
        if not path.is_file():
            continue

        file_count += 1
        size = path.stat().st_size
        total_bytes += size
        if size > MAX_PUBLIC_FILE_BYTES:
            raise RuntimeError(f"public documentation file exceeds 20 MiB: {path}")
        if path.suffix.lower() in FORBIDDEN_SUFFIXES:
            raise RuntimeError(f"private asset type is forbidden in public documentation: {path}")
        if path.suffix.lower() not in TEXT_SUFFIXES:
            continue

        contents = path.read_text(encoding="utf-8", errors="replace")
        for label, pattern in FORBIDDEN_TEXT.items():
            if pattern.search(contents):
                raise RuntimeError(f"{label} found in public documentation: {path}")

    return file_count, total_bytes


def publish(dist_root: Path) -> None:
    revision = subprocess.run(
        ["git", "rev-parse", "--short=12", "HEAD"],
        cwd=REPOSITORY_ROOT,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()

    with tempfile.TemporaryDirectory(prefix="chatwaifu-docs-publish-") as temp_dir:
        publish_root = Path(temp_dir) / "site"
        run(
            "git", "clone", "--depth", "1", PUBLIC_REPOSITORY, str(publish_root), cwd=Path(temp_dir)
        )

        for child in publish_root.iterdir():
            if child.name == ".git":
                continue
            if child.is_dir() and not child.is_symlink():
                shutil.rmtree(child)
            else:
                child.unlink()

        shutil.copytree(dist_root, publish_root, dirs_exist_ok=True)
        (publish_root / ".nojekyll").write_text("", encoding="utf-8")
        (publish_root / "README.md").write_text(
            "# ChatWaifu NEXT Documentation\n\n"
            "Generated from the private ChatWaifu NEXT monorepo. "
            "Do not edit this deployment repository by hand.\n",
            encoding="utf-8",
        )

        run("git", "add", "--all", cwd=publish_root)
        status = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=publish_root,
            check=True,
            capture_output=True,
            text=True,
        ).stdout
        if not status.strip():
            print("Public documentation is already up to date.")
            return

        run("git", "commit", "-m", f"docs: publish {revision}", cwd=publish_root)
        run("git", "push", "origin", "HEAD:main", cwd=publish_root)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Audit and publish the generated documentation site."
    )
    parser.add_argument(
        "--publish",
        action="store_true",
        help="push the audited static build to the public Pages repository",
    )
    args = parser.parse_args()

    file_count, total_bytes = validate_dist(DIST_ROOT)
    print(f"Validated {file_count} public files ({total_bytes} bytes).")
    if args.publish:
        publish(DIST_ROOT)


if __name__ == "__main__":
    main()
