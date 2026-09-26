"""Headless credential durability and fail-closed boundaries."""

import asyncio
import os
from pathlib import Path
from threading import Event

import pytest
from chatwaifu_runtime.external_channels.credentials import ChannelCredentialStoreError
from chatwaifu_runtime.external_channels.encrypted_credentials import (
    EncryptedFileChannelCredentialStore,
)

pytestmark = pytest.mark.skipif(os.name != "posix", reason="explicit POSIX server backend")


def store(root: Path) -> EncryptedFileChannelCredentialStore:
    return EncryptedFileChannelCredentialStore(root / "data", root / "key")


async def test_restart_encryption_and_concurrent_updates(tmp_path: Path) -> None:
    first, second = store(tmp_path), store(tmp_path)
    assert await first.available()
    await asyncio.gather(first.set("one", "secret-value"), second.set("two", "other"))
    restored = store(tmp_path)
    assert await restored.get("one") == "secret-value"
    assert await restored.get("two") == "other"
    assert b"secret-value" not in (tmp_path / "data/credentials.fernet").read_bytes()
    assert (tmp_path / "key/master.key").stat().st_mode & 0o777 == 0o600
    await restored.delete("one")
    assert await store(tmp_path).get("one") is None
    assert await store(tmp_path).get("two") == "other"


@pytest.mark.parametrize("failure", ["missing_key", "corrupt", "permissions", "symlink"])
async def test_invalid_vault_is_not_overwritten(tmp_path: Path, failure: str) -> None:
    current = store(tmp_path)
    await current.set("one", "secret")
    key, data = tmp_path / "key/master.key", tmp_path / "data/credentials.fernet"
    if failure == "missing_key":
        key.unlink()
    elif failure == "corrupt":
        data.write_bytes(b"corrupt")
    elif failure == "permissions":
        key.chmod(0o644)
    else:
        original = key.with_name("original.key")
        key.rename(original)
        key.symlink_to(original)
    before = data.read_bytes()
    assert not await current.available()
    with pytest.raises(ChannelCredentialStoreError):
        await current.set("two", "new-secret")
    assert data.read_bytes() == before
    if failure == "missing_key":
        assert not key.exists()


@pytest.mark.parametrize("fail", [False, True])
async def test_cancelled_write_finishes_before_returning(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fail: bool
) -> None:
    current = store(tmp_path)
    started, release = Event(), Event()
    original = current._operate  # pyright: ignore[reportPrivateUsage]

    def blocked(action: str, reference: str, value: str | None) -> str | None:
        started.set()
        assert release.wait(5)
        if fail:
            raise ChannelCredentialStoreError("failed I/O")
        return original(action, reference, value)

    monkeypatch.setattr(current, "_operate", blocked)
    task = asyncio.create_task(current.set("one", "secret"))
    assert await asyncio.to_thread(started.wait, 5)
    task.cancel()
    release.set()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert await store(tmp_path).get("one") == (None if fail else "secret")
