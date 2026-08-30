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
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import Any, Literal, cast

from pydantic import ValidationError

from chatwaifu_model_worker.packages import (
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
COPY_CHUNK_BYTES = 4 * 1024 * 1024
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
        "credentials.json",
        "install-receipt.json",
        "manifest.json",
        "thumbs.db",
    }
)
_FORBIDDEN_STAGING_SUFFIXES = frozenset(
    {".db", ".key", ".p12", ".pem", ".pfx", ".sqlite", ".sqlite3", ".token"}
)
_DETERMINISTIC_ZIP_TIMESTAMP = (1980, 1, 1, 0, 0, 0)


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
    files: dict[str, zipfile.ZipInfo] = {}
    seen: dict[str, str] = {}
    for member in archive.infolist():
        path, is_directory = _validate_zip_member(member)
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
    if staging.is_symlink() or not staging.is_dir():
        raise WorkerPackError(f"worker pack staging root must be a real directory: {staging}")
    files: list[dict[str, Any]] = []
    seen: dict[str, str] = {}
    for directory, directory_names, file_names in os.walk(staging, followlinks=False):
        directory_path = Path(directory)
        for name in [*directory_names, *file_names]:
            candidate = directory_path / name
            relative = candidate.relative_to(staging).as_posix()
            _portable_archive_path(relative)
            _reject_forbidden_staging_path(relative)
            if candidate.is_symlink():
                raise WorkerPackError(f"staging must not contain symlinks: {relative!r}")
        for name in file_names:
            candidate = directory_path / name
            relative = candidate.relative_to(staging).as_posix()
            mode = candidate.stat(follow_symlinks=False).st_mode
            if not stat.S_ISREG(mode):
                raise WorkerPackError(f"staging entry is not a regular file: {relative!r}")
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
    if path.is_symlink() or not path.is_file():
        raise WorkerPackError(f"manifest template must be a regular JSON file: {path}")
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

    resolved_staging = staging.expanduser().resolve(strict=True)
    resolved_output = output.expanduser().resolve()
    if resolved_output.exists() or resolved_output.is_symlink():
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
                source = resolved_staging.joinpath(*PurePosixPath(file.path).parts)
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
    with zipfile.ZipFile(verified.archive_path) as archive:
        members = _archive_members(archive)
        for file in verified.manifest.files:
            member = members[file.path]
            target = destination.joinpath(*PurePosixPath(file.path).parts)
            target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
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
    if expanded_root.is_symlink():
        raise WorkerPackError(f"worker pack root must not be a symlink: {expanded_root}")
    resolved_root = expanded_root.resolve()
    resolved_root.mkdir(parents=True, exist_ok=True, mode=0o700)
    pack_root = resolved_root / verified.manifest.pack_id
    if pack_root.is_symlink():
        raise WorkerPackError(f"worker pack namespace must not be a symlink: {pack_root}")
    pack_root.mkdir(exist_ok=True, mode=0o700)
    target = pack_root / verified.manifest.version
    if target.exists() or target.is_symlink():
        raise WorkerPackError(f"worker pack is already installed: {target}")

    temporary = Path(tempfile.mkdtemp(prefix=".install-", dir=pack_root))
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
            installed_at=datetime.now(UTC),
            verified_file_count=len(verified.manifest.files),
        )
        _write_json_file(
            temporary / RECEIPT_NAME,
            receipt.model_dump(mode="json"),
            exclusive=True,
        )
        # The staging directory lives inside pack_root, so the final rename never crosses volumes.
        os.replace(temporary, target)
    except BaseException:
        if temporary.exists():
            shutil.rmtree(temporary, ignore_errors=True)
        raise

    return InstalledWorkerPack(root=target, manifest=verified.manifest, receipt=receipt)


def load_installed_pack(path: Path, *, verify_payload: bool = False) -> InstalledWorkerPack:
    """Load an installed pack and optionally re-hash every immutable payload file."""

    if path.is_symlink() or path.parent.is_symlink():
        raise WorkerPackError(f"installed worker pack is not a real directory: {path}")
    resolved = path.resolve(strict=True)
    if not resolved.is_dir():
        raise WorkerPackError(f"installed worker pack is not a real directory: {path}")
    manifest_path = resolved / MANIFEST_NAME
    receipt_path = resolved / RECEIPT_NAME
    if manifest_path.is_symlink() or receipt_path.is_symlink():
        raise WorkerPackError(f"installed worker pack metadata must not be symlinked: {resolved}")
    try:
        manifest_bytes = manifest_path.read_bytes()
        manifest = WorkerPackManifest.model_validate_json(manifest_bytes)
        receipt = WorkerPackInstallReceipt.model_validate_json(receipt_path.read_bytes())
    except (OSError, ValueError, ValidationError) as error:
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
    if verify_payload:
        for file in manifest.files:
            payload = resolved
            for part in PurePosixPath(file.path).parts:
                payload /= part
                if payload.is_symlink():
                    raise WorkerPackError(f"installed worker pack path is symlinked: {file.path!r}")
            if payload.is_symlink() or not payload.is_file():
                raise WorkerPackError(f"installed worker pack file is missing: {file.path!r}")
            if payload.stat().st_size != file.size or _sha256_file(payload) != file.sha256:
                raise WorkerPackError(
                    f"installed worker pack file checksum mismatch: {file.path!r}"
                )
            if (
                manifest.platform.os == "windows"
                and PurePosixPath(file.path).suffix.casefold() in _WINDOWS_NATIVE_SUFFIXES
            ):
                _verify_installed_pe_machine(
                    payload,
                    architecture=manifest.platform.architecture,
                    label=file.path,
                )
    return InstalledWorkerPack(root=resolved, manifest=manifest, receipt=receipt)


def discover_installed_packs(root: Path) -> tuple[list[InstalledWorkerPack], list[str]]:
    """Return valid receipts and bounded diagnostics for malformed install directories."""

    expanded_root = root.expanduser()
    if expanded_root.is_symlink():
        raise WorkerPackError(f"worker pack root must be a real directory: {expanded_root}")
    resolved_root = expanded_root.resolve()
    if not resolved_root.exists():
        return [], []
    if resolved_root.is_symlink() or not resolved_root.is_dir():
        raise WorkerPackError(f"worker pack root must be a real directory: {resolved_root}")
    packs: list[InstalledWorkerPack] = []
    errors: list[str] = []
    for manifest_path in sorted(resolved_root.glob("*/*/manifest.json")):
        try:
            packs.append(load_installed_pack(manifest_path.parent))
        except WorkerPackError as error:
            errors.append(str(error)[:500])
    packs.sort(
        key=lambda item: (item.manifest.worker.kind, item.manifest.pack_id, item.manifest.version)
    )
    return packs, errors


def _semver_sort_key(version: str) -> tuple[int, int, int, int, tuple[tuple[int, int | str], ...]]:
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
    if config_path.is_symlink() or not config_path.is_file():
        raise WorkerPackError(f"activation config must be a regular file: {config_path}")
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

    packs, errors = discover_installed_packs(root)
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
    selected = max(candidates, key=lambda item: _semver_sort_key(item.manifest.version))
    expanded_config_root = config_root.expanduser()
    if expanded_config_root.is_symlink():
        raise WorkerPackError(f"config root must not be a symlink: {expanded_config_root}")
    resolved_config_root = expanded_config_root.resolve()
    resolved_config_root.mkdir(parents=True, exist_ok=True, mode=0o700)
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
        if config_path.is_symlink():
            raise WorkerPackError(f"refusing to replace symlinked activation config: {config_path}")
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
