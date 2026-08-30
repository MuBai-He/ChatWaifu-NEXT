"""Secure credential-store port for external channel adapters."""

from __future__ import annotations

import asyncio
from typing import Protocol, cast


class _KeyringBackend(Protocol):
    priority: float


class _KeyringModule(Protocol):
    def get_keyring(self) -> _KeyringBackend: ...

    def get_password(self, service_name: str, username: str) -> str | None: ...

    def set_password(self, service_name: str, username: str, password: str) -> None: ...

    def delete_password(self, service_name: str, username: str) -> None: ...


class ChannelCredentialStoreError(RuntimeError):
    """The operating-system credential store is unavailable or rejected an operation."""


class ChannelCredentialStore(Protocol):
    async def available(self) -> bool: ...

    async def get(self, reference: str) -> str | None: ...

    async def set(self, reference: str, value: str) -> None: ...

    async def delete(self, reference: str) -> None: ...


class KeyringChannelCredentialStore:
    """Store channel credentials in the platform keyring without plaintext fallback.

    macOS uses Keychain, Windows uses Credential Manager, and supported Linux
    desktops use Secret Service/KWallet through ``keyring``. A missing secure
    backend is reported to the caller; this adapter never falls back to a file.
    """

    def __init__(self, service_name: str = "ai.chatwaifu.next.external-channels") -> None:
        self._service_name = service_name

    async def available(self) -> bool:
        return await asyncio.to_thread(self._available_sync)

    async def get(self, reference: str) -> str | None:
        return await asyncio.to_thread(self._get_sync, _validated_reference(reference))

    async def set(self, reference: str, value: str) -> None:
        if not value:
            raise ValueError("channel credential value cannot be empty")
        await asyncio.to_thread(self._set_sync, _validated_reference(reference), value)

    async def delete(self, reference: str) -> None:
        await asyncio.to_thread(self._delete_sync, _validated_reference(reference))

    @staticmethod
    def _keyring() -> _KeyringModule:
        try:
            import keyring
        except ImportError as error:  # pragma: no cover - dependency packaging guard
            raise ChannelCredentialStoreError(
                "the platform credential-store dependency is unavailable"
            ) from error
        return cast(_KeyringModule, keyring)

    def _available_sync(self) -> bool:
        try:
            keyring = self._keyring()
            backend = keyring.get_keyring()
            priority = float(backend.priority)
            if priority <= 0:
                return False
            # Backend discovery alone is insufficient on headless Linux or a
            # locked keychain. A non-secret missing-key read proves that the
            # selected backend is actually reachable without writing anything.
            keyring.get_password(self._service_name, "__health_probe__")
        except Exception:
            return False
        return True

    def _required_keyring(self) -> _KeyringModule:
        keyring = self._keyring()
        try:
            backend = keyring.get_keyring()
            if float(backend.priority) <= 0:
                raise ChannelCredentialStoreError(
                    "no secure operating-system credential store is available"
                )
        except ChannelCredentialStoreError:
            raise
        except Exception as error:
            raise ChannelCredentialStoreError(
                "no secure operating-system credential store is available"
            ) from error
        return keyring

    def _get_sync(self, reference: str) -> str | None:
        keyring = self._required_keyring()
        try:
            value = keyring.get_password(self._service_name, reference)
        except Exception as error:
            raise ChannelCredentialStoreError("credential read failed") from error
        return value if isinstance(value, str) and value else None

    def _set_sync(self, reference: str, value: str) -> None:
        keyring = self._required_keyring()
        try:
            keyring.set_password(self._service_name, reference, value)
        except Exception as error:
            raise ChannelCredentialStoreError("credential write failed") from error

    def _delete_sync(self, reference: str) -> None:
        keyring = self._required_keyring()
        try:
            keyring.delete_password(self._service_name, reference)
        except Exception as error:
            # ``delete_password`` does not standardize a missing-entry result.
            # Re-read to distinguish absence from a backend failure without
            # importing backend-specific exception classes into the port.
            try:
                if keyring.get_password(self._service_name, reference) is None:
                    return
            except Exception:
                pass
            raise ChannelCredentialStoreError("credential deletion failed") from error


class InMemoryChannelCredentialStore:
    """Deterministic fake used by Runtime tests."""

    def __init__(self, *, enabled: bool = True) -> None:
        self._enabled = enabled
        self._values: dict[str, str] = {}
        self._lock = asyncio.Lock()

    async def available(self) -> bool:
        return self._enabled

    async def get(self, reference: str) -> str | None:
        self._require_enabled()
        async with self._lock:
            return self._values.get(_validated_reference(reference))

    async def set(self, reference: str, value: str) -> None:
        self._require_enabled()
        if not value:
            raise ValueError("channel credential value cannot be empty")
        async with self._lock:
            self._values[_validated_reference(reference)] = value

    async def delete(self, reference: str) -> None:
        self._require_enabled()
        async with self._lock:
            self._values.pop(_validated_reference(reference), None)

    def _require_enabled(self) -> None:
        if not self._enabled:
            raise ChannelCredentialStoreError(
                "no secure operating-system credential store is available"
            )


def _validated_reference(reference: str) -> str:
    value = reference.strip()
    if not value or len(value) > 512 or value != reference:
        raise ValueError("invalid channel credential reference")
    return value
