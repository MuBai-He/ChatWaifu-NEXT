"""Registry-driven cloud TTS configuration and secret persistence tests."""

from __future__ import annotations

import asyncio
import json
import os
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
from chatwaifu_runtime.bootstrap.container import RuntimeContainer
from chatwaifu_runtime.config.settings import Settings
from chatwaifu_runtime.persistence.atomic_secret_store import (
    AtomicSecretStore,
    SecretStoreError,
)
from chatwaifu_runtime.providers.tts_config import (
    ALIYUN_COSYVOICE_TTS_PROVIDER_ID,
    ALIYUN_QWEN_TTS_PROVIDER_ID,
    AliyunCosyVoiceTtsConfiguration,
)


@pytest.mark.asyncio
async def test_tts_configuration_registry_seeds_validates_and_persists_each_provider(
    runtime_settings: Settings,
) -> None:
    container = RuntimeContainer(runtime_settings)
    await container.start()
    try:
        service = container.tts_configurations
        registrations = service.registrations()
        assert {registration.provider_id for registration in registrations} == {
            ALIYUN_QWEN_TTS_PROVIDER_ID,
            ALIYUN_COSYVOICE_TTS_PROVIDER_ID,
        }
        assert all("properties" in registration.schema() for registration in registrations)

        configured = service.validate_update(
            ALIYUN_COSYVOICE_TTS_PROVIDER_ID,
            {
                "enabled": True,
                "model": "cosyvoice-v3.5-plus",
                "voice_id": "cosyvoice-clone",
                "region": "beijing",
                "workspace_id": "",
                "language_type": "auto",
                "sample_rate": 24_000,
                "speech_rate": 1.0,
                "volume": 50,
                "pitch_rate": 1.0,
                "instruction": "温柔自然",
                "timeout_seconds": 45.0,
                "max_audio_bytes": 32_000_000,
            },
        )
        assert isinstance(configured, AliyunCosyVoiceTtsConfiguration)
        saved = await service.update(configured, api_key="provider-secret")
        assert saved.enabled is True
        assert saved.api_key_configured is True
        assert service.api_key(ALIYUN_COSYVOICE_TTS_PROVIDER_ID) == "provider-secret"

        with pytest.raises(KeyError):
            service.validate_update("unknown-provider", {})
    finally:
        await container.stop()


def test_atomic_secret_store_keeps_concurrent_updates_and_restrictive_permissions(
    tmp_path: Path,
) -> None:
    secret_path = tmp_path / "config" / "provider-secrets.json"
    store = AtomicSecretStore(secret_path)

    def set_secret(index: int) -> None:
        store.set(f"provider-{index}", f"secret-{index}")

    with ThreadPoolExecutor(max_workers=8) as executor:
        list(executor.map(set_secret, range(24)))

    document = json.loads(secret_path.read_text(encoding="utf-8"))
    assert document == {f"provider-{index}": f"secret-{index}" for index in range(24)}
    if os.name != "nt":
        assert secret_path.stat().st_mode & 0o777 == 0o600

    store.prune({"provider-3", "provider-17"})
    assert store.get("provider-3") == "secret-3"
    assert store.get("provider-17") == "secret-17"
    assert store.get("provider-1") is None


def test_atomic_secret_store_refuses_to_overwrite_corrupt_or_unreadable_data(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    secret_path = tmp_path / "provider-secrets.json"
    secret_path.write_text("{ definitely-not-json", encoding="utf-8")
    store = AtomicSecretStore(secret_path)

    with pytest.raises(SecretStoreError, match="corrupt"):
        store.set("provider", "replacement")
    assert secret_path.read_text(encoding="utf-8") == "{ definitely-not-json"

    original_read_text = Path.read_text

    def fail_read_text(path: Path, *args: object, **kwargs: object) -> str:
        if path == secret_path:
            raise OSError("injected unreadable file")
        return original_read_text(path, *args, **kwargs)  # type: ignore[arg-type]

    secret_path.write_text('{"provider":"original"}', encoding="utf-8")
    monkeypatch.setattr(Path, "read_text", fail_read_text)
    with pytest.raises(SecretStoreError, match="unreadable"):
        store.prune(set())


@pytest.mark.asyncio
async def test_tts_secret_write_failure_keeps_database_and_previous_secret(
    runtime_settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    container = RuntimeContainer(runtime_settings)
    await container.start()
    try:
        service = container.tts_configurations
        original = service.get_cosyvoice()
        await service.update(original, api_key="original-secret")
        updated = original.model_copy(update={"model": "cosyvoice-v3-flash"})
        original_set = service._secrets.set  # pyright: ignore[reportPrivateUsage]
        calls = 0

        def fail_once(name: str, value: str | None) -> None:
            nonlocal calls
            calls += 1
            if calls == 1:
                raise OSError("injected secret replacement failure")
            original_set(name, value)

        monkeypatch.setattr(
            service._secrets,  # pyright: ignore[reportPrivateUsage]
            "set",
            fail_once,
        )
        with pytest.raises(OSError, match="secret replacement"):
            await service.update(updated, clear_api_key=True)

        row = await container.database.fetchone(
            "SELECT model FROM tts_cloud_configs WHERE provider_id = ?",
            (ALIYUN_COSYVOICE_TTS_PROVIDER_ID,),
        )
        assert row is not None and row["model"] == original.model
        assert service.api_key(ALIYUN_COSYVOICE_TTS_PROVIDER_ID) == "original-secret"
    finally:
        await container.stop()


@pytest.mark.asyncio
async def test_tts_database_failure_compensates_secret_and_discards_journal(
    runtime_settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    container = RuntimeContainer(runtime_settings)
    await container.start()
    try:
        service = container.tts_configurations
        original = service.get_cosyvoice()
        await service.update(original, api_key="original-secret")

        async def fail_persist(*_args: object, **_kwargs: object) -> None:
            raise RuntimeError("injected database failure")

        monkeypatch.setattr(service, "_persist_config", fail_persist)
        with pytest.raises(RuntimeError, match="database failure"):
            await service.update(
                original.model_copy(update={"model": "cosyvoice-v3-flash"}),
                api_key="next-secret",
            )

        assert service.api_key(ALIYUN_COSYVOICE_TTS_PROVIDER_ID) == "original-secret"
        assert (
            service._secret_mutations.get(  # pyright: ignore[reportPrivateUsage]
                ALIYUN_COSYVOICE_TTS_PROVIDER_ID
            )
            is None
        )
    finally:
        await container.stop()


@pytest.mark.asyncio
async def test_tts_committed_update_with_leftover_journal_recovers_on_restart(
    runtime_settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    first = RuntimeContainer(runtime_settings)
    await first.start()
    service = first.tts_configurations
    original_discard = (
        service._secret_mutations.discard  # pyright: ignore[reportPrivateUsage]
    )

    def fail_discard(_provider_id: str) -> None:
        raise OSError("injected journal cleanup failure")

    monkeypatch.setattr(
        service._secret_mutations,  # pyright: ignore[reportPrivateUsage]
        "discard",
        fail_discard,
    )
    updated = service.get_cosyvoice().model_copy(update={"model": "cosyvoice-v3-flash"})
    with pytest.raises(OSError, match="journal cleanup"):
        await service.update(updated, api_key="committed-secret")
    monkeypatch.setattr(
        service._secret_mutations,  # pyright: ignore[reportPrivateUsage]
        "discard",
        original_discard,
    )
    await first.stop()

    restarted = RuntimeContainer(runtime_settings)
    await restarted.start()
    try:
        assert restarted.tts_configurations.get_cosyvoice().model == "cosyvoice-v3-flash"
        assert (
            restarted.tts_configurations.api_key(ALIYUN_COSYVOICE_TTS_PROVIDER_ID)
            == "committed-secret"
        )
        assert (
            restarted.tts_configurations._secret_mutations.get(  # pyright: ignore[reportPrivateUsage]
                ALIYUN_COSYVOICE_TTS_PROVIDER_ID
            )
            is None
        )
    finally:
        await restarted.stop()


@pytest.mark.asyncio
async def test_tts_configuration_updates_are_serialized_with_their_secrets(
    runtime_settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    container = RuntimeContainer(runtime_settings)
    await container.start()
    try:
        service = container.tts_configurations
        original_persist = service._persist_config  # pyright: ignore[reportPrivateUsage]
        first_persist_entered = asyncio.Event()
        release_first_persist = asyncio.Event()
        persist_calls = 0

        async def gated_persist(*args: object, **kwargs: object) -> None:
            nonlocal persist_calls
            persist_calls += 1
            if persist_calls == 1:
                first_persist_entered.set()
                await release_first_persist.wait()
            await original_persist(*args, **kwargs)  # type: ignore[arg-type]

        monkeypatch.setattr(service, "_persist_config", gated_persist)
        original = service.get_cosyvoice()
        first_task = asyncio.create_task(
            service.update_patch(
                ALIYUN_COSYVOICE_TTS_PROVIDER_ID,
                {"volume": 41},
                api_key="first-secret",
            )
        )
        await asyncio.wait_for(first_persist_entered.wait(), timeout=1)
        second_task = asyncio.create_task(
            service.update_patch(
                ALIYUN_COSYVOICE_TTS_PROVIDER_ID,
                {"speech_rate": 1.35},
                api_key="second-secret",
            )
        )
        await asyncio.sleep(0)

        assert persist_calls == 1
        assert not second_task.done()
        assert service.get_cosyvoice().volume == original.volume
        assert service.api_key(ALIYUN_COSYVOICE_TTS_PROVIDER_ID) is None

        release_first_persist.set()
        await asyncio.gather(first_task, second_task)

        assert service.get_cosyvoice().volume == 41
        assert service.get_cosyvoice().speech_rate == 1.35
        assert service.api_key(ALIYUN_COSYVOICE_TTS_PROVIDER_ID) == "second-secret"
        row = await container.database.fetchone(
            "SELECT volume, speech_rate FROM tts_cloud_configs WHERE provider_id = ?",
            (ALIYUN_COSYVOICE_TTS_PROVIDER_ID,),
        )
        assert row is not None and row["volume"] == 41 and row["speech_rate"] == 1.35
        assert (
            service._secret_mutations.get(  # pyright: ignore[reportPrivateUsage]
                ALIYUN_COSYVOICE_TTS_PROVIDER_ID
            )
            is None
        )
    finally:
        await container.stop()
