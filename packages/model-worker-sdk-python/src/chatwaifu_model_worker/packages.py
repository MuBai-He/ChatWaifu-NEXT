"""Strict, portable contracts for independently installed model worker packs."""

from __future__ import annotations

import re
from datetime import datetime
from pathlib import PurePosixPath
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

WORKER_PACK_SCHEMA_VERSION = "1.0"
WORKER_PACK_RECEIPT_SCHEMA_VERSION = "1.0"
WORKER_PACK_SELECTION_SCHEMA_VERSION = "1.0"
WORKER_PACK_MAX_FILE_BYTES = 256 * 1024 * 1024 * 1024
WORKER_PACK_MAX_FILE_COUNT = 100_000
WORKER_PACK_MAX_EXPANDED_BYTES = 1024 * 1024 * 1024 * 1024

_IDENTIFIER_PATTERN = r"^[a-z0-9][a-z0-9_.-]{1,127}$"
_SEMVER_PATTERN = (
    r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)"
    r"(?:-[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?"
    r"(?:\+[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?$"
)
_SHA256_PATTERN = r"^[0-9a-f]{64}$"
_ENVIRONMENT_KEY_PATTERN = re.compile(r"^[A-Z][A-Z0-9_]{1,127}$")
_PLACEHOLDER_PATTERN = re.compile(r"\$\{([^{}]+)\}")
_ALLOWED_PLACEHOLDERS = frozenset({"PACK_ROOT", "DATA_ROOT", "CONFIG_ROOT"})
_WINDOWS_RESERVED_NAMES = frozenset(
    {
        "CON",
        "PRN",
        "AUX",
        "NUL",
        *(f"COM{index}" for index in range(1, 10)),
        *(f"LPT{index}" for index in range(1, 10)),
    }
)
_RESERVED_PACK_FILES = frozenset({"manifest.json", "install-receipt.json"})
_WORKER_ENVIRONMENT_PREFIXES: dict[str, str] = {
    "stt": "CHATWAIFU_STT_WORKER_",
    "tts": "CHATWAIFU_NEURAL_TTS_WORKER_",
}
_SUPERVISOR_ENVIRONMENT_SUFFIXES = frozenset({"HOST", "PORT", "TOKEN"})
_SECRET_ENVIRONMENT_SEGMENTS = frozenset(
    {
        "AUTH",
        "BEARER",
        "COOKIE",
        "CREDENTIAL",
        "CREDENTIALS",
        "KEY",
        "PASSWORD",
        "PASSWD",
        "SECRET",
        "TOKEN",
    }
)
_SECRET_ENVIRONMENT_COMPOUNDS = frozenset(
    {
        "ACCESSKEY",
        "APIKEY",
        "CLIENTSECRET",
        "CONNECTIONSTRING",
        "PRIVATEKEY",
        "SECRETKEY",
    }
)


class WorkerPackModel(BaseModel):
    """Base type that rejects unknown forward-incompatible fields."""

    model_config = ConfigDict(extra="forbid", frozen=True)


def _validate_portable_relative_path(value: str, *, allow_current: bool = False) -> str:
    if value == "." and allow_current:
        return value
    if not value or "\\" in value or "\x00" in value:
        raise ValueError("path must be a non-empty POSIX relative path")
    path = PurePosixPath(value)
    if path.is_absolute() or value != path.as_posix() or not path.parts:
        raise ValueError("path must be a normalized POSIX relative path")
    if any(part in {"", ".", ".."} for part in path.parts):
        raise ValueError("path must not contain empty, current, or parent segments")
    for part in path.parts:
        if part.endswith((" ", ".")) or any(character in '<>:"|?*' for character in part):
            raise ValueError("path is not portable to Windows")
        if any(ord(character) < 32 for character in part):
            raise ValueError("path contains a control character")
        if part.split(".", 1)[0].upper() in _WINDOWS_RESERVED_NAMES:
            raise ValueError("path contains a Windows-reserved name")
    return value


def _validate_environment_value(value: str) -> str:
    if "\x00" in value:
        raise ValueError("environment values must not contain NUL")
    placeholders = set(_PLACEHOLDER_PATTERN.findall(value))
    unsupported = placeholders - _ALLOWED_PLACEHOLDERS
    if unsupported:
        raise ValueError(f"unsupported environment placeholder: {sorted(unsupported)[0]}")
    # A leftover marker would be interpreted differently by shells or future launchers.
    without_known_placeholders = _PLACEHOLDER_PATTERN.sub("", value)
    if "${" in without_known_placeholders or "}" in without_known_placeholders:
        raise ValueError("environment value contains a malformed placeholder")
    return value


def _environment_key_looks_secret(key: str) -> bool:
    segments = frozenset(key.split("_"))
    if segments & _SECRET_ENVIRONMENT_SEGMENTS:
        return True
    compact = key.replace("_", "")
    return any(marker in compact for marker in _SECRET_ENVIRONMENT_COMPOUNDS)


class WorkerPackPlatform(WorkerPackModel):
    os: Literal["windows", "macos", "linux"]
    architecture: Literal["x86_64", "arm64"]
    accelerator: Literal["cpu", "cuda", "metal"]
    accelerator_version: str | None = Field(default=None, min_length=1, max_length=64)
    python_abi: str | None = Field(default=None, pattern=r"^cp\d{2,3}$")

    @model_validator(mode="after")
    def validate_accelerator(self) -> WorkerPackPlatform:
        if self.accelerator == "metal" and (self.os, self.architecture) != ("macos", "arm64"):
            raise ValueError("metal worker packs require macos/arm64")
        if self.accelerator == "cuda" and self.os == "macos":
            raise ValueError("cuda worker packs are not supported on macos")
        return self


class WorkerPackFile(WorkerPackModel):
    path: str = Field(min_length=1, max_length=512)
    size: int = Field(ge=0, le=WORKER_PACK_MAX_FILE_BYTES)
    sha256: str = Field(pattern=_SHA256_PATTERN)
    role: Literal["runtime", "library", "model", "metadata", "license", "other"] = "other"

    @field_validator("path")
    @classmethod
    def validate_path(cls, value: str) -> str:
        value = _validate_portable_relative_path(value)
        if value.casefold() in _RESERVED_PACK_FILES:
            raise ValueError("payload path is reserved by the worker pack format")
        return value


class WorkerPackEntrypoint(WorkerPackModel):
    executable: str = Field(min_length=1, max_length=512)
    arguments: list[str] = Field(default_factory=list, max_length=64)
    working_directory: str = Field(default=".", min_length=1, max_length=512)
    environment: dict[str, str] = Field(default_factory=dict, max_length=64)
    health_path: str = Field(default="/v1/health", pattern=r"^/[A-Za-z0-9_./-]{1,127}$")
    capabilities_path: str = Field(default="/v1/capabilities", pattern=r"^/[A-Za-z0-9_./-]{1,127}$")
    startup_timeout_seconds: float = Field(default=120.0, gt=0, le=900)
    shutdown_timeout_seconds: float = Field(default=10.0, gt=0, le=120)

    @field_validator("executable")
    @classmethod
    def validate_executable(cls, value: str) -> str:
        return _validate_portable_relative_path(value)

    @field_validator("working_directory")
    @classmethod
    def validate_working_directory(cls, value: str) -> str:
        return _validate_portable_relative_path(value, allow_current=True)

    @field_validator("arguments")
    @classmethod
    def validate_arguments(cls, values: list[str]) -> list[str]:
        for value in values:
            if not value or len(value) > 2_048 or "\x00" in value:
                raise ValueError(
                    "entrypoint arguments must be non-empty and at most 2048 characters"
                )
            _validate_environment_value(value)
        return values

    @field_validator("environment")
    @classmethod
    def validate_environment(cls, values: dict[str, str]) -> dict[str, str]:
        for key, value in values.items():
            if not _ENVIRONMENT_KEY_PATTERN.fullmatch(key):
                raise ValueError(f"invalid environment variable name: {key!r}")
            if len(value) > 8_192:
                raise ValueError(f"environment value for {key!r} is too long")
            _validate_environment_value(value)
        return values


class WorkerPackWorker(WorkerPackModel):
    kind: Literal["stt", "tts"]
    backend: str = Field(pattern=_IDENTIFIER_PATTERN)
    provider_id: str = Field(pattern=_IDENTIFIER_PATTERN)
    display_name: str = Field(min_length=1, max_length=128)
    model: str = Field(min_length=1, max_length=256)
    entrypoint: WorkerPackEntrypoint

    @model_validator(mode="after")
    def validate_environment_scope(self) -> WorkerPackWorker:
        prefix = _WORKER_ENVIRONMENT_PREFIXES[self.kind]
        forbidden = {f"{prefix}{suffix}" for suffix in _SUPERVISOR_ENVIRONMENT_SUFFIXES}
        for key in self.entrypoint.environment:
            if not key.startswith(prefix):
                raise ValueError(f"{self.kind} pack environment key must start with {prefix}")
            if key in forbidden:
                raise ValueError(f"{key} is owned by the worker supervisor")
            if _environment_key_looks_secret(key):
                raise ValueError(f"{key} looks like a secret and must not be stored in a pack")
        return self


class WorkerPackLicense(WorkerPackModel):
    name: str = Field(min_length=1, max_length=128)
    spdx_id: str | None = Field(default=None, min_length=1, max_length=64)
    url: str | None = Field(default=None, pattern=r"^https://[^\s]{1,2040}$")
    file: str | None = Field(default=None, min_length=1, max_length=512)

    @field_validator("file")
    @classmethod
    def validate_file(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return _validate_portable_relative_path(value)


class WorkerPackManifest(WorkerPackModel):
    schema_version: Literal["1.0"] = WORKER_PACK_SCHEMA_VERSION
    pack_id: str = Field(pattern=_IDENTIFIER_PATTERN)
    version: str = Field(pattern=_SEMVER_PATTERN)
    platform: WorkerPackPlatform
    worker: WorkerPackWorker
    files: list[WorkerPackFile] = Field(min_length=1, max_length=WORKER_PACK_MAX_FILE_COUNT)
    licenses: list[WorkerPackLicense] = Field(min_length=1, max_length=128)

    @model_validator(mode="after")
    def validate_payload_references(self) -> WorkerPackManifest:
        paths: dict[str, str] = {}
        for file in self.files:
            folded = file.path.casefold()
            if folded in paths:
                raise ValueError(
                    f"payload paths collide on case-insensitive filesystems: "
                    f"{paths[folded]!r} and {file.path!r}"
                )
            paths[folded] = file.path
        executable = self.worker.entrypoint.executable.casefold()
        if executable not in paths:
            raise ValueError("entrypoint executable must be listed in files")
        if self.platform.os == "windows" and PurePosixPath(executable).suffix != ".exe":
            raise ValueError("Windows worker pack entrypoint must be an .exe file")
        expanded_bytes = sum(file.size for file in self.files)
        if expanded_bytes > WORKER_PACK_MAX_EXPANDED_BYTES:
            raise ValueError(
                "worker pack expanded payload exceeds the supported size limit "
                f"of {WORKER_PACK_MAX_EXPANDED_BYTES} bytes"
            )
        for license_metadata in self.licenses:
            if license_metadata.file is not None and license_metadata.file.casefold() not in paths:
                raise ValueError(f"license file {license_metadata.file!r} must be listed in files")
        return self


class WorkerPackInstallReceipt(WorkerPackModel):
    schema_version: Literal["1.0"] = WORKER_PACK_RECEIPT_SCHEMA_VERSION
    pack_id: str = Field(pattern=_IDENTIFIER_PATTERN)
    version: str = Field(pattern=_SEMVER_PATTERN)
    manifest_sha256: str = Field(pattern=_SHA256_PATTERN)
    archive_sha256: str = Field(pattern=_SHA256_PATTERN)
    installed_at: datetime
    verified_file_count: int = Field(ge=1, le=100_000)


class WorkerPackSelection(WorkerPackModel):
    pack_id: str = Field(pattern=_IDENTIFIER_PATTERN)
    version: str = Field(pattern=_SEMVER_PATTERN)


class WorkerPackActiveSelection(WorkerPackModel):
    stt: WorkerPackSelection | None = None
    tts: WorkerPackSelection | None = None


class WorkerPackActivationConfig(WorkerPackModel):
    schema_version: Literal["1.0"] = WORKER_PACK_SELECTION_SCHEMA_VERSION
    active: WorkerPackActiveSelection = Field(default_factory=WorkerPackActiveSelection)
