"""Secure offline build, verification, installation, and selection for worker packs."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import stat
import struct
import tempfile
import zipfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any, Literal, cast

from pydantic import ValidationError

from chatwaifu_model_worker.packages import (
    WORKER_PACK_MAX_EXPANDED_BYTES,
    WORKER_PACK_MAX_FILE_BYTES,
    WORKER_PACK_MAX_FILE_COUNT,
    WorkerPackActivationConfig,
    WorkerPackActiveSelection,
    WorkerPackInstallReceipt,
    WorkerPackManifest,
    WorkerPackSelection,
)

MANIFEST_NAME = "manifest.json"
RECEIPT_NAME = "install-receipt.json"
SELECTION_NAME = "local-ai-selection.json"
MAX_MANIFEST_BYTES = 4 * 1024 * 1024
MAX_RECEIPT_BYTES = 1024 * 1024
COPY_CHUNK_BYTES = 4 * 1024 * 1024
MAX_ARCHIVE_MEMBER_COUNT = WORKER_PACK_MAX_FILE_COUNT * 2 + 2
MIN_FREE_SPACE_HEADROOM_BYTES = 512 * 1024 * 1024
FREE_SPACE_HEADROOM_PERCENT = 5
_WINDOWS_PE_MACHINE = {"x86_64": 0x8664, "arm64": 0xAA64}
_WINDOWS_NATIVE_SUFFIXES = frozenset({".exe", ".dll", ".pyd"})
_ALREADY_COMPRESSED_OR_LARGE_BINARY_SUFFIXES = frozenset(
    {
        ".7z",
        ".bin",
        ".bz2",
        ".ckpt",
        ".flac",
        ".gguf",
        ".gz",
        ".m4a",
        ".mp3",
        ".npy",
        ".npz",
        ".onnx",
        ".pt",
        ".pth",
        ".safetensors",
        ".wav",
        ".whl",
        ".xz",
        ".zip",
    }
)
_FORBIDDEN_STAGING_PARTS = frozenset(
    {
        ".git",
        ".idea",
        ".local",
        ".mypy_cache",
        ".pytest_cache",
        ".ruff_cache",
        "__pycache__",
    }
)
_FORBIDDEN_STAGING_NAMES = frozenset(
    {
        ".ds_store",
        ".netrc",
        ".npmrc",
        ".pypirc",
        "auth.json",
        "credentials.json",
        "install-receipt.json",
        "manifest.json",
        "pip.conf",
        "pip.ini",
        "thumbs.db",
    }
)
_FORBIDDEN_STAGING_SUFFIXES = frozenset(
    {".db", ".key", ".p12", ".pfx", ".sqlite", ".sqlite3", ".token"}
)
_PRIVATE_KEY_MARKERS = (
    b"-----BEGIN PRIVATE KEY-----",
    b"-----BEGIN ENCRYPTED PRIVATE KEY-----",
    b"-----BEGIN RSA PRIVATE KEY-----",
    b"-----BEGIN DSA PRIVATE KEY-----",
    b"-----BEGIN EC PRIVATE KEY-----",
    b"-----BEGIN OPENSSH PRIVATE KEY-----",
)
_DETERMINISTIC_ZIP_TIMESTAMP = (1980, 1, 1, 0, 0, 0)
_WINDOWS_REPARSE_POINT_ATTRIBUTE = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
# `datetime.UTC` was added in Python 3.11, while the shared worker SDK is also
# installed into the supported Python 3.10 GPT-SoVITS environment.
_UTC = timezone.utc  # noqa: UP017


class WorkerPackError(RuntimeError):
    """Raised when a worker pack is unsafe, invalid, or cannot be installed."""


@dataclass(frozen=True, slots=True)
class VerifiedWorkerPackArchive:
    archive_path: Path
    archive_sha256: str
    manifest_sha256: str
    manifest_bytes: bytes
    manifest: WorkerPackManifest


@dataclass(frozen=True, slots=True)
class InstalledWorkerPack:
    root: Path
    manifest: WorkerPackManifest
    receipt: WorkerPackInstallReceipt


def _path_is_link_or_reparse(path: Path) -> bool:
    """Detect POSIX symlinks and Windows junction/reparse points without following them."""

    try:
        metadata = path.lstat()
    except FileNotFoundError:
        return False
    except OSError as error:
        raise WorkerPackError(f"could not inspect filesystem path: {path}") from error
    if stat.S_ISLNK(metadata.st_mode):
        return True
    file_attributes = cast(int, getattr(metadata, "st_file_attributes", 0))
    if file_attributes & _WINDOWS_REPARSE_POINT_ATTRIBUTE:
        return True
    is_junction = getattr(path, "is_junction", None)
    if callable(is_junction):
        try:
            return bool(is_junction())
        except OSError as error:
            raise WorkerPackError(f"could not inspect filesystem junction: {path}") from error
    return False


def _reject_link_or_reparse(path: Path, *, label: str) -> None:
    if _path_is_link_or_reparse(path):
        raise WorkerPackError(
            f"{label} must not be symlinked and must not be a junction or reparse point: {path}"
        )


def _reject_reparse_ancestors(path: Path, *, label: str) -> None:
    """Reject an existing reparse point anywhere in an untrusted path's ancestry."""

    current = path.expanduser().absolute()
    while True:
        _reject_link_or_reparse(current, label=label)
        parent = current.parent
        if parent == current:
            return
        current = parent


def _require_real_directory(path: Path, *, label: str) -> None:
    _reject_link_or_reparse(path, label=label)
    try:
        metadata = path.stat(follow_symlinks=False)
    except OSError as error:
        raise WorkerPackError(f"could not inspect {label}: {path}") from error
    if not stat.S_ISDIR(metadata.st_mode):
        raise WorkerPackError(f"{label} must be a real directory: {path}")


def _require_regular_file(path: Path, *, label: str) -> None:
    _reject_link_or_reparse(path, label=label)
    try:
        metadata = path.stat(follow_symlinks=False)
    except OSError as error:
        raise WorkerPackError(f"could not inspect {label}: {path}") from error
    if not stat.S_ISREG(metadata.st_mode):
        raise WorkerPackError(f"{label} must be a regular file: {path}")


def _preflight_free_space(destination: Path, *, expanded_bytes: int, operation: str) -> None:
    headroom = max(
        MIN_FREE_SPACE_HEADROOM_BYTES,
        expanded_bytes * FREE_SPACE_HEADROOM_PERCENT // 100,
    )
    required = expanded_bytes + headroom
    try:
        available = shutil.disk_usage(destination).free
    except OSError as error:
        raise WorkerPackError(
            f"could not determine free space for {operation}: {destination}"
        ) from error
    if available < required:
        raise WorkerPackError(
            f"insufficient free space for {operation}: required at least {required} bytes "
            f"including headroom, available {available} bytes"
        )


def _manifest_json(manifest: WorkerPackManifest) -> bytes:
    return (
        json.dumps(
            manifest.model_dump(mode="json"),
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
        + "\n"
    ).encode("utf-8")


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        while chunk := source.read(COPY_CHUNK_BYTES):
            digest.update(chunk)
    return digest.hexdigest()


def _portable_archive_path(name: str, *, directory: bool = False) -> str:
    candidate = name[:-1] if directory and name.endswith("/") else name
    if not candidate or "\\" in candidate or "\x00" in candidate:
        raise WorkerPackError(f"unsafe archive path: {name!r}")
    path = PurePosixPath(candidate)
    if path.is_absolute() or candidate != path.as_posix() or not path.parts:
        raise WorkerPackError(f"unsafe archive path: {name!r}")
    if any(part in {"", ".", ".."} for part in path.parts):
        raise WorkerPackError(f"unsafe archive path: {name!r}")
    for part in path.parts:
        if part.endswith((" ", ".")) or any(character in '<>:"|?*' for character in part):
            raise WorkerPackError(f"archive path is not portable to Windows: {name!r}")
        if any(ord(character) < 32 for character in part):
            raise WorkerPackError(f"archive path contains a control character: {name!r}")
        stem = part.split(".", 1)[0].upper()
        if stem in {"CON", "PRN", "AUX", "NUL"} or (
            len(stem) == 4 and stem[:3] in {"COM", "LPT"} and stem[3] in "123456789"
        ):
            raise WorkerPackError(f"archive path contains a Windows-reserved name: {name!r}")
    return candidate


def _validate_zip_member(member: zipfile.ZipInfo) -> tuple[str, bool]:
    is_directory = member.is_dir()
    path = _portable_archive_path(member.filename, directory=is_directory)
    mode = member.external_attr >> 16
    file_type = stat.S_IFMT(mode)
    if stat.S_ISLNK(mode) or file_type not in {0, stat.S_IFREG, stat.S_IFDIR}:
        raise WorkerPackError(
            f"archive member is not a regular file or directory: {member.filename!r}"
        )
    if member.flag_bits & 0x1:
        raise WorkerPackError(f"encrypted archive members are not supported: {member.filename!r}")
    return path, is_directory


def _read_manifest(
    archive: zipfile.ZipFile, member: zipfile.ZipInfo
) -> tuple[bytes, WorkerPackManifest]:
    if member.file_size > MAX_MANIFEST_BYTES:
        raise WorkerPackError("worker pack manifest exceeds the size limit")
    try:
        raw = archive.read(member)
        manifest = WorkerPackManifest.model_validate_json(raw)
    except (KeyError, OSError, UnicodeError, ValueError, ValidationError) as error:
        raise WorkerPackError(f"worker pack manifest is invalid: {error}") from error
    return raw, manifest


def _archive_members(archive: zipfile.ZipFile) -> dict[str, zipfile.ZipInfo]:
    members = archive.infolist()
    if len(members) > MAX_ARCHIVE_MEMBER_COUNT:
        raise WorkerPackError(
            "worker pack archive contains too many members: "
            f"maximum {MAX_ARCHIVE_MEMBER_COUNT}, received {len(members)}"
        )
    files: dict[str, zipfile.ZipInfo] = {}
    seen: dict[str, str] = {}
    expanded_bytes = 0
    for member in members:
        path, is_directory = _validate_zip_member(member)
        member_limit = (
            MAX_MANIFEST_BYTES if path.casefold() == MANIFEST_NAME else WORKER_PACK_MAX_FILE_BYTES
        )
        if member.file_size < 0 or member.file_size > member_limit:
            raise WorkerPackError(
                f"archive member exceeds the expanded size limit: {member.filename!r}"
            )
        expanded_bytes += member.file_size
        if expanded_bytes > WORKER_PACK_MAX_EXPANDED_BYTES + MAX_MANIFEST_BYTES:
            raise WorkerPackError(
                "worker pack archive exceeds the total expanded size limit "
                f"of {WORKER_PACK_MAX_EXPANDED_BYTES} bytes"
            )
        folded = path.casefold()
        if folded in seen:
            raise WorkerPackError(
                "archive paths collide on case-insensitive filesystems: "
                f"{seen[folded]!r} and {member.filename!r}"
            )
        seen[folded] = member.filename
        if not is_directory:
            files[path] = member
    return files


def _hash_archive_member(
    archive: zipfile.ZipFile,
    member: zipfile.ZipInfo,
    *,
    expected_size: int,
) -> str:
    digest = hashlib.sha256()
    total = 0
    try:
        with archive.open(member) as source:
            while chunk := source.read(COPY_CHUNK_BYTES):
                total += len(chunk)
                if total > expected_size:
                    raise WorkerPackError(
                        f"archive member exceeds declared size: {member.filename!r}"
                    )
                digest.update(chunk)
    except WorkerPackError:
        raise
    except (OSError, RuntimeError, zipfile.BadZipFile) as error:
        raise WorkerPackError(f"could not read archive member {member.filename!r}") from error
    if total != expected_size:
        raise WorkerPackError(
            f"archive member size mismatch for {member.filename!r}: "
            f"expected {expected_size}, received {total}"
        )
    return digest.hexdigest()


def _read_pe_machine(source: Any, *, size: int, label: str) -> int:
    if size < 70:
        raise WorkerPackError(f"Windows native file is too small to be PE: {label!r}")
    header = source.read(64)
    if len(header) != 64 or header[:2] != b"MZ":
        raise WorkerPackError(f"Windows native file is missing the DOS header: {label!r}")
    pe_offset = struct.unpack_from("<I", header, 0x3C)[0]
    if pe_offset < 64 or pe_offset > size - 6:
        raise WorkerPackError(f"Windows native file has an invalid PE offset: {label!r}")
    source.seek(pe_offset)
    pe_header = source.read(6)
    if len(pe_header) != 6 or pe_header[:4] != b"PE\0\0":
        raise WorkerPackError(f"Windows native file is missing the PE signature: {label!r}")
    return struct.unpack_from("<H", pe_header, 4)[0]


def _verify_archive_pe_machine(
    archive: zipfile.ZipFile,
    member: zipfile.ZipInfo,
    *,
    architecture: str,
) -> None:
    expected_machine = _WINDOWS_PE_MACHINE[architecture]
    try:
        with archive.open(member) as source:
            actual_machine = _read_pe_machine(
                source,
                size=member.file_size,
                label=member.filename,
            )
    except WorkerPackError:
        raise
    except (OSError, RuntimeError, zipfile.BadZipFile) as error:
        raise WorkerPackError(f"could not inspect PE header: {member.filename!r}") from error
    if actual_machine != expected_machine:
        raise WorkerPackError(
            f"Windows PE machine mismatch for {member.filename!r}: "
            f"expected 0x{expected_machine:04x}, received 0x{actual_machine:04x}"
        )


def _verify_installed_pe_machine(path: Path, *, architecture: str, label: str) -> None:
    expected_machine = _WINDOWS_PE_MACHINE[architecture]
    try:
        with path.open("rb") as source:
            actual_machine = _read_pe_machine(source, size=path.stat().st_size, label=label)
    except WorkerPackError:
        raise
    except OSError as error:
        raise WorkerPackError(f"could not inspect installed PE header: {label!r}") from error
    if actual_machine != expected_machine:
        raise WorkerPackError(
            f"Windows PE machine mismatch for {label!r}: "
            f"expected 0x{expected_machine:04x}, received 0x{actual_machine:04x}"
        )


def verify_archive(archive_path: Path) -> VerifiedWorkerPackArchive:
    """Fully verify one offline archive without extracting it."""

    resolved_archive = archive_path.expanduser().resolve(strict=True)
    if not resolved_archive.is_file():
        raise WorkerPackError(f"worker pack archive is not a file: {resolved_archive}")
    archive_sha256 = _sha256_file(resolved_archive)
    try:
        with zipfile.ZipFile(resolved_archive) as archive:
            members = _archive_members(archive)
            manifest_member = members.get(MANIFEST_NAME)
            if manifest_member is None:
                raise WorkerPackError(f"worker pack archive is missing {MANIFEST_NAME}")
            manifest_bytes, manifest = _read_manifest(archive, manifest_member)
            expected = {file.path: file for file in manifest.files}
            actual_payload = set(members) - {MANIFEST_NAME}
            if actual_payload != set(expected):
                missing = sorted(set(expected) - actual_payload)
                unexpected = sorted(actual_payload - set(expected))
                raise WorkerPackError(
                    "archive payload does not match manifest; "
                    f"missing={missing}, unexpected={unexpected}"
                )
            expanded_payload = sum(members[path].file_size for path in expected)
            if expanded_payload > WORKER_PACK_MAX_EXPANDED_BYTES:
                raise WorkerPackError(
                    "worker pack archive exceeds the total expanded size limit "
                    f"of {WORKER_PACK_MAX_EXPANDED_BYTES} bytes"
                )
            for path, file in expected.items():
                member = members[path]
                if member.file_size != file.size:
                    raise WorkerPackError(
                        f"archive member size mismatch for {path!r}: "
                        f"expected {file.size}, received {member.file_size}"
                    )
                actual_sha256 = _hash_archive_member(archive, member, expected_size=file.size)
                if actual_sha256 != file.sha256:
                    raise WorkerPackError(
                        f"archive member checksum mismatch for {path!r}: "
                        f"expected {file.sha256}, received {actual_sha256}"
                    )
                if (
                    manifest.platform.os == "windows"
                    and PurePosixPath(path).suffix.casefold() in _WINDOWS_NATIVE_SUFFIXES
                ):
                    _verify_archive_pe_machine(
                        archive,
                        member,
                        architecture=manifest.platform.architecture,
                    )
    except WorkerPackError:
        raise
    except (OSError, zipfile.BadZipFile, zipfile.LargeZipFile) as error:
        raise WorkerPackError(f"worker pack archive is invalid: {resolved_archive}") from error

    return VerifiedWorkerPackArchive(
        archive_path=resolved_archive,
        archive_sha256=archive_sha256,
        manifest_sha256=hashlib.sha256(manifest_bytes).hexdigest(),
        manifest_bytes=manifest_bytes,
        manifest=manifest,
    )


def _reject_forbidden_staging_path(relative: str) -> None:
    path = PurePosixPath(relative)
    folded_parts = tuple(part.casefold() for part in path.parts)
    if any(part in _FORBIDDEN_STAGING_PARTS for part in folded_parts):
        raise WorkerPackError(f"staging contains a forbidden development path: {relative!r}")
    name = folded_parts[-1]
    if (
        name in _FORBIDDEN_STAGING_NAMES
        or name == ".env"
        or name.startswith(".env.")
        or name.endswith(("-wal", "-shm"))
        or PurePosixPath(name).suffix in _FORBIDDEN_STAGING_SUFFIXES
    ):
        raise WorkerPackError(f"staging contains a forbidden secret or database file: {relative!r}")


def _reject_private_key_material(path: Path, *, relative: str) -> None:
    if path.suffix.casefold() != ".pem":
        return
    try:
        with path.open("rb") as source:
            header = source.read(64 * 1024)
    except OSError as error:
        raise WorkerPackError(f"could not inspect PEM payload: {relative!r}") from error
    if any(marker in header for marker in _PRIVATE_KEY_MARKERS):
        raise WorkerPackError(f"staging contains private-key material: {relative!r}")


def _infer_file_role(path: str, *, executable: str) -> str:
    relative = PurePosixPath(path)
    folded_parts = tuple(part.casefold() for part in relative.parts)
    if path == executable:
        return "runtime"
    if "licenses" in folded_parts or relative.name.casefold().startswith(("license", "notice")):
        return "license"
    if "models" in folded_parts:
        return "model"
    if relative.suffix.casefold() in {".dll", ".dylib", ".pyd", ".so"}:
        return "library"
    if relative.suffix.casefold() == ".exe":
        return "runtime"
    if "metadata" in folded_parts:
        return "metadata"
    return "other"


def _scan_staging(staging: Path, *, executable: str) -> list[dict[str, Any]]:
    _require_real_directory(staging, label="worker pack staging root")
    files: list[dict[str, Any]] = []
    seen: dict[str, str] = {}
    for directory, directory_names, file_names in os.walk(staging, followlinks=False):
        directory_path = Path(directory)
        _require_real_directory(directory_path, label="staging directory")
        for name in [*directory_names, *file_names]:
            candidate = directory_path / name
            relative = candidate.relative_to(staging).as_posix()
            _portable_archive_path(relative)
            _reject_forbidden_staging_path(relative)
            _reject_link_or_reparse(candidate, label=f"staging entry {relative!r}")
        for name in file_names:
            candidate = directory_path / name
            relative = candidate.relative_to(staging).as_posix()
            mode = candidate.stat(follow_symlinks=False).st_mode
            if not stat.S_ISREG(mode):
                raise WorkerPackError(f"staging entry is not a regular file: {relative!r}")
            _reject_private_key_material(candidate, relative=relative)
            folded = relative.casefold()
            if folded in seen:
                raise WorkerPackError(
                    "staging paths collide on case-insensitive filesystems: "
                    f"{seen[folded]!r} and {relative!r}"
                )
            seen[folded] = relative
            files.append(
                {
                    "path": relative,
                    "size": candidate.stat().st_size,
                    "sha256": _sha256_file(candidate),
                    "role": _infer_file_role(relative, executable=executable),
                }
            )
    files.sort(key=lambda value: cast(str, value["path"]))
    return files


def _load_manifest_template(path: Path) -> dict[str, Any]:
    _reject_reparse_ancestors(path, label="manifest template path")
    _require_regular_file(path, label="manifest template")
    try:
        parsed = cast(object, json.loads(path.read_text(encoding="utf-8")))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise WorkerPackError(f"manifest template is invalid JSON: {path}") from error
    if not isinstance(parsed, dict):
        raise WorkerPackError("manifest template must contain a JSON object")
    values = cast(dict[str, Any], parsed)
    if "files" in values:
        raise WorkerPackError("manifest template must not prefill files")
    return values


def _zip_info(path: str, *, executable: bool = False) -> zipfile.ZipInfo:
    info = zipfile.ZipInfo(path, date_time=_DETERMINISTIC_ZIP_TIMESTAMP)
    # Model tensors and media are already dense. Deflating multi-gigabyte files
    # wastes hours and temporary disk while saving almost nothing. Everything
    # else uses the archive's fast level-1 Deflate default.
    suffix = PurePosixPath(path).suffix.casefold()
    info.compress_type = (
        zipfile.ZIP_STORED
        if suffix in _ALREADY_COMPRESSED_OR_LARGE_BINARY_SUFFIXES
        else zipfile.ZIP_DEFLATED
    )
    info.create_system = 3
    info.external_attr = (stat.S_IFREG | (0o700 if executable else 0o600)) << 16
    return info


def build_archive(
    staging: Path, manifest_template: Path, output: Path
) -> VerifiedWorkerPackArchive:
    """Create one deterministic Zip64 pack from an already materialized payload tree."""

    expanded_staging = staging.expanduser()
    _reject_reparse_ancestors(expanded_staging, label="worker pack staging path")
    resolved_staging = expanded_staging.resolve(strict=True)
    expanded_output = output.expanduser()
    _reject_reparse_ancestors(expanded_output.parent, label="worker pack output path")
    resolved_output = expanded_output.resolve()
    if resolved_output.exists() or _path_is_link_or_reparse(resolved_output):
        raise WorkerPackError(f"worker pack output already exists: {resolved_output}")
    if resolved_output.is_relative_to(resolved_staging):
        raise WorkerPackError("worker pack output must be outside the staging directory")
    template = _load_manifest_template(manifest_template.expanduser())
    raw_worker = template.get("worker")
    if not isinstance(raw_worker, dict):
        raise WorkerPackError("manifest template worker must be an object")
    raw_entrypoint = cast(dict[str, Any], raw_worker).get("entrypoint")
    if not isinstance(raw_entrypoint, dict) or not isinstance(
        cast(dict[str, Any], raw_entrypoint).get("executable"), str
    ):
        raise WorkerPackError("manifest template must define worker.entrypoint.executable")
    executable = cast(str, cast(dict[str, Any], raw_entrypoint)["executable"])
    template["files"] = _scan_staging(resolved_staging, executable=executable)
    try:
        manifest = WorkerPackManifest.model_validate(template)
    except ValidationError as error:
        raise WorkerPackError(f"generated worker pack manifest is invalid: {error}") from error
    manifest_bytes = _manifest_json(manifest)

    resolved_output.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    _require_real_directory(resolved_output.parent, label="worker pack output directory")
    _preflight_free_space(
        resolved_output.parent,
        expanded_bytes=sum(file.size for file in manifest.files),
        operation="worker pack build",
    )
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{resolved_output.name}.", suffix=".tmp", dir=resolved_output.parent
    )
    os.close(descriptor)
    temporary = Path(temporary_name)
    temporary.unlink()
    try:
        with zipfile.ZipFile(
            temporary,
            "x",
            compression=zipfile.ZIP_DEFLATED,
            compresslevel=1,
            allowZip64=True,
        ) as archive:
            archive.writestr(_zip_info(MANIFEST_NAME), manifest_bytes)
            for file in manifest.files:
                source = resolved_staging
                path_parts = PurePosixPath(file.path).parts
                for part in path_parts[:-1]:
                    source /= part
                    _require_real_directory(source, label="worker pack staging directory")
                source /= path_parts[-1]
                _require_regular_file(source, label=f"worker pack staging file {file.path!r}")
                info = _zip_info(
                    file.path,
                    executable=file.path == manifest.worker.entrypoint.executable,
                )
                with (
                    source.open("rb") as input_file,
                    archive.open(info, "w", force_zip64=True) as output_file,
                ):
                    shutil.copyfileobj(input_file, output_file, length=COPY_CHUNK_BYTES)
        verified = verify_archive(temporary)
        os.replace(temporary, resolved_output)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise
    return VerifiedWorkerPackArchive(
        archive_path=resolved_output,
        archive_sha256=verified.archive_sha256,
        manifest_sha256=verified.manifest_sha256,
        manifest_bytes=verified.manifest_bytes,
        manifest=verified.manifest,
    )


def _extract_verified_payload(
    verified: VerifiedWorkerPackArchive,
    destination: Path,
) -> None:
    _require_real_directory(destination, label="worker pack installation staging")
    with zipfile.ZipFile(verified.archive_path) as archive:
        members = _archive_members(archive)
        for file in verified.manifest.files:
            member = members[file.path]
            target = destination.joinpath(*PurePosixPath(file.path).parts)
            current = destination
            for part in PurePosixPath(file.path).parts[:-1]:
                current /= part
                _reject_link_or_reparse(current, label="worker pack payload directory")
                if not current.exists():
                    current.mkdir(mode=0o700)
                _require_real_directory(current, label="worker pack payload directory")
            if target.exists() or _path_is_link_or_reparse(target):
                raise WorkerPackError(
                    f"worker pack extraction target already exists: {file.path!r}"
                )
            digest = hashlib.sha256()
            total = 0
            try:
                with archive.open(member) as source, target.open("xb") as output:
                    while chunk := source.read(COPY_CHUNK_BYTES):
                        total += len(chunk)
                        if total > file.size:
                            raise WorkerPackError(
                                f"archive member exceeds declared size: {file.path!r}"
                            )
                        digest.update(chunk)
                        output.write(chunk)
                    output.flush()
                    os.fsync(output.fileno())
            except WorkerPackError:
                raise
            except (OSError, RuntimeError, zipfile.BadZipFile) as error:
                raise WorkerPackError(f"could not extract archive member {file.path!r}") from error
            if total != file.size or digest.hexdigest() != file.sha256:
                raise WorkerPackError(f"archive member changed during installation: {file.path!r}")
            target.chmod(
                0o700 if file.path == verified.manifest.worker.entrypoint.executable else 0o600
            )
            _require_regular_file(target, label=f"installed payload {file.path!r}")


def _write_json_file(path: Path, value: Any, *, exclusive: bool) -> None:
    mode = "x" if exclusive else "w"
    with path.open(mode, encoding="utf-8", newline="\n") as output:
        json.dump(value, output, ensure_ascii=False, indent=2)
        output.write("\n")
        output.flush()
        os.fsync(output.fileno())
    path.chmod(0o600)


def install_archive(archive_path: Path, root: Path) -> InstalledWorkerPack:
    """Install an archive through same-volume staging and one final rename."""

    verified = verify_archive(archive_path)
    expanded_root = root.expanduser()
    _reject_reparse_ancestors(expanded_root, label="worker pack installation path")
    resolved_root = expanded_root.resolve()
    resolved_root.mkdir(parents=True, exist_ok=True, mode=0o700)
    _require_real_directory(resolved_root, label="worker pack root")
    pack_root = resolved_root / verified.manifest.pack_id
    _reject_link_or_reparse(pack_root, label="worker pack namespace")
    pack_root.mkdir(exist_ok=True, mode=0o700)
    _require_real_directory(pack_root, label="worker pack namespace")
    _preflight_free_space(
        pack_root,
        expanded_bytes=sum(file.size for file in verified.manifest.files),
        operation="worker pack installation",
    )
    target = pack_root / verified.manifest.version
    if target.exists() or _path_is_link_or_reparse(target):
        raise WorkerPackError(f"worker pack is already installed: {target}")

    temporary = Path(tempfile.mkdtemp(prefix=".install-", dir=pack_root))
    renamed = False
    try:
        _extract_verified_payload(verified, temporary)
        manifest_path = temporary / MANIFEST_NAME
        with manifest_path.open("xb") as output:
            output.write(verified.manifest_bytes)
            output.flush()
            os.fsync(output.fileno())
        manifest_path.chmod(0o600)
        receipt = WorkerPackInstallReceipt(
            pack_id=verified.manifest.pack_id,
            version=verified.manifest.version,
            manifest_sha256=verified.manifest_sha256,
            archive_sha256=verified.archive_sha256,
            installed_at=datetime.now(_UTC),
            verified_file_count=len(verified.manifest.files),
        )
        _write_json_file(
            temporary / RECEIPT_NAME,
            receipt.model_dump(mode="json"),
            exclusive=True,
        )
        # The staging directory lives inside pack_root, so the final rename never crosses volumes.
        if target.exists() or _path_is_link_or_reparse(target):
            raise WorkerPackError(
                f"worker pack installation target appeared during install: {target}"
            )
        os.replace(temporary, target)
        renamed = True
        installed = load_installed_pack(target, verify_payload=True)
    except BaseException:
        if temporary.exists():
            shutil.rmtree(temporary, ignore_errors=True)
        if renamed and target.exists() and not _path_is_link_or_reparse(target):
            shutil.rmtree(target, ignore_errors=True)
        raise

    return installed


def _read_bounded_regular_file(path: Path, *, label: str, maximum_bytes: int) -> bytes:
    _require_regular_file(path, label=label)
    try:
        size = path.stat(follow_symlinks=False).st_size
        if size > maximum_bytes:
            raise WorkerPackError(f"{label} exceeds the size limit: {path}")
        return path.read_bytes()
    except WorkerPackError:
        raise
    except OSError as error:
        raise WorkerPackError(f"could not read {label}: {path}") from error


def _expected_payload_directories(manifest: WorkerPackManifest) -> set[str]:
    directories: set[str] = set()
    for file in manifest.files:
        parent = PurePosixPath(file.path).parent
        while parent != PurePosixPath("."):
            directories.add(parent.as_posix())
            parent = parent.parent
    return directories


def _verify_declared_installed_paths(resolved: Path, manifest: WorkerPackManifest) -> None:
    """Reject missing or redirecting declared paths without paying the hash cost."""

    for file in manifest.files:
        payload = resolved
        parts = PurePosixPath(file.path).parts
        for index, part in enumerate(parts):
            payload /= part
            _reject_link_or_reparse(payload, label=f"installed payload {file.path!r}")
            if index < len(parts) - 1:
                _require_real_directory(payload, label=f"installed payload directory {file.path!r}")
        _require_regular_file(payload, label=f"installed payload {file.path!r}")


def _verify_installed_payload_tree(resolved: Path, manifest: WorkerPackManifest) -> None:
    expected_files = {
        MANIFEST_NAME.casefold(): MANIFEST_NAME,
        RECEIPT_NAME.casefold(): RECEIPT_NAME,
        **{file.path.casefold(): file.path for file in manifest.files},
    }
    expected_directories = {
        directory.casefold(): directory for directory in _expected_payload_directories(manifest)
    }
    observed_files: dict[str, str] = {}
    observed_directories: dict[str, str] = {}
    for directory, directory_names, file_names in os.walk(resolved, followlinks=False):
        directory_path = Path(directory)
        _require_real_directory(directory_path, label="installed worker pack directory")
        for name in directory_names:
            candidate = directory_path / name
            relative = candidate.relative_to(resolved).as_posix()
            _portable_archive_path(relative)
            _require_real_directory(candidate, label=f"installed directory {relative!r}")
            folded = relative.casefold()
            if folded in observed_directories:
                raise WorkerPackError(
                    "installed worker pack directories collide on a case-insensitive "
                    f"filesystem: {observed_directories[folded]!r} and {relative!r}"
                )
            observed_directories[folded] = relative
        for name in file_names:
            candidate = directory_path / name
            relative = candidate.relative_to(resolved).as_posix()
            _portable_archive_path(relative)
            _require_regular_file(candidate, label=f"installed file {relative!r}")
            folded = relative.casefold()
            if folded in observed_files:
                raise WorkerPackError(
                    "installed worker pack files collide on a case-insensitive filesystem: "
                    f"{observed_files[folded]!r} and {relative!r}"
                )
            observed_files[folded] = relative
    if set(observed_files) != set(expected_files):
        missing = sorted(expected_files[key] for key in set(expected_files) - set(observed_files))
        unexpected = sorted(
            observed_files[key] for key in set(observed_files) - set(expected_files)
        )
        raise WorkerPackError(
            "installed worker pack files do not match manifest; "
            f"missing={missing}, unexpected={unexpected}"
        )
    unexpected_directories = sorted(
        observed_directories[key] for key in set(observed_directories) - set(expected_directories)
    )
    if unexpected_directories:
        raise WorkerPackError(
            f"installed worker pack contains undeclared directories: {unexpected_directories}"
        )

    for file in manifest.files:
        payload = resolved.joinpath(*PurePosixPath(file.path).parts)
        size = payload.stat(follow_symlinks=False).st_size
        if size != file.size or _sha256_file(payload) != file.sha256:
            raise WorkerPackError(f"installed worker pack file checksum mismatch: {file.path!r}")
        if (
            manifest.platform.os == "windows"
            and PurePosixPath(file.path).suffix.casefold() in _WINDOWS_NATIVE_SUFFIXES
        ):
            _verify_installed_pe_machine(
                payload,
                architecture=manifest.platform.architecture,
                label=file.path,
            )


def load_installed_pack(path: Path, *, verify_payload: bool = False) -> InstalledWorkerPack:
    """Load an installed pack and optionally verify its complete immutable file tree."""

    expanded = path.expanduser()
    _reject_reparse_ancestors(expanded, label="installed worker pack path")
    try:
        resolved = expanded.resolve(strict=True)
    except OSError as error:
        raise WorkerPackError(f"installed worker pack path is invalid: {path}") from error
    _require_real_directory(resolved, label="installed worker pack")
    manifest_path = resolved / MANIFEST_NAME
    receipt_path = resolved / RECEIPT_NAME
    try:
        manifest_bytes = _read_bounded_regular_file(
            manifest_path,
            label="installed worker pack manifest",
            maximum_bytes=MAX_MANIFEST_BYTES,
        )
        receipt_bytes = _read_bounded_regular_file(
            receipt_path,
            label="installed worker pack receipt",
            maximum_bytes=MAX_RECEIPT_BYTES,
        )
        manifest = WorkerPackManifest.model_validate_json(manifest_bytes)
        receipt = WorkerPackInstallReceipt.model_validate_json(receipt_bytes)
    except (WorkerPackError, ValueError, ValidationError) as error:
        if isinstance(error, WorkerPackError):
            raise
        raise WorkerPackError(f"installed worker pack metadata is invalid: {resolved}") from error
    manifest_sha256 = hashlib.sha256(manifest_bytes).hexdigest()
    if receipt.pack_id != manifest.pack_id or receipt.version != manifest.version:
        raise WorkerPackError(f"installed worker pack receipt identity mismatch: {resolved}")
    if receipt.manifest_sha256 != manifest_sha256:
        raise WorkerPackError(f"installed worker pack manifest checksum mismatch: {resolved}")
    if receipt.verified_file_count != len(manifest.files):
        raise WorkerPackError(f"installed worker pack receipt file count mismatch: {resolved}")
    if resolved.parent.name != manifest.pack_id or resolved.name != manifest.version:
        raise WorkerPackError(f"installed worker pack directory identity mismatch: {resolved}")
    _verify_declared_installed_paths(resolved, manifest)
    if verify_payload:
        _verify_installed_payload_tree(resolved, manifest)
    return InstalledWorkerPack(root=resolved, manifest=manifest, receipt=receipt)


def discover_installed_packs(
    root: Path, *, verify_payload: bool = False
) -> tuple[list[InstalledWorkerPack], list[str]]:
    """Return valid receipts and bounded diagnostics for malformed install directories."""

    expanded_root = root.expanduser()
    _reject_reparse_ancestors(expanded_root, label="worker pack discovery path")
    resolved_root = expanded_root.resolve()
    if not resolved_root.exists():
        return [], []
    _require_real_directory(resolved_root, label="worker pack root")
    packs: list[InstalledWorkerPack] = []
    errors: list[str] = []
    candidate_roots: list[Path] = []
    try:
        namespaces = sorted(resolved_root.iterdir(), key=lambda value: value.name.casefold())
    except OSError as error:
        raise WorkerPackError(f"could not enumerate worker pack root: {resolved_root}") from error
    if len(namespaces) > WORKER_PACK_MAX_FILE_COUNT:
        raise WorkerPackError("worker pack root contains too many namespace entries")
    for namespace in namespaces:
        try:
            _reject_link_or_reparse(namespace, label="worker pack namespace")
            if not namespace.is_dir():
                continue
            versions = sorted(namespace.iterdir(), key=lambda value: value.name.casefold())
            if len(versions) > WORKER_PACK_MAX_FILE_COUNT:
                raise WorkerPackError(
                    f"worker pack namespace contains too many versions: {namespace}"
                )
            for version in versions:
                if version.name.startswith(".install-"):
                    continue
                _reject_link_or_reparse(version, label="installed worker pack version")
                if version.is_dir() and (version / MANIFEST_NAME).exists():
                    candidate_roots.append(version)
        except WorkerPackError as error:
            errors.append(str(error)[:500])
    for candidate in candidate_roots:
        try:
            packs.append(load_installed_pack(candidate, verify_payload=verify_payload))
        except WorkerPackError as error:
            errors.append(str(error)[:500])
    packs.sort(
        key=lambda item: (item.manifest.worker.kind, item.manifest.pack_id, item.manifest.version)
    )
    return packs, errors


def semver_sort_key(
    version: str,
) -> tuple[int, int, int, int, tuple[tuple[int, int | str], ...]]:
    """Return a SemVer precedence key, excluding build metadata."""

    without_build = version.split("+", 1)[0]
    core, separator, prerelease = without_build.partition("-")
    major, minor, patch = (int(part) for part in core.split("."))
    prerelease_parts: tuple[tuple[int, int | str], ...] = tuple(
        (0, int(part)) if part.isdigit() else (1, part) for part in prerelease.split(".") if part
    )
    return major, minor, patch, 1 if not separator else 0, prerelease_parts


def _load_activation_config(config_path: Path) -> WorkerPackActivationConfig:
    if not config_path.exists():
        return WorkerPackActivationConfig()
    _require_regular_file(config_path, label="activation config")
    try:
        return WorkerPackActivationConfig.model_validate_json(config_path.read_bytes())
    except (OSError, ValueError, ValidationError) as error:
        raise WorkerPackError(f"activation config is invalid: {config_path}") from error


def activate_pack(
    pack_id: str,
    *,
    root: Path,
    config_root: Path,
    version: str | None = None,
) -> tuple[InstalledWorkerPack, Path]:
    """Select one installed pack for its worker kind using atomic replacement."""

    packs, errors = discover_installed_packs(root, verify_payload=True)
    candidates = [
        pack
        for pack in packs
        if pack.manifest.pack_id == pack_id
        and (version is None or pack.manifest.version == version)
    ]
    if not candidates:
        detail = f"; invalid installs: {errors}" if errors else ""
        requested = f"{pack_id}@{version}" if version else pack_id
        raise WorkerPackError(f"installed worker pack was not found: {requested}{detail}")
    selected = max(candidates, key=lambda item: semver_sort_key(item.manifest.version))
    expanded_config_root = config_root.expanduser()
    _reject_reparse_ancestors(expanded_config_root, label="worker pack config path")
    resolved_config_root = expanded_config_root.resolve()
    resolved_config_root.mkdir(parents=True, exist_ok=True, mode=0o700)
    _require_real_directory(resolved_config_root, label="worker pack config root")
    config_path = resolved_config_root / SELECTION_NAME
    current = _load_activation_config(config_path)
    selection = WorkerPackSelection(
        pack_id=selected.manifest.pack_id,
        version=selected.manifest.version,
    )
    active_values = current.active.model_dump()
    active_values[selected.manifest.worker.kind] = selection
    updated = WorkerPackActivationConfig(
        active=WorkerPackActiveSelection.model_validate(active_values)
    )
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{SELECTION_NAME}.", suffix=".tmp", dir=resolved_config_root
    )
    os.close(descriptor)
    temporary = Path(temporary_name)
    try:
        _write_json_file(temporary, updated.model_dump(mode="json"), exclusive=False)
        _reject_link_or_reparse(config_path, label="activation config")
        os.replace(temporary, config_path)
    finally:
        temporary.unlink(missing_ok=True)
    return selected, config_path


def _pack_json(pack: InstalledWorkerPack) -> dict[str, Any]:
    return {
        "pack_id": pack.manifest.pack_id,
        "version": pack.manifest.version,
        "kind": pack.manifest.worker.kind,
        "backend": pack.manifest.worker.backend,
        "provider_id": pack.manifest.worker.provider_id,
        "platform": pack.manifest.platform.model_dump(mode="json"),
        "path": str(pack.root),
        "installed_at": pack.receipt.installed_at.isoformat(),
        "archive_sha256": pack.receipt.archive_sha256,
    }


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subcommands = parser.add_subparsers(dest="command", required=True)
    build = subcommands.add_parser(
        "build", help="build and fully verify a deterministic offline worker pack"
    )
    build.add_argument("--staging", type=Path, required=True)
    build.add_argument("--manifest-template", type=Path, required=True)
    build.add_argument("--output", type=Path, required=True)
    build.add_argument("--json", action="store_true")
    verify = subcommands.add_parser("verify", help="fully verify an offline worker pack ZIP")
    verify.add_argument("archive", type=Path)
    verify.add_argument("--json", action="store_true")
    install = subcommands.add_parser("install", help="atomically install a verified worker pack")
    install.add_argument("archive", type=Path)
    install.add_argument("--root", type=Path, required=True)
    install.add_argument("--json", action="store_true")
    listing = subcommands.add_parser("list", help="list installed worker packs with valid receipts")
    listing.add_argument("--root", type=Path, required=True)
    listing.add_argument("--json", action="store_true")
    activate = subcommands.add_parser(
        "activate", help="activate an installed pack for its worker kind"
    )
    activate.add_argument("pack_id")
    activate.add_argument("--version")
    activate.add_argument("--root", type=Path, required=True)
    activate.add_argument("--config-root", type=Path, required=True)
    activate.add_argument("--json", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    try:
        if args.command == "build":
            built = build_archive(
                cast(Path, args.staging),
                cast(Path, args.manifest_template),
                cast(Path, args.output),
            )
            result: dict[str, Any] = {
                "pack_id": built.manifest.pack_id,
                "version": built.manifest.version,
                "kind": built.manifest.worker.kind,
                "files": len(built.manifest.files),
                "path": str(built.archive_path),
                "archive_sha256": built.archive_sha256,
                "manifest_sha256": built.manifest_sha256,
            }
            _print_result(result, as_json=cast(bool, args.json), action="built")
            return 0
        if args.command == "verify":
            verified = verify_archive(cast(Path, args.archive))
            result = {
                "pack_id": verified.manifest.pack_id,
                "version": verified.manifest.version,
                "kind": verified.manifest.worker.kind,
                "files": len(verified.manifest.files),
                "archive_sha256": verified.archive_sha256,
                "manifest_sha256": verified.manifest_sha256,
            }
            _print_result(result, as_json=cast(bool, args.json), action="verified")
            return 0
        if args.command == "install":
            installed = install_archive(cast(Path, args.archive), cast(Path, args.root))
            _print_result(_pack_json(installed), as_json=cast(bool, args.json), action="installed")
            return 0
        if args.command == "list":
            packs, errors = discover_installed_packs(cast(Path, args.root))
            if args.json:
                print(
                    json.dumps(
                        {"packs": [_pack_json(pack) for pack in packs], "errors": errors},
                        ensure_ascii=False,
                        indent=2,
                    )
                )
            else:
                for pack in packs:
                    print(
                        f"{pack.manifest.pack_id}@{pack.manifest.version} "
                        f"{pack.manifest.worker.kind}/{pack.manifest.worker.backend} {pack.root}"
                    )
                for error in errors:
                    print(f"invalid: {error}")
            return 1 if errors else 0
        if args.command == "activate":
            selected, config_path = activate_pack(
                cast(str, args.pack_id),
                version=cast(str | None, args.version),
                root=cast(Path, args.root),
                config_root=cast(Path, args.config_root),
            )
            result = {**_pack_json(selected), "config_path": str(config_path)}
            _print_result(result, as_json=cast(bool, args.json), action="activated")
            return 0
    except (OSError, WorkerPackError) as error:
        parser.exit(2, f"worker-packs: error: {error}\n")
    raise AssertionError(f"unhandled command: {args.command}")


def _print_result(
    value: dict[str, Any],
    *,
    as_json: bool,
    action: Literal["built", "verified", "installed", "activated"],
) -> None:
    if as_json:
        print(json.dumps(value, ensure_ascii=False, indent=2))
        return
    print(f"{action}: {value['pack_id']}@{value['version']} ({value['kind']})")


if __name__ == "__main__":
    raise SystemExit(main())
