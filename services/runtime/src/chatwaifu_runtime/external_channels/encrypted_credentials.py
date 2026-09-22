"""Explicit POSIX server credential adapter; never an automatic keyring fallback."""

from __future__ import annotations

import asyncio
import json
import os
import stat
import tempfile
import threading
from pathlib import Path

from cryptography.fernet import Fernet
from pydantic import TypeAdapter

from chatwaifu_runtime.external_channels.credentials import (
    ChannelCredentialStoreError,
    _validated_reference,  # pyright: ignore[reportPrivateUsage]
)

_MAX_BYTES = 4 * 1024 * 1024


class EncryptedFileChannelCredentialStore:
    """Authenticated encryption with a separate, owner-only host key.

    Protects offline copies of the ciphertext, not compromise of the Runtime user.
    All instances serialize through a stable flock, including key initialization.
    Missing keys for an existing vault and corrupt files fail closed.
    """

    def __init__(self, directory: Path, key_directory: Path) -> None:
        self._directory = directory
        self._key_directory = key_directory
        self._path = directory / "credentials.fernet"
        self._key = key_directory / "master.key"
        self._lock = threading.Lock()

    async def available(self) -> bool:
        try:
            await self._run("read", "__health_probe__", None)
        except ChannelCredentialStoreError:
            return False
        return True

    async def get(self, reference: str) -> str | None:
        return await self._run("read", _validated_reference(reference), None)

    async def set(self, reference: str, value: str) -> None:
        if not value:
            raise ValueError("credential value must not be empty")
        await self._run("write", _validated_reference(reference), value)

    async def delete(self, reference: str) -> None:
        await self._run("write", _validated_reference(reference), None)

    async def _run(self, action: str, reference: str, value: str | None) -> str | None:
        task = asyncio.create_task(asyncio.to_thread(self._operate, action, reference, value))
        cancelled = False
        while not task.done():
            try:
                await asyncio.shield(task)
            except asyncio.CancelledError:
                cancelled = True
            except Exception:
                if cancelled:
                    raise asyncio.CancelledError from None
                raise
        if cancelled:
            task.exception()
            raise asyncio.CancelledError
        return task.result()

    def _operate(self, action: str, reference: str, value: str | None) -> str | None:
        try:
            import fcntl  # POSIX only; Windows continues using Credential Manager.

            with self._lock:
                self._private_directory(self._directory)
                self._private_directory(self._key_directory)
                lock_path = self._directory / "vault.lock"
                descriptor = os.open(lock_path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
                try:
                    self._validate_file(os.fstat(descriptor))
                    fcntl.flock(descriptor, fcntl.LOCK_EX)
                    return self._locked_operation(action, reference, value)
                finally:
                    os.close(descriptor)
        except Exception as error:
            raise ChannelCredentialStoreError(
                "encrypted channel credential store unavailable"
            ) from error

    def _locked_operation(self, action: str, reference: str, value: str | None) -> str | None:
        key = self._read(self._key)
        if key is None:
            if self._path.exists() or self._path.is_symlink():
                raise ValueError("existing vault requires its original key")
            key = Fernet.generate_key()
            self._replace(self._key, key)
        cipher = Fernet(key)
        encrypted = self._read(self._path)
        values: dict[str, str] = {}
        if encrypted is not None:
            values = TypeAdapter(dict[str, str]).validate_json(
                cipher.decrypt(encrypted), strict=True
            )
        if action == "read":
            # Materialize an empty encrypted vault so readiness also proves writes.
            if encrypted is None:
                self._replace(self._path, cipher.encrypt(b"{}"))
            return values.get(reference)
        if value is None:
            values.pop(reference, None)
        else:
            values[reference] = value
        self._replace(self._path, cipher.encrypt(json.dumps(values).encode()))
        return None

    @staticmethod
    def _private_directory(path: Path) -> None:
        path.mkdir(parents=True, exist_ok=True, mode=0o700)
        info = path.lstat()
        if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
            raise ValueError("credential directory must be owner-only")

    @staticmethod
    def _validate_file(info: os.stat_result) -> None:
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_uid != os.getuid()
            or info.st_mode & 0o077
            or info.st_nlink != 1
            or info.st_size > _MAX_BYTES
        ):
            raise ValueError("credential file must be private and bounded")

    @classmethod
    def _read(cls, path: Path) -> bytes | None:
        try:
            descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        except FileNotFoundError:
            return None
        with os.fdopen(descriptor, "rb") as source:
            cls._validate_file(os.fstat(source.fileno()))
            value = source.read(_MAX_BYTES + 1)
            if len(value) > _MAX_BYTES:
                raise ValueError("credential file too large")
            return value

    @staticmethod
    def _replace(path: Path, value: bytes) -> None:
        if len(value) > _MAX_BYTES:
            raise ValueError("credential vault too large")
        descriptor, name = tempfile.mkstemp(prefix=".vault-", dir=path.parent)
        temporary = Path(name)
        try:
            with os.fdopen(descriptor, "wb") as target:
                target.write(value)
                target.flush()
                os.fsync(target.fileno())
            os.replace(temporary, path)
            directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
        finally:
            temporary.unlink(missing_ok=True)
