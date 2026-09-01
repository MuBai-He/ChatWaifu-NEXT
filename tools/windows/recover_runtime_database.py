"""Recover durable Runtime state into a new canonical SQLite database.

This is an operator tool for a stopped Runtime.  It never opens the original
database through SQLite and never replaces it: the original main/WAL/SHM/journal
family is first copied byte-for-byte, recovery reads a disposable copy of that
backup, and the requested target must not already exist.
"""

from __future__ import annotations

import argparse
import base64
import ctypes
import hashlib
import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import tempfile
from collections.abc import Generator
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import cast
from uuid import UUID

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
RUNTIME_SOURCE = REPOSITORY_ROOT / "services" / "runtime" / "src"
if str(RUNTIME_SOURCE) not in sys.path:
    sys.path.insert(0, str(RUNTIME_SOURCE))

from chatwaifu_runtime.persistence.migrations import MIGRATIONS  # noqa: E402

type SqlValue = int | float | str | bytes | None

# This is deliberately an allowlist rather than "all non-transient tables".
# Order also documents the intended dependency order for insertion.
DURABLE_TABLES: tuple[str, ...] = (
    "turns",
    "generations",
    "skill_plugins",
    "mcp_connections",
    "skill_runs",
    "permission_requests",
    "permission_grants",
    "skill_tool_calls",
    "memory_records",
    "memory_proposals",
    "memory_sources",
    "model_role_configs",
    "character_states",
    "relationship_states",
    "companion_settings",
    "ambient_actions",
    "tts_cloud_configs",
    "tts_provider_configs",
    "channel_connections",
    "channel_bindings",
    "channel_turns",
    "channel_deliveries",
    "channel_adapter_checkpoints",
)

# These tables must be empty in the recovered database.  In particular, an
# outbox is durable during normal operation but unsafe to replay during salvage.
TRANSIENT_TABLES: tuple[str, ...] = (
    "outbox",
    "playback_segments",
    "playback_ack_commands",
)

# These are inspected for loss accounting, but current canonical state is not
# copied. FTS is rebuilt by schema triggers; embeddings are a rebuildable cache;
# memory_items is the legacy predecessor of memory_records.
REBUILDABLE_TABLES: tuple[str, ...] = ("memory_embeddings",)
LEGACY_TABLES: tuple[str, ...] = ("memory_items",)

_IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_SESSION_FIELDS = frozenset(("session_id", "source_session_id"))
_FAMILY_SUFFIXES = ("", "-wal", "-shm", "-journal")


class RecoveryError(RuntimeError):
    """Raised when recovery cannot prove that the result is safe."""


@dataclass(frozen=True)
class FamilyFile:
    suffix: str
    name: str
    size: int
    mtime_ns: int
    device: int
    inode: int
    sha256: str


@dataclass(frozen=True)
class AliasAudit:
    selected_path: str
    selected_final_path: str
    alternate_main_paths: tuple[str, ...]
    physical_local_app_data: str | None
    package_local_cache_family_paths: tuple[str, ...]


@dataclass(frozen=True)
class TableSnapshot:
    columns: tuple[str, ...]
    rows: tuple[tuple[SqlValue, ...], ...]
    sha256: str

    @property
    def count(self) -> int:
        return len(self.rows)

    def dictionaries(self) -> tuple[dict[str, SqlValue], ...]:
        return tuple(dict(zip(self.columns, row, strict=True)) for row in self.rows)


@dataclass(frozen=True)
class SessionRecovery:
    snapshot: TableSnapshot
    copied: int
    reconstructed: int
    adjusted: int
    reconstructed_id_sha256: tuple[str, ...]
    lookup_not_found: int
    lookup_corrupt: int
    lookup_corrupt_id_sha256: tuple[str, ...]


def recover_runtime_database(
    source: Path,
    target: Path,
    backup_directory: Path,
    *,
    runtime_stopped: bool,
) -> dict[str, object]:
    """Recover a stopped Runtime database without modifying or replacing it."""

    source = source.absolute()
    target = target.absolute()
    backup_directory = backup_directory.absolute()
    _validate_paths(source, target, backup_directory, runtime_stopped=runtime_stopped)

    started_at = _utc_now()
    source_family: dict[str, FamilyFile] | None = None
    published_identity: tuple[int, int, int, str] | None = None
    success_report_identity: tuple[int, int, int, str] | None = None
    raw_backup = backup_directory / "source-family"

    with _source_family_guard(source) as source_guard_policy:
        alias_audit = _audit_source_aliases(source)
        _validate_alias_output_paths(alias_audit, target, backup_directory)
        try:
            backup_directory.mkdir()
            raw_backup.mkdir()
            source_family = _backup_stable_family(source, raw_backup)
            with tempfile.TemporaryDirectory(
                prefix="chatwaifu-db-recovery-", dir=target.parent
            ) as temporary_name:
                temporary = Path(temporary_name)
                snapshot_database = _copy_family_to_workspace(source, raw_backup, temporary)
                building_target = temporary / "recovered.sqlite3"
                report = _recover_snapshot(
                    snapshot_database,
                    building_target,
                    source=source,
                    target=target,
                    backup_directory=backup_directory,
                    source_family=source_family,
                    started_at=started_at,
                )

                final_source_family = _snapshot_family(source)
                _assert_same_family(
                    source_family, final_source_family, "source changed during recovery"
                )
                if _audit_source_aliases(source) != alias_audit:
                    raise RecoveryError("source main-file aliases changed during recovery")
                _verify_raw_backup(source_family, raw_backup, source.name)
                published_identity = _file_identity(building_target)
                _publish_new_target(building_target, target)
                if os.name == "nt" and alias_audit.physical_local_app_data is not None:
                    try:
                        final_target = _windows_path_for_namespace_policy(
                            "published target", target, existing=True
                        )
                    except RecoveryError as error:
                        raise RecoveryError(
                            "published target entered an unsafe Windows namespace; "
                            "refusing to bless a redirected database: "
                            f"{error}"
                        ) from error
                    redirected_target_paths = _windows_package_local_cache_family_paths(
                        final_target, Path(alias_audit.physical_local_app_data)
                    )
                    if redirected_target_paths:
                        raise RecoveryError(
                            "published target entered a Package LocalCache layer; "
                            "refusing to bless a redirected database: "
                            + ", ".join(redirected_target_paths)
                        )
                if not _matches_file_identity(target, published_identity):
                    raise RecoveryError("published target changed before it could be verified")

            _assert_same_family(
                source_family,
                _snapshot_family(source),
                "source changed after target publication",
            )
            if _audit_source_aliases(source) != alias_audit:
                raise RecoveryError("source main-file aliases changed after target publication")
            if not _matches_file_identity(target, published_identity):
                raise RecoveryError("published target changed before report creation")
            report["completed_at"] = _utc_now()
            report["source_exclusion_policy"] = source_guard_policy
            report["source_alias_audit"] = asdict(alias_audit)
            report["target_file"] = asdict(_file_metadata(target, suffix=""))
            success_report_identity = _write_json_exclusive(
                backup_directory / "recovery-report.json", report
            )
            if not _matches_file_identity(target, published_identity):
                raise RecoveryError("published target changed while its report was written")
            return report
        except BaseException as error:
            if published_identity is not None and _matches_file_identity(
                target, published_identity
            ):
                target.unlink()
            success_report_path = backup_directory / "recovery-report.json"
            if success_report_identity is not None and _matches_file_identity(
                success_report_path, success_report_identity
            ):
                success_report_path.unlink()
            failure_report: dict[str, object] = {
                "schema_version": "1.0",
                "status": "failed",
                "started_at": started_at,
                "failed_at": _utc_now(),
                "source": str(source),
                "target": str(target),
                "source_exclusion_policy": source_guard_policy,
                "source_alias_audit": asdict(alias_audit),
                "error_type": type(error).__name__,
                "error": str(error),
            }
            if source_family is not None:
                failure_report["source_family"] = _family_report(source_family)
                try:
                    failure_report["raw_backup_family"] = _family_report(
                        _snapshot_family(raw_backup / source.name),
                        root=raw_backup,
                    )
                except (OSError, RecoveryError) as backup_error:
                    failure_report["raw_backup_metadata_error"] = str(backup_error)
            failure_path = backup_directory / "recovery-failure.json"
            if not failure_path.exists():
                try:
                    _write_json_exclusive(failure_path, failure_report)
                except (OSError, RecoveryError):
                    pass
            raise


def _validate_paths(
    source: Path,
    target: Path,
    backup_directory: Path,
    *,
    runtime_stopped: bool,
) -> None:
    if not runtime_stopped:
        raise RecoveryError(
            "refusing recovery without explicit confirmation that Runtime is stopped"
        )
    if not source.is_file():
        raise RecoveryError(f"source database does not exist: {source}")
    if os.path.lexists(target):
        raise RecoveryError(f"target must not exist: {target}")
    if os.path.lexists(backup_directory):
        raise RecoveryError(f"backup directory must not exist: {backup_directory}")
    if not target.parent.is_dir():
        raise RecoveryError(f"target parent directory does not exist: {target.parent}")
    if not backup_directory.parent.is_dir():
        raise RecoveryError(f"backup parent directory does not exist: {backup_directory.parent}")

    if os.name == "nt":
        canonical_source = _windows_path_for_namespace_policy("source", source, existing=True)
        canonical_target = _windows_path_for_namespace_policy("target", target, existing=False)
        canonical_backup = _windows_path_for_namespace_policy(
            "backup directory", backup_directory, existing=False
        )
    else:
        canonical_source = source.resolve(strict=True)
        canonical_target = target.resolve(strict=False)
        canonical_backup = backup_directory.resolve(strict=False)

    normalized_source = os.path.normcase(str(canonical_source))
    normalized_target = os.path.normcase(str(canonical_target))
    normalized_backup = os.path.normcase(str(canonical_backup))
    if normalized_source == normalized_target:
        raise RecoveryError("source and target must be different paths")
    if normalized_target == normalized_backup:
        raise RecoveryError("target and backup directory must be different paths")
    source_family_paths = {
        os.path.normcase(str(canonical_source.with_name(canonical_source.name + suffix)))
        for suffix in _FAMILY_SUFFIXES
    }
    if normalized_target in source_family_paths:
        raise RecoveryError("target must not alias a source database family path")
    if normalized_backup in source_family_paths:
        raise RecoveryError("backup directory must not alias a source database family path")


def _audit_source_aliases(source: Path) -> AliasAudit:
    aliases = {source}
    selected_final_path = str(source)
    physical_local_app_data_text: str | None = None
    package_family_paths: tuple[str, ...] = ()
    if os.name == "nt":
        final_source = _windows_path_for_namespace_policy("source", source, existing=True)
        selected_final_path = str(final_source)
        physical_local_app_data = _windows_path_for_namespace_policy(
            "physical LocalAppData",
            _windows_physical_local_app_data(),
            existing=True,
        )
        physical_local_app_data_text = str(physical_local_app_data)
        package_family_paths = _windows_package_local_cache_family_paths(
            final_source, physical_local_app_data
        )
        if package_family_paths:
            raise RecoveryError(
                "physical LocalAppData source has a Package LocalCache layered "
                "SQLite family; refusing a merged database namespace: "
                + ", ".join(package_family_paths)
            )
        aliases.update(_windows_hardlink_aliases(source))
        for alias in aliases:
            _windows_path_for_namespace_policy("source hard-link alias", alias, existing=True)
    else:
        for candidate in source.parent.iterdir():
            try:
                if candidate.is_file() and os.path.samefile(candidate, source):
                    aliases.add(candidate)
            except OSError:
                continue

    selected_key = _normalized_path(source)
    by_key = {_normalized_path(alias): alias.absolute() for alias in aliases}
    alternate = tuple(sorted(str(alias) for key, alias in by_key.items() if key != selected_key))
    conflicts: list[str] = []
    for alias_text in alternate:
        alias = Path(alias_text)
        for suffix in _FAMILY_SUFFIXES[1:]:
            alternate_sidecar = alias.with_name(alias.name + suffix)
            if not alternate_sidecar.exists():
                continue
            selected_sidecar = source.with_name(source.name + suffix)
            try:
                is_same = selected_sidecar.exists() and os.path.samefile(
                    selected_sidecar, alternate_sidecar
                )
            except OSError:
                is_same = False
            if not is_same:
                conflicts.append(str(alternate_sidecar))
    if conflicts:
        raise RecoveryError(
            "alternate main-file namespace has an independent SQLite sidecar: "
            + ", ".join(sorted(conflicts))
        )
    return AliasAudit(
        selected_path=str(source),
        selected_final_path=selected_final_path,
        alternate_main_paths=alternate,
        physical_local_app_data=physical_local_app_data_text,
        package_local_cache_family_paths=package_family_paths,
    )


def _validate_alias_output_paths(audit: AliasAudit, target: Path, backup_directory: Path) -> None:
    protected: set[str] = set()
    for main_text in (
        audit.selected_path,
        audit.selected_final_path,
        *audit.alternate_main_paths,
    ):
        main = Path(main_text)
        protected.update(
            _normalized_path(main.with_name(main.name + suffix)) for suffix in _FAMILY_SUFFIXES
        )
    if _normalized_path(target) in protected:
        raise RecoveryError("target must not occupy any source-alias database family path")
    if _normalized_path(backup_directory) in protected:
        raise RecoveryError("backup directory must not occupy any source-alias family path")


def _windows_hardlink_aliases(source: Path) -> set[Path]:
    completed = subprocess.run(
        ["fsutil", "hardlink", "list", str(source)],
        check=False,
        capture_output=True,
        text=True,
    )
    if completed.returncode != 0:
        detail = completed.stderr.strip() or completed.stdout.strip() or "unknown fsutil error"
        raise RecoveryError(f"could not audit Windows hard-link aliases: {detail}")
    aliases: set[Path] = set()
    for raw_line in completed.stdout.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        candidate = Path(source.anchor) / line.lstrip("\\/")
        try:
            if candidate.is_file() and os.path.samefile(candidate, source):
                aliases.add(candidate)
        except OSError:
            continue
    if not aliases:
        raise RecoveryError("Windows hard-link audit returned no usable path for the source")
    return aliases


def _normalized_path(path: Path) -> str:
    return os.path.normcase(str(path.resolve(strict=False)))


def _is_package_local_cache_path(path: Path) -> bool:
    parts = tuple(part.casefold() for part in path.parts)
    for index in range(len(parts) - 3):
        if (
            parts[index] == "packages"
            and parts[index + 2] == "localcache"
            and parts[index + 3] in {"local", "roaming"}
        ):
            return True
    return False


def _windows_path_for_namespace_policy(label: str, path: Path, *, existing: bool) -> Path:
    """Resolve reparse points and short names before applying local path policy.

    Output paths do not exist yet, so their existing parent is resolved through a
    handle and the leaf name is appended. UNC inputs are rejected conservatively:
    even a loopback administrative share retains a distinct MUP spelling, which
    cannot be proven to share the physical LocalAppData namespace and sidecars.
    """

    if _is_windows_unc_path(path):
        raise RecoveryError(
            f"{label} must use a local drive path; UNC paths are not supported: {path}"
        )

    if existing:
        final_path = _windows_final_existing_path(path)
    else:
        final_parent = _windows_final_existing_path(path.parent)
        final_path = final_parent / path.name

    if _is_windows_unc_path(final_path):
        raise RecoveryError(
            f"{label} resolves to a UNC path that cannot be audited safely: {final_path}"
        )
    if _is_package_local_cache_path(path) or _is_package_local_cache_path(final_path):
        raise RecoveryError(f"{label} must not use a Package LocalCache path: {final_path}")
    return final_path


def _windows_final_existing_path(path: Path) -> Path:
    """Return the normalized DOS path reported for an existing Win32 handle."""

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    create_file = kernel32.CreateFileW
    create_file.argtypes = [
        ctypes.c_wchar_p,
        ctypes.c_uint32,
        ctypes.c_uint32,
        ctypes.c_void_p,
        ctypes.c_uint32,
        ctypes.c_uint32,
        ctypes.c_void_p,
    ]
    create_file.restype = ctypes.c_void_p
    get_final_path = kernel32.GetFinalPathNameByHandleW
    get_final_path.argtypes = [
        ctypes.c_void_p,
        ctypes.c_wchar_p,
        ctypes.c_uint32,
        ctypes.c_uint32,
    ]
    get_final_path.restype = ctypes.c_uint32
    close_handle = kernel32.CloseHandle
    close_handle.argtypes = [ctypes.c_void_p]
    close_handle.restype = ctypes.c_int

    share_read_write_delete = 0x00000001 | 0x00000002 | 0x00000004
    open_existing = 3
    backup_semantics = 0x02000000
    invalid_handle = ctypes.c_void_p(-1).value
    handle = create_file(
        str(path),
        0,
        share_read_write_delete,
        None,
        open_existing,
        backup_semantics,
        None,
    )
    if handle in (None, invalid_handle):
        error_code = ctypes.get_last_error()
        raise RecoveryError(
            f"could not resolve final Windows path for {path} (Win32 error {error_code})"
        )
    try:
        required = get_final_path(handle, None, 0, 0)
        if required == 0:
            error_code = ctypes.get_last_error()
            raise RecoveryError(
                f"could not size final Windows path for {path} (Win32 error {error_code})"
            )
        buffer = ctypes.create_unicode_buffer(required + 1)
        written = get_final_path(handle, buffer, len(buffer), 0)
        if written == 0 or written >= len(buffer):
            error_code = ctypes.get_last_error()
            raise RecoveryError(
                f"could not read final Windows path for {path} (Win32 error {error_code})"
            )
        value = buffer.value
    finally:
        close_handle(handle)

    extended_unc_prefix = "\\\\?\\UNC\\"
    extended_prefix = "\\\\?\\"
    if value.casefold().startswith(extended_unc_prefix.casefold()):
        value = "\\\\" + value[len(extended_unc_prefix) :]
    elif value.startswith(extended_prefix) and re.match(
        r"^[A-Za-z]:\\", value[len(extended_prefix) :]
    ):
        value = value[len(extended_prefix) :]
    elif value.startswith(extended_prefix):
        raise RecoveryError(f"final Windows path has no auditable DOS drive spelling: {value}")
    if not value:
        raise RecoveryError(f"final Windows path resolution returned empty for {path}")
    return Path(value)


def _is_windows_unc_path(path: Path) -> bool:
    value = str(path)
    casefolded = value.casefold()
    if casefolded.startswith("\\\\?\\unc\\"):
        return True
    if casefolded.startswith("\\\\?\\"):
        return False
    return value.startswith("\\\\")


def _windows_physical_local_app_data() -> Path:
    """Return LocalAppData without the current package's redirected spelling."""

    class Guid(ctypes.Structure):
        _fields_ = (
            ("data1", ctypes.c_uint32),
            ("data2", ctypes.c_uint16),
            ("data3", ctypes.c_uint16),
            ("data4", ctypes.c_ubyte * 8),
        )

    folder_id = Guid.from_buffer_copy(UUID("f1b32785-6fba-4fcf-9d55-7b8e7f157091").bytes_le)
    shell32 = ctypes.WinDLL("shell32", use_last_error=True)
    get_path = shell32.SHGetKnownFolderPath
    get_path.argtypes = [
        ctypes.POINTER(Guid),
        ctypes.c_uint32,
        ctypes.c_void_p,
        ctypes.POINTER(ctypes.c_void_p),
    ]
    get_path.restype = ctypes.c_long
    ole32 = ctypes.WinDLL("ole32", use_last_error=True)
    free_memory = ole32.CoTaskMemFree
    free_memory.argtypes = [ctypes.c_void_p]
    free_memory.restype = None

    no_package_redirection = 0x00010000
    raw_path = ctypes.c_void_p()
    result = get_path(
        ctypes.byref(folder_id),
        no_package_redirection,
        None,
        ctypes.byref(raw_path),
    )
    if result != 0:
        raise RecoveryError(
            "could not resolve physical LocalAppData with "
            "KF_FLAG_NO_PACKAGE_REDIRECTION "
            f"(HRESULT 0x{result & 0xFFFFFFFF:08x})"
        )
    if raw_path.value is None:
        raise RecoveryError("physical LocalAppData resolution returned no path")
    try:
        value = ctypes.wstring_at(raw_path.value)
    finally:
        free_memory(raw_path)
    if not value:
        raise RecoveryError("physical LocalAppData resolution returned an empty path")
    return Path(value).absolute()


def _windows_package_local_cache_family_paths(
    source: Path, physical_local_app_data: Path
) -> tuple[str, ...]:
    """Find package-private members layered over a physical LocalAppData family."""

    if os.name == "nt":
        if _is_windows_unc_path(source) or _is_windows_unc_path(physical_local_app_data):
            raise RecoveryError(
                "Package LocalCache audit requires local-drive source and root paths"
            )
        source = _windows_final_existing_path(source)
        physical_local_app_data = _windows_final_existing_path(physical_local_app_data)
    if _is_package_local_cache_path(source):
        raise RecoveryError(
            "source must use the physical LocalAppData spelling, not a Package LocalCache path"
        )
    try:
        relative_source = source.absolute().relative_to(physical_local_app_data.absolute())
    except ValueError:
        return ()

    packages_root = physical_local_app_data / "Packages"
    try:
        package_roots = tuple(packages_root.iterdir())
    except FileNotFoundError:
        return ()
    except OSError as error:
        raise RecoveryError(
            f"could not enumerate Package LocalCache roots under {packages_root}: {error}"
        ) from error

    layered_members: list[str] = []
    for package_root in package_roots:
        candidate_main = package_root / "LocalCache" / "Local" / relative_source
        for suffix in _FAMILY_SUFFIXES:
            candidate = candidate_main.with_name(candidate_main.name + suffix)
            try:
                candidate.lstat()
            except FileNotFoundError:
                continue
            except OSError as error:
                raise RecoveryError(
                    f"could not audit Package LocalCache family member {candidate}: {error}"
                ) from error
            layered_members.append(str(candidate.absolute()))
    return tuple(sorted(layered_members))


@contextmanager
def _source_family_guard(source: Path) -> Generator[str]:
    """Deny write/delete opens for the selected family throughout Windows recovery."""

    if os.name != "nt":
        yield "operator confirmation plus repeated family digest sampling"
        return

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    create_file = kernel32.CreateFileW
    create_file.argtypes = [
        ctypes.c_wchar_p,
        ctypes.c_uint32,
        ctypes.c_uint32,
        ctypes.c_void_p,
        ctypes.c_uint32,
        ctypes.c_uint32,
        ctypes.c_void_p,
    ]
    create_file.restype = ctypes.c_void_p
    close_handle = kernel32.CloseHandle
    close_handle.argtypes = [ctypes.c_void_p]
    close_handle.restype = ctypes.c_int

    generic_read = 0x80000000
    share_read = 0x00000001
    open_existing = 3
    normal_attributes = 0x00000080
    invalid_handle = ctypes.c_void_p(-1).value
    handles: list[int] = []
    try:
        for suffix in _FAMILY_SUFFIXES:
            path = source.with_name(source.name + suffix)
            if not path.exists():
                continue
            handle = create_file(
                str(path),
                generic_read,
                share_read,
                None,
                open_existing,
                normal_attributes,
                None,
            )
            if handle in (None, invalid_handle):
                error_code = ctypes.get_last_error()
                raise RecoveryError(
                    "could not lock source family against writers; "
                    f"Runtime may still be running ({path}, Win32 error {error_code})"
                )
            handles.append(int(handle))
        yield "Win32 handles deny FILE_SHARE_WRITE and FILE_SHARE_DELETE"
    finally:
        for handle in reversed(handles):
            close_handle(ctypes.c_void_p(handle))


def _backup_stable_family(source: Path, raw_backup: Path) -> dict[str, FamilyFile]:
    before = _snapshot_family(source)
    for suffix, metadata in before.items():
        shutil.copy2(source.with_name(source.name + suffix), raw_backup / metadata.name)
    after = _snapshot_family(source)
    _assert_same_family(before, after, "source changed while its raw backup was copied")
    _verify_raw_backup(before, raw_backup, source.name)
    return before


def _copy_family_to_workspace(source: Path, raw_backup: Path, workspace: Path) -> Path:
    snapshot_root = workspace / "source-snapshot"
    snapshot_root.mkdir()
    for suffix in _FAMILY_SUFFIXES:
        raw_file = raw_backup / (source.name + suffix)
        if raw_file.exists():
            shutil.copy2(raw_file, snapshot_root / raw_file.name)
    return snapshot_root / source.name


def _snapshot_family(database: Path) -> dict[str, FamilyFile]:
    result: dict[str, FamilyFile] = {}
    for suffix in _FAMILY_SUFFIXES:
        candidate = database.with_name(database.name + suffix)
        if not candidate.exists():
            continue
        result[suffix] = _file_metadata(candidate, suffix=suffix)
    if "" not in result:
        raise RecoveryError(f"database main file disappeared: {database}")
    return result


def _file_metadata(path: Path, *, suffix: str) -> FamilyFile:
    stat = path.stat()
    return FamilyFile(
        suffix=suffix,
        name=path.name,
        size=stat.st_size,
        mtime_ns=stat.st_mtime_ns,
        device=stat.st_dev,
        inode=stat.st_ino,
        sha256=_sha256_file(path),
    )


def _assert_same_family(
    expected: dict[str, FamilyFile], actual: dict[str, FamilyFile], reason: str
) -> None:
    if set(expected) != set(actual):
        raise RecoveryError(f"{reason}: database family membership changed")
    for suffix, expected_file in expected.items():
        actual_file = actual[suffix]
        if (
            expected_file.size,
            expected_file.device,
            expected_file.inode,
            expected_file.sha256,
        ) != (
            actual_file.size,
            actual_file.device,
            actual_file.inode,
            actual_file.sha256,
        ):
            raise RecoveryError(f"{reason}: {expected_file.name} changed")


def _verify_raw_backup(
    source_family: dict[str, FamilyFile], raw_backup: Path, source_name: str
) -> None:
    for suffix, source_file in source_family.items():
        backup_file = raw_backup / (source_name + suffix)
        if not backup_file.is_file():
            raise RecoveryError(f"raw backup is missing {source_file.name}")
        if backup_file.stat().st_size != source_file.size:
            raise RecoveryError(f"raw backup size mismatch for {source_file.name}")
        if _sha256_file(backup_file) != source_file.sha256:
            raise RecoveryError(f"raw backup digest mismatch for {source_file.name}")


def _publish_new_target(building_target: Path, target: Path) -> None:
    """Atomically publish without ever replacing a path that appeared after preflight."""

    try:
        os.link(building_target, target)
    except FileExistsError as error:
        raise RecoveryError(
            f"target appeared during recovery and was not replaced: {target}"
        ) from error
    except OSError as error:
        raise RecoveryError(f"could not atomically publish recovered target: {error}") from error


def _file_identity(path: Path) -> tuple[int, int, int, str]:
    stat = path.stat()
    return stat.st_dev, stat.st_ino, stat.st_size, _sha256_file(path)


def _matches_file_identity(path: Path, expected: tuple[int, int, int, str]) -> bool:
    try:
        return _file_identity(path) == expected
    except FileNotFoundError:
        return False


def _recover_snapshot(
    snapshot_database: Path,
    building_target: Path,
    *,
    source: Path,
    target: Path,
    backup_directory: Path,
    source_family: dict[str, FamilyFile],
    started_at: str,
) -> dict[str, object]:
    source_connection = _open_snapshot_read_only(snapshot_database)
    target_connection = sqlite3.connect(building_target)
    try:
        source_connection.row_factory = sqlite3.Row
        target_connection.row_factory = sqlite3.Row
        source_connection.execute("PRAGMA query_only=ON")
        source_connection.execute("PRAGMA cell_size_check=ON")
        source_connection.execute("BEGIN")

        _create_current_schema(target_connection)
        _verify_migration_ledger(source_connection)

        snapshots = {
            table: _read_table(source_connection, target_connection, table)
            for table in DURABLE_TABLES
        }
        rebuildable = {
            table: _read_table(source_connection, target_connection, table)
            for table in REBUILDABLE_TABLES
        }
        legacy = {
            table: _read_table(source_connection, target_connection, table)
            for table in LEGACY_TABLES
        }
        _validate_legacy_memory(legacy["memory_items"], snapshots["memory_records"])

        events = _select_provenance_events(source_connection, target_connection, snapshots)
        sessions = _recover_sessions(source_connection, target_connection, snapshots, events)
        historical_lineage = _validate_logical_references(snapshots, events)
        _write_recovered_rows(target_connection, sessions.snapshot, events, snapshots)
        verification = _verify_target(
            target_connection,
            sessions.snapshot,
            events,
            snapshots,
        )
        source_connection.rollback()
    except sqlite3.DatabaseError as error:
        raise RecoveryError(f"SQLite recovery failed closed: {error}") from error
    finally:
        source_connection.close()
        target_connection.close()

    return {
        "schema_version": "1.0",
        "status": "complete",
        "started_at": started_at,
        "source": str(source),
        "target": str(target),
        "backup_directory": str(backup_directory),
        "operator_confirmation": {"runtime_stopped": True},
        "source_open_policy": "original never opened by SQLite; disposable raw-backup copy mode=ro",
        "source_family": _family_report(source_family),
        "raw_backup_family": _family_report(
            _snapshot_family(backup_directory / "source-family" / source.name),
            root=backup_directory / "source-family",
        ),
        "durable_tables": _snapshot_summary(snapshots),
        "selected_provenance_events": {
            "row_count": events.count,
            "sha256": events.sha256,
            "policy": "only events referenced by memory_sources or memory_proposals",
            "unselected_events_scanned": False,
        },
        "historical_lineage": historical_lineage,
        "sessions": {
            "row_count": sessions.snapshot.count,
            "sha256": sessions.snapshot.sha256,
            "copied": sessions.copied,
            "reconstructed": sessions.reconstructed,
            "adjusted": sessions.adjusted,
            "reconstructed_id_sha256": list(sessions.reconstructed_id_sha256),
            "lookup_not_found": sessions.lookup_not_found,
            "lookup_corrupt": sessions.lookup_corrupt,
            "lookup_corrupt_id_sha256": list(sessions.lookup_corrupt_id_sha256),
            "policy": "only session IDs referenced by recovered durable rows or events",
            "unreferenced_sessions_scanned": False,
        },
        "legacy_tables_not_copied": _snapshot_summary(legacy),
        "rebuildable_tables_not_copied": _snapshot_summary(rebuildable),
        "transient_tables_not_read_or_copied": list(TRANSIENT_TABLES),
        "fts_policy": "rebuilt by current-schema triggers",
        "migration_policy": "current repository migration catalog; source ledger not copied",
        "verification": verification,
    }


def _open_snapshot_read_only(database: Path) -> sqlite3.Connection:
    uri = database.resolve().as_uri() + "?mode=ro"
    return sqlite3.connect(uri, uri=True)


def _create_current_schema(connection: sqlite3.Connection) -> None:
    connection.execute("PRAGMA foreign_keys=ON")
    connection.execute("PRAGMA cell_size_check=ON")
    connection.execute("PRAGMA synchronous=FULL")
    connection.execute("PRAGMA journal_mode=DELETE")
    connection.executescript(
        """
        BEGIN IMMEDIATE;
        CREATE TABLE schema_migrations (
            version INTEGER PRIMARY KEY,
            checksum TEXT,
            applied_at TEXT
        );
        COMMIT;
        """
    )
    for version, script in MIGRATIONS:
        checksum = hashlib.sha256(script.encode("utf-8")).hexdigest()
        applied_at = _utc_now().replace("'", "''")
        ledger = (
            "INSERT INTO schema_migrations(version, checksum, applied_at) "
            f"VALUES ({version}, '{checksum}', '{applied_at}');"
        )
        connection.executescript(f"BEGIN IMMEDIATE;\n{script.rstrip()}\n{ledger}\nCOMMIT;")


def _verify_migration_ledger(connection: sqlite3.Connection) -> None:
    expected = tuple(
        (version, hashlib.sha256(script.encode("utf-8")).hexdigest())
        for version, script in MIGRATIONS
    )
    try:
        rows = connection.execute(
            "SELECT version, checksum FROM schema_migrations ORDER BY version"
        ).fetchall()
    except sqlite3.DatabaseError as error:
        raise RecoveryError(f"source migration ledger is unreadable: {error}") from error
    actual = tuple((int(row[0]), str(row[1])) for row in rows)
    if actual != expected:
        raise RecoveryError("source migration ledger does not match the current catalog")


def _read_table(
    source: sqlite3.Connection,
    target: sqlite3.Connection,
    table: str,
) -> TableSnapshot:
    columns, primary_key = _table_shape(target, table)
    source_columns, _source_primary_key = _table_shape(source, table)
    if source_columns != columns:
        raise RecoveryError(f"source schema mismatch for table {table}")

    quoted_table = _quote(table)
    selected = ", ".join(_quote(column) for column in columns)
    ordered = ", ".join(_quote(column) for column in (primary_key or columns))
    try:
        declared_count = int(
            source.execute(f"SELECT COUNT(*) FROM {quoted_table} NOT INDEXED").fetchone()[0]
        )
        raw_rows = source.execute(
            f"SELECT {selected} FROM {quoted_table} NOT INDEXED ORDER BY {ordered}"
        ).fetchall()
    except sqlite3.DatabaseError as error:
        raise RecoveryError(f"durable table {table} is unreadable: {error}") from error
    rows = tuple(_normalize_row(row) for row in raw_rows)
    if declared_count != len(rows):
        raise RecoveryError(
            f"durable table {table} returned inconsistent row counts: "
            f"COUNT(*)={declared_count}, scan={len(rows)}"
        )
    _validate_json_values(table, columns, rows)
    return TableSnapshot(columns=columns, rows=rows, sha256=_rows_digest(columns, rows))


def _table_shape(
    connection: sqlite3.Connection, table: str
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    quoted = _quote(table)
    rows = connection.execute(f"PRAGMA table_info({quoted})").fetchall()
    if not rows:
        raise RecoveryError(f"required table is missing: {table}")
    columns = tuple(str(row[1]) for row in rows)
    primary_key = tuple(
        str(row[1]) for row in sorted(rows, key=lambda item: int(item[5])) if int(row[5]) > 0
    )
    return columns, primary_key


def _select_provenance_events(
    source: sqlite3.Connection,
    target: sqlite3.Connection,
    snapshots: dict[str, TableSnapshot],
) -> TableSnapshot:
    event_ids = {
        _required_string(row, "source_event_id", table="memory_sources")
        for row in snapshots["memory_sources"].dictionaries()
    }
    for row in snapshots["memory_proposals"].dictionaries():
        raw_evidence = _required_string(row, "evidence_event_ids_json", table="memory_proposals")
        evidence = _load_json(raw_evidence, "memory_proposals.evidence_event_ids_json")
        if not isinstance(evidence, list):
            raise RecoveryError("memory proposal evidence must be a list of non-empty event IDs")
        evidence_values = cast(list[object], evidence)
        if not evidence_values or any(
            not isinstance(value, str) or not _is_uuid(value) for value in evidence_values
        ):
            raise RecoveryError("memory proposal evidence must be a list of non-empty event IDs")
        event_ids.update(cast(list[str], evidence_values))

    columns, primary_key = _table_shape(target, "events")
    source_columns, _source_primary_key = _table_shape(source, "events")
    if source_columns != columns:
        raise RecoveryError("source schema mismatch for table events")
    rows: list[tuple[SqlValue, ...]] = []
    selected = ", ".join(_quote(column) for column in columns)
    for event_id in sorted(event_ids):
        try:
            found = source.execute(
                f"SELECT {selected} FROM events WHERE event_id = ?", (event_id,)
            ).fetchall()
        except sqlite3.DatabaseError as error:
            raise RecoveryError(f"provenance event {event_id} is unreadable: {error}") from error
        if len(found) != 1:
            raise RecoveryError(f"required provenance event is missing: {event_id}")
        rows.append(_normalize_row(found[0]))

    order_indexes = [columns.index(column) for column in (primary_key or columns)]
    rows.sort(key=lambda row: tuple(_sortable_value(row[index]) for index in order_indexes))
    result = TableSnapshot(
        columns=columns,
        rows=tuple(rows),
        sha256=_rows_digest(columns, tuple(rows)),
    )
    _validate_json_values("events", result.columns, result.rows)
    _validate_event_envelopes(result)
    return result


def _validate_event_envelopes(events: TableSnapshot) -> None:
    matching_fields = (
        "event_id",
        "session_id",
        "sequence",
        "event_type",
        "schema_version",
        "occurred_at",
        "source",
        "correlation_id",
        "causation_id",
    )
    for row in events.dictionaries():
        envelope_text = _required_string(row, "envelope_json", table="events")
        payload_text = _required_string(row, "payload_json", table="events")
        envelope = _load_json(envelope_text, "events.envelope_json")
        payload = _load_json(payload_text, "events.payload_json")
        if not isinstance(envelope, dict) or not isinstance(payload, dict):
            raise RecoveryError("event envelope and payload must both be JSON objects")
        envelope_object = cast(dict[str, object], envelope)
        for field in matching_fields:
            if field == "occurred_at":
                matches = _same_timestamp(envelope_object.get(field), row[field])
            else:
                matches = envelope_object.get(field) == row[field]
            if not matches:
                raise RecoveryError(f"event envelope field does not match scalar column: {field}")
        if envelope_object.get("payload") != payload:
            raise RecoveryError("event envelope payload does not match payload_json")


def _recover_sessions(
    source: sqlite3.Connection,
    target: sqlite3.Connection,
    snapshots: dict[str, TableSnapshot],
    events: TableSnapshot,
) -> SessionRecovery:
    required_ids: set[str] = set()
    all_rows: list[dict[str, SqlValue]] = []
    for snapshot in (*snapshots.values(), events):
        for row in snapshot.dictionaries():
            all_rows.append(row)
            for field in _SESSION_FIELDS.intersection(row):
                value = row[field]
                if value is not None:
                    if not isinstance(value, str) or not value:
                        raise RecoveryError(f"invalid {field} in recovery input")
                    required_ids.add(value)

    columns, primary_key = _table_shape(target, "sessions")
    source_columns, _source_primary_key = _table_shape(source, "sessions")
    if source_columns != columns:
        raise RecoveryError("source schema mismatch for table sessions")
    selected = ", ".join(_quote(column) for column in columns)
    max_sequences: dict[str, int] = {}
    for row in events.dictionaries():
        session_id = _required_string(row, "session_id", table="events")
        sequence = row["sequence"]
        if not isinstance(sequence, int) or sequence < 0:
            raise RecoveryError("event sequence must be a non-negative integer")
        max_sequences[session_id] = max(max_sequences.get(session_id, -1), sequence)

    output: list[tuple[SqlValue, ...]] = []
    copied = 0
    reconstructed = 0
    adjusted = 0
    reconstructed_hashes: list[str] = []
    lookup_not_found = 0
    lookup_corrupt = 0
    corrupt_lookup_hashes: list[str] = []
    for session_id in sorted(required_ids):
        lookup_was_corrupt = False
        try:
            found = source.execute(
                f"SELECT {selected} FROM sessions WHERE session_id = ?", (session_id,)
            ).fetchall()
        except sqlite3.DatabaseError as error:
            if not _recoverable_session_lookup_error(error):
                raise RecoveryError(f"session lookup failed: {error}") from error
            found = []
            lookup_was_corrupt = True
            lookup_corrupt += 1
            corrupt_lookup_hashes.append(hashlib.sha256(session_id.encode("utf-8")).hexdigest())
        recovered_next = max(1, max_sequences.get(session_id, 0) + 1)
        if len(found) == 1:
            row = list(_normalize_row(found[0]))
            next_index = columns.index("next_sequence")
            current_next = row[next_index]
            if not isinstance(current_next, int):
                raise RecoveryError(f"session {session_id} has invalid next_sequence")
            if current_next != recovered_next:
                row[next_index] = recovered_next
                adjusted += 1
            output.append(tuple(row))
            copied += 1
            continue
        if len(found) > 1:
            raise RecoveryError(f"session primary key is not unique: {session_id}")
        if not lookup_was_corrupt:
            lookup_not_found += 1

        related = [
            row
            for row in all_rows
            if row.get("session_id") == session_id or row.get("source_session_id") == session_id
        ]
        created_at, updated_at = _time_bounds(related)
        values: dict[str, SqlValue] = {
            "session_id": session_id,
            "character_id": _infer_session_character(session_id, snapshots)
            or _infer_default_character(snapshots),
            "state": "ready",
            "conversation_state": "idle",
            "revision": 0,
            "next_sequence": recovered_next,
            "created_at": created_at,
            "updated_at": updated_at,
        }
        output.append(tuple(values[column] for column in columns))
        reconstructed += 1
        reconstructed_hashes.append(hashlib.sha256(session_id.encode("utf-8")).hexdigest())

    order_indexes = [columns.index(column) for column in (primary_key or columns)]
    output.sort(key=lambda row: tuple(_sortable_value(row[index]) for index in order_indexes))
    rows = tuple(output)
    return SessionRecovery(
        snapshot=TableSnapshot(columns, rows, _rows_digest(columns, rows)),
        copied=copied,
        reconstructed=reconstructed,
        adjusted=adjusted,
        reconstructed_id_sha256=tuple(reconstructed_hashes),
        lookup_not_found=lookup_not_found,
        lookup_corrupt=lookup_corrupt,
        lookup_corrupt_id_sha256=tuple(corrupt_lookup_hashes),
    )


def _infer_default_character(snapshots: dict[str, TableSnapshot]) -> str:
    characters = {
        _required_string(row, "character_id", table="character_states")
        for row in snapshots["character_states"].dictionaries()
    }
    connection_characters = {
        _required_string(row, "character_id", table="channel_connections")
        for row in snapshots["channel_connections"].dictionaries()
    }
    candidates = characters | connection_characters
    if len(candidates) == 1:
        return next(iter(candidates))
    if len(candidates) > 1:
        raise RecoveryError("missing session has ambiguous character ownership")
    return "default"


def _infer_session_character(session_id: str, snapshots: dict[str, TableSnapshot]) -> str | None:
    connection_by_id = {
        _required_string(row, "connection_id", table="channel_connections"): _required_string(
            row, "character_id", table="channel_connections"
        )
        for row in snapshots["channel_connections"].dictionaries()
    }
    candidates: set[str | None] = {
        connection_by_id.get(_required_string(row, "connection_id", table="channel_bindings"))
        for row in snapshots["channel_bindings"].dictionaries()
        if row.get("session_id") == session_id
    }
    memories = {
        _required_string(row, "memory_id", table="memory_records"): _required_string(
            row, "namespace", table="memory_records"
        )
        for row in snapshots["memory_records"].dictionaries()
    }
    for source in snapshots["memory_sources"].dictionaries():
        if source.get("session_id") != session_id:
            continue
        memory_id = _required_string(source, "memory_id", table="memory_sources")
        namespace = memories.get(memory_id)
        if namespace is None:
            raise RecoveryError(f"memory source references a missing memory: {memory_id}")
        parts = namespace.split("/")
        if len(parts) >= 2 and parts[0] == "character" and parts[1]:
            candidates.add(parts[1])
    candidates.discard(None)
    if len(candidates) == 1:
        return next(iter(candidates))
    if len(candidates) > 1:
        raise RecoveryError(f"session {session_id} has ambiguous character provenance")
    return None


def _recoverable_session_lookup_error(error: sqlite3.DatabaseError) -> bool:
    message = str(error).casefold()
    return "malformed" in message or "corrupt" in message


def _time_bounds(rows: list[dict[str, SqlValue]]) -> tuple[str, str]:
    values = sorted(
        value
        for row in rows
        for key, value in row.items()
        if key.endswith("_at") and isinstance(value, str) and value
    )
    now = _utc_now()
    return (values[0] if values else now, values[-1] if values else now)


def _validate_logical_references(
    snapshots: dict[str, TableSnapshot], events: TableSnapshot
) -> dict[str, int]:
    turns = {
        _required_string(row, "turn_id", table="turns"): row
        for row in snapshots["turns"].dictionaries()
    }
    generations = {
        _required_string(row, "generation_id", table="generations"): row
        for row in snapshots["generations"].dictionaries()
    }
    event_ids = {_required_string(row, "event_id", table="events") for row in events.dictionaries()}
    events_by_id = {
        _required_string(row, "event_id", table="events"): row for row in events.dictionaries()
    }
    for row in snapshots["memory_sources"].dictionaries():
        event_id = _required_string(row, "source_event_id", table="memory_sources")
        if event_id not in event_ids:
            raise RecoveryError(f"memory source event was not recovered: {event_id}")
        if events_by_id[event_id]["session_id"] != row["session_id"]:
            raise RecoveryError("memory source and provenance event sessions disagree")
        turn_id = row.get("turn_id")
        if turn_id is not None and turn_id not in turns:
            raise RecoveryError(f"memory source turn is missing: {turn_id}")
        if turn_id is not None and turns[turn_id]["session_id"] != row["session_id"]:
            raise RecoveryError("memory source and turn sessions disagree")

    for row in snapshots["generations"].dictionaries():
        turn_id = _required_string(row, "turn_id", table="generations")
        turn = turns.get(turn_id)
        if turn is None:
            raise RecoveryError(f"generation references a missing turn: {turn_id}")
        if turn["session_id"] != row["session_id"]:
            raise RecoveryError("generation and turn sessions disagree")

    historical_lineage = {
        "missing_skill_run_turn_id": 0,
        "missing_skill_run_generation_id": 0,
        "missing_channel_turn_turn_id": 0,
        "missing_channel_turn_generation_id": 0,
        "missing_event_turn_id": 0,
        "missing_event_generation_id": 0,
        "missing_event_skill_run_id": 0,
    }
    for table in ("skill_runs", "channel_turns"):
        for row in snapshots[table].dictionaries():
            turn_id = row.get("turn_id")
            generation_id = row.get("generation_id")
            if turn_id is not None and turn_id not in turns:
                historical_lineage[f"missing_{table.removesuffix('s')}_turn_id"] += 1
            if generation_id is not None and generation_id not in generations:
                key = f"missing_{table.removesuffix('s')}_generation_id"
                historical_lineage[key] += 1
            if (
                turn_id is not None
                and turn_id in turns
                and turns[turn_id]["session_id"] != row.get("session_id")
            ):
                raise RecoveryError(f"{table} turn belongs to a different session")
            if (
                generation_id is not None
                and generation_id in generations
                and generations[generation_id]["session_id"] != row.get("session_id")
            ):
                raise RecoveryError(f"{table} generation belongs to a different session")

    bindings = {
        _required_string(row, "binding_id", table="channel_bindings"): row
        for row in snapshots["channel_bindings"].dictionaries()
    }
    deliveries = {
        _required_string(row, "delivery_id", table="channel_deliveries"): row
        for row in snapshots["channel_deliveries"].dictionaries()
    }
    for row in snapshots["channel_turns"].dictionaries():
        binding_id = _required_string(row, "binding_id", table="channel_turns")
        binding = bindings.get(binding_id)
        if binding is None:
            raise RecoveryError(f"channel turn binding is missing: {binding_id}")
        if binding["connection_id"] != row["connection_id"]:
            raise RecoveryError("channel turn and binding connection IDs disagree")
        if binding["session_id"] != row["session_id"]:
            raise RecoveryError("channel turn and binding session IDs disagree")
        delivery_id = row.get("delivery_id")
        if delivery_id is not None:
            delivery = deliveries.get(str(delivery_id))
            if delivery is None or delivery["channel_turn_id"] != row["channel_turn_id"]:
                raise RecoveryError("channel turn delivery linkage is incomplete")
    channel_turns = {
        _required_string(row, "channel_turn_id", table="channel_turns"): row
        for row in snapshots["channel_turns"].dictionaries()
    }
    for delivery_id, delivery in deliveries.items():
        channel_turn_id = _required_string(delivery, "channel_turn_id", table="channel_deliveries")
        channel_turn = channel_turns.get(channel_turn_id)
        if channel_turn is None or channel_turn.get("delivery_id") != delivery_id:
            raise RecoveryError("channel delivery linkage is incomplete")

    skill_runs = {
        _required_string(row, "skill_run_id", table="skill_runs"): row
        for row in snapshots["skill_runs"].dictionaries()
    }
    for event in events.dictionaries():
        envelope = _load_json(
            _required_string(event, "envelope_json", table="events"),
            "events.envelope_json",
        )
        if not isinstance(envelope, dict):
            raise RecoveryError("event envelope must be a JSON object")
        envelope_object = cast(dict[str, object], envelope)
        if _validate_envelope_reference(
            envelope_object,
            "turn_id",
            turns,
            event,
        ):
            historical_lineage["missing_event_turn_id"] += 1
        if _validate_envelope_reference(
            envelope_object,
            "generation_id",
            generations,
            event,
        ):
            historical_lineage["missing_event_generation_id"] += 1
        if _validate_envelope_reference(
            envelope_object,
            "skill_run_id",
            skill_runs,
            event,
        ):
            historical_lineage["missing_event_skill_run_id"] += 1
    return historical_lineage


def _validate_legacy_memory(legacy: TableSnapshot, canonical: TableSnapshot) -> None:
    canonical_ids = {
        _required_string(row, "memory_id", table="memory_records")
        for row in canonical.dictionaries()
    }
    legacy_ids = {
        _required_string(row, "memory_id", table="memory_items") for row in legacy.dictionaries()
    }
    missing = sorted(legacy_ids - canonical_ids)
    if missing:
        raise RecoveryError(
            "legacy-only memory_items cannot be silently discarded; missing canonical records: "
            + ", ".join(missing)
        )


def _write_recovered_rows(
    connection: sqlite3.Connection,
    sessions: TableSnapshot,
    events: TableSnapshot,
    snapshots: dict[str, TableSnapshot],
) -> None:
    try:
        connection.execute("BEGIN IMMEDIATE")
        connection.execute("PRAGMA defer_foreign_keys=ON")
        connection.execute("DELETE FROM companion_settings")
        _insert_snapshot(connection, "sessions", sessions)
        _insert_snapshot(connection, "events", events)
        for table in DURABLE_TABLES:
            _insert_snapshot(connection, table, snapshots[table])
        connection.commit()
    except BaseException:
        connection.rollback()
        raise


def _insert_snapshot(connection: sqlite3.Connection, table: str, snapshot: TableSnapshot) -> None:
    if not snapshot.rows:
        return
    columns = ", ".join(_quote(column) for column in snapshot.columns)
    placeholders = ", ".join("?" for _column in snapshot.columns)
    connection.executemany(
        f"INSERT INTO {_quote(table)} ({columns}) VALUES ({placeholders})", snapshot.rows
    )


def _verify_target(
    connection: sqlite3.Connection,
    sessions: TableSnapshot,
    events: TableSnapshot,
    snapshots: dict[str, TableSnapshot],
) -> dict[str, object]:
    _verify_migration_ledger(connection)
    expected = {"sessions": sessions, "events": events, **snapshots}
    table_results: dict[str, object] = {}
    for table, source_snapshot in expected.items():
        target_snapshot = _read_table(connection, connection, table)
        if (
            target_snapshot.count != source_snapshot.count
            or target_snapshot.sha256 != source_snapshot.sha256
        ):
            raise RecoveryError(f"target digest mismatch for table {table}")
        table_results[table] = {
            "row_count": target_snapshot.count,
            "sha256": target_snapshot.sha256,
        }

    for table in TRANSIENT_TABLES:
        count = int(connection.execute(f"SELECT COUNT(*) FROM {_quote(table)}").fetchone()[0])
        if count != 0:
            raise RecoveryError(f"transient target table is not empty: {table}")

    sequence_mismatches = connection.execute(
        """
        SELECT session.session_id
        FROM sessions AS session
        LEFT JOIN events AS event ON event.session_id = session.session_id
        GROUP BY session.session_id, session.next_sequence
        HAVING session.next_sequence != COALESCE(MAX(event.sequence), 0) + 1
        """
    ).fetchall()
    if sequence_mismatches:
        raise RecoveryError(
            f"target has {len(sequence_mismatches)} non-contiguous session sequence cursors"
        )

    active_legacy = int(
        connection.execute("SELECT COUNT(*) FROM memory_items WHERE state = 'active'").fetchone()[0]
    )
    legacy_fts = int(connection.execute("SELECT COUNT(*) FROM memory_fts").fetchone()[0])
    if active_legacy != legacy_fts:
        raise RecoveryError("legacy memory FTS projection is inconsistent")
    active_records = int(
        connection.execute("SELECT COUNT(*) FROM memory_records WHERE state = 'active'").fetchone()[
            0
        ]
    )
    records_fts = int(connection.execute("SELECT COUNT(*) FROM memory_records_fts").fetchone()[0])
    if active_records != records_fts:
        raise RecoveryError("canonical memory FTS projection is inconsistent")

    quick_check = tuple(str(row[0]) for row in connection.execute("PRAGMA quick_check").fetchall())
    integrity_check = tuple(
        str(row[0]) for row in connection.execute("PRAGMA integrity_check").fetchall()
    )
    foreign_keys = connection.execute("PRAGMA foreign_key_check").fetchall()
    if quick_check != ("ok",):
        raise RecoveryError(f"target quick_check failed: {quick_check}")
    if integrity_check != ("ok",):
        raise RecoveryError(f"target integrity_check failed: {integrity_check}")
    if foreign_keys:
        raise RecoveryError(f"target foreign_key_check failed with {len(foreign_keys)} rows")
    connection.commit()
    return {
        "quick_check": "ok",
        "integrity_check": "ok",
        "foreign_key_violations": 0,
        "table_digests": table_results,
        "transient_table_counts": {table: 0 for table in TRANSIENT_TABLES},
        "memory_records_fts_count": records_fts,
    }


def _validate_json_values(
    table: str,
    columns: tuple[str, ...],
    rows: tuple[tuple[SqlValue, ...], ...],
) -> None:
    json_indexes = [index for index, column in enumerate(columns) if column.endswith("_json")]
    for row in rows:
        for index in json_indexes:
            value = row[index]
            if value is None:
                continue
            if not isinstance(value, str):
                raise RecoveryError(f"{table}.{columns[index]} is not JSON text")
            _load_json(value, f"{table}.{columns[index]}")


def _load_json(value: str, label: str) -> object:
    try:
        return json.loads(value, parse_constant=lambda constant: _reject_json_constant(constant))
    except (TypeError, ValueError, json.JSONDecodeError) as error:
        raise RecoveryError(f"invalid JSON in {label}: {error}") from error


def _reject_json_constant(constant: str) -> object:
    raise ValueError(f"non-finite JSON number {constant}")


def _same_timestamp(left: object, right: SqlValue) -> bool:
    if not isinstance(left, str) or not isinstance(right, str):
        return False
    try:
        left_value = datetime.fromisoformat(left.replace("Z", "+00:00"))
        right_value = datetime.fromisoformat(right.replace("Z", "+00:00"))
    except ValueError:
        return False
    return left_value == right_value


def _validate_envelope_reference(
    envelope: dict[str, object],
    field: str,
    referenced: dict[str, dict[str, SqlValue]],
    event: dict[str, SqlValue],
) -> bool:
    value = envelope.get(field)
    if value is None:
        return False
    if not isinstance(value, str) or not _is_uuid(value):
        raise RecoveryError(f"event envelope {field} is invalid")
    row = referenced.get(value)
    if row is None:
        return True
    if row.get("session_id") != event.get("session_id"):
        raise RecoveryError(f"event envelope {field} belongs to a different session")
    return False


def _required_string(row: dict[str, SqlValue], field: str, *, table: str) -> str:
    value = row.get(field)
    if not isinstance(value, str) or not value:
        raise RecoveryError(f"{table}.{field} must be a non-empty string")
    return value


def _is_uuid(value: str) -> bool:
    try:
        UUID(value)
    except ValueError:
        return False
    return True


def _normalize_row(row: sqlite3.Row | tuple[object, ...]) -> tuple[SqlValue, ...]:
    output: list[SqlValue] = []
    for value in row:
        if value is None or isinstance(value, (int, float, str, bytes)):
            output.append(value)
        else:
            raise RecoveryError(f"unsupported SQLite value type: {type(value).__name__}")
    return tuple(output)


def _rows_digest(columns: tuple[str, ...], rows: tuple[tuple[SqlValue, ...], ...]) -> str:
    digest = hashlib.sha256()
    digest.update(_canonical_json(list(columns)))
    for row in rows:
        encoded = _canonical_json([_encode_value(value) for value in row])
        digest.update(len(encoded).to_bytes(8, "big"))
        digest.update(encoded)
    return digest.hexdigest()


def _encode_value(value: SqlValue) -> object:
    if isinstance(value, bytes):
        return {"base64": base64.b64encode(value).decode("ascii")}
    return value


def _canonical_json(value: object) -> bytes:
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    except (TypeError, ValueError) as error:
        raise RecoveryError(f"cannot digest SQLite value: {error}") from error


def _sortable_value(value: SqlValue) -> tuple[str, str]:
    return type(value).__name__, repr(value)


def _snapshot_summary(snapshots: dict[str, TableSnapshot]) -> dict[str, object]:
    return {
        table: {"row_count": snapshot.count, "sha256": snapshot.sha256}
        for table, snapshot in snapshots.items()
    }


def _family_report(family: dict[str, FamilyFile], *, root: Path | None = None) -> dict[str, object]:
    report: dict[str, object] = {}
    for suffix, metadata in family.items():
        entry: dict[str, object] = asdict(metadata)
        if root is not None:
            entry["path"] = str(root / metadata.name)
        report[suffix or "main"] = entry
    return report


def _quote(identifier: str) -> str:
    if not _IDENTIFIER.fullmatch(identifier):
        raise RecoveryError(f"unsafe SQLite identifier: {identifier!r}")
    return f'"{identifier}"'


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _write_json_exclusive(path: Path, payload: dict[str, object]) -> tuple[int, int, int, str]:
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as stream:
            json.dump(payload, stream, ensure_ascii=False, indent=2, sort_keys=True)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        expected = _file_identity(temporary)
        try:
            os.link(temporary, path)
        except FileExistsError as error:
            raise RecoveryError(f"report path appeared and was not replaced: {path}") from error
        if not _matches_file_identity(path, expected):
            raise RecoveryError("published recovery report failed identity verification")
        return expected
    finally:
        temporary.unlink(missing_ok=True)


def _utc_now() -> str:
    return datetime.now(UTC).isoformat()


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", required=True, type=Path, help="stopped Runtime chatwaifu.db")
    parser.add_argument("--target", required=True, type=Path, help="new recovered database path")
    parser.add_argument(
        "--backup-directory",
        required=True,
        type=Path,
        help="new directory for the byte-for-byte source family and recovery report",
    )
    parser.add_argument(
        "--confirm-runtime-stopped",
        action="store_true",
        help="required acknowledgement; the tool also detects source-family changes",
    )
    return parser.parse_args()


def main() -> int:
    arguments = _parse_args()
    try:
        report = recover_runtime_database(
            arguments.source,
            arguments.target,
            arguments.backup_directory,
            runtime_stopped=bool(arguments.confirm_runtime_stopped),
        )
    except (OSError, RecoveryError) as error:
        print(f"recovery failed: {error}", file=sys.stderr)
        return 1
    summary = {
        "status": report["status"],
        "target": report["target"],
        "backup_directory": report["backup_directory"],
        "sessions": report["sessions"],
        "selected_provenance_events": report["selected_provenance_events"],
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
