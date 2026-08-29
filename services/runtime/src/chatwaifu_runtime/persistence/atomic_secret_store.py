"""Small crash-safe API for write-only local provider secrets."""

from __future__ import annotations

import json
import os
import threading
from pathlib import Path
from typing import cast
from uuid import uuid4


class SecretStoreError(RuntimeError):
    """A write-only secret store cannot be read without risking data loss."""


class AtomicSecretStore:
    """Persist string secrets with atomic replacement and restrictive modes.

    The API intentionally never exposes the full mapping. Callers can retrieve
    one named secret, replace one value, or prune names no longer referenced by
    durable configuration.
    """

    def __init__(self, path: Path) -> None:
        self._path = path
        self._lock = threading.RLock()

    def get(self, name: str) -> str | None:
        with self._lock:
            value = self._read().get(name)
        return value if value else None

    def set(self, name: str, value: str | None) -> None:
        with self._lock:
            secrets = self._read()
            if value:
                secrets[name] = value
            else:
                secrets.pop(name, None)
            self._replace(secrets)

    def prune(self, retained_names: set[str]) -> None:
        with self._lock:
            current = self._read()
            retained = {key: value for key, value in current.items() if key in retained_names}
            if retained != current:
                self._replace(retained)

    def _replace(self, secrets: dict[str, str]) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self._path.with_name(f".{self._path.name}.{uuid4().hex}.tmp")
        descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as secret_file:
                secret_file.write(
                    json.dumps(secrets, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
                )
                secret_file.flush()
                os.fsync(secret_file.fileno())
            os.replace(temporary, self._path)
            os.chmod(self._path, 0o600)
            try:
                directory = os.open(self._path.parent, os.O_RDONLY)
            except OSError:
                # Windows does not consistently allow opening directories for
                # fsync; os.replace above is still atomic on the same volume.
                return
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
        finally:
            temporary.unlink(missing_ok=True)

    def _read(self) -> dict[str, str]:
        try:
            serialized = self._path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return {}
        except OSError as error:
            raise SecretStoreError(
                f"secret storage is unreadable and will not be overwritten: {self._path}"
            ) from error
        try:
            value: object = json.loads(serialized)
        except json.JSONDecodeError as error:
            raise SecretStoreError(
                f"secret storage is corrupt and will not be overwritten: {self._path}"
            ) from error
        if not isinstance(value, dict):
            raise SecretStoreError(
                f"secret storage has an invalid document and will not be overwritten: {self._path}"
            )
        typed = cast(dict[object, object], value)
        if any(
            not isinstance(key, str) or not isinstance(item, str) or not item
            for key, item in typed.items()
        ):
            raise SecretStoreError(
                f"secret storage has invalid entries and will not be overwritten: {self._path}"
            )
        return cast(dict[str, str], typed)
