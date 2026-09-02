"""Reject local build paths embedded anywhere in a Worker Pack payload."""

from __future__ import annotations

import argparse
import os
import re
import sys
from collections.abc import Iterable, Sequence
from pathlib import Path

_CHUNK_SIZE = 8 * 1024 * 1024
_HF_TOKEN_PATTERN = re.compile(rb"(?<![A-Za-z0-9_])hf_[A-Za-z0-9]{34}(?![A-Za-z0-9])")


class PayloadContentError(RuntimeError):
    """Raised when a staged payload embeds forbidden local or credential material."""


def _path_markers(paths: Iterable[str]) -> tuple[bytes, ...]:
    markers: set[bytes] = set()
    for raw_path in paths:
        resolved = str(Path(raw_path).expanduser().resolve(strict=True)).rstrip("\\/")
        if not resolved:
            raise ValueError("forbidden paths must not resolve to a filesystem root")
        variants = {
            resolved,
            resolved.replace("\\", "/"),
            resolved.replace("/", "\\"),
        }
        for variant in variants:
            for encoding in ("utf-8", "utf-16-le", "utf-16-be"):
                # The file stream is byte-lowered for Windows' ASCII case folding.
                # Preserve non-ASCII path bytes verbatim so exact Unicode paths also match.
                marker = variant.encode(encoding).lower()
                if marker:
                    markers.add(marker)
    if not markers:
        raise ValueError("at least one forbidden path is required")
    return tuple(sorted(markers, key=len, reverse=True))


def _file_violation(path: Path, markers: Sequence[bytes]) -> str | None:
    overlap = max(max(len(marker) for marker in markers), 40) - 1
    carry = b""
    with path.open("rb") as stream:
        while chunk := stream.read(_CHUNK_SIZE):
            window = carry + chunk
            folded = window.lower()
            if any(marker in folded for marker in markers):
                return "a forbidden local build path"
            if _HF_TOKEN_PATTERN.search(window):
                return "Hugging Face access-token material"
            carry = window[-overlap:] if overlap else b""
    return None


def assert_payload_is_sanitized(root: Path, forbidden_paths: Sequence[str]) -> None:
    resolved_root = root.expanduser().resolve(strict=True)
    if not resolved_root.is_dir():
        raise ValueError(f"payload root is not a directory: {resolved_root}")
    markers = _path_markers(forbidden_paths)
    file_count = 0
    byte_count = 0
    for directory, directory_names, file_names in os.walk(resolved_root, followlinks=False):
        directory_names.sort(key=str.casefold)
        file_names.sort(key=str.casefold)
        for file_name in file_names:
            path = Path(directory, file_name)
            if not path.is_file():
                continue
            file_count += 1
            byte_count += path.stat().st_size
            violation = _file_violation(path, markers)
            if violation is not None:
                relative = path.relative_to(resolved_root).as_posix()
                raise PayloadContentError(f"worker payload contains {violation}: {relative!r}")
    print(
        "Verified worker payload contains no local build paths or credential material "
        f"across {file_count} files and {byte_count} bytes."
    )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--forbidden-path", action="append", required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        assert_payload_is_sanitized(args.root, args.forbidden_path)
    except (OSError, PayloadContentError, ValueError) as error:
        print(f"worker-payload-paths: error: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
