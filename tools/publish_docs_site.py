from __future__ import annotations

import argparse
import re
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
DIST_ROOT = REPOSITORY_ROOT / "docs-site" / ".vitepress" / "dist"
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


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Audit the generated public documentation site for sensitive paths and assets."
    )
    parser.add_argument(
        "--publish",
        action="store_true",
        help="legacy flag; documentation is published automatically by GitHub Actions",
    )
    args = parser.parse_args()

    file_count, total_bytes = validate_dist(DIST_ROOT)
    print(f"Validated {file_count} public files ({total_bytes} bytes).")
    if args.publish:
        print(
            "Documentation deployment is managed automatically by GitHub Actions on push to main."
        )


if __name__ == "__main__":
    main()
