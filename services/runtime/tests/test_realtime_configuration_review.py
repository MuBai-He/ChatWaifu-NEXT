"""Independent regression checks for configuration commit boundaries."""

# pyright: reportPrivateUsage=false

import asyncio
from pathlib import Path

import pytest
from chatwaifu_runtime.config.settings import Settings, StorageConfig
from chatwaifu_runtime.main import create_app
from chatwaifu_runtime.persistence.database import Database
from chatwaifu_runtime.persistence.sqlite_realtime_config import RealtimeConfigurationRecord
from chatwaifu_runtime.realtime.configuration import (
    RealtimeConfigurationService,
    RealtimeConfigurationUpdateRequest,
)
from fastapi.testclient import TestClient


def settings_at(path: Path) -> Settings:
    return Settings(
        config_dir=path / "config",
        data_dir=path,
        storage=StorageConfig(database_path=path / "runtime.db"),
    )


def update() -> RealtimeConfigurationUpdateRequest:
    return RealtimeConfigurationUpdateRequest(
        expected_revision=1, connection_mode="cascade", api_key="review-dummy-secret"
    )


@pytest.mark.asyncio
async def test_cancel_after_commit_keeps_durable_secret(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = settings_at(tmp_path)
    assert settings.storage.database_path is not None
    db = Database(settings.storage.database_path, settings.storage)
    await db.open()
    try:
        service = RealtimeConfigurationService(db, settings)
        await service.start()
        original = service._repository.update_configuration_cas
        committed = asyncio.Event()
        release = asyncio.Event()

        async def delayed(expected: int, record: RealtimeConfigurationRecord) -> bool:
            result = await original(expected, record)
            committed.set()
            await release.wait()
            return result

        monkeypatch.setattr(service._repository, "update_configuration_cas", delayed)
        task = asyncio.create_task(service.update_configuration(update()))
        async with asyncio.timeout(5):
            await committed.wait()
            task.cancel()
            release.set()
            with pytest.raises(asyncio.CancelledError):
                await task
        reloaded = RealtimeConfigurationService(db, settings)
        await reloaded.start()
        assert reloaded.current_snapshot().revision == 2
        assert reloaded.current_snapshot().api_key == "review-dummy-secret"
        assert service.current_snapshot() == reloaded.current_snapshot()
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_prune_failure_does_not_leave_runtime_on_old_revision(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = settings_at(tmp_path)
    assert settings.storage.database_path is not None
    db = Database(settings.storage.database_path, settings.storage)
    await db.open()
    try:
        service = RealtimeConfigurationService(db, settings)
        await service.start()

        def fail(_retained: set[str]) -> None:
            raise OSError("simulated cleanup failure")

        monkeypatch.setattr(service._secret_store, "prune", fail)
        try:
            await service.update_configuration(update())
        except OSError:
            pass
        reloaded = RealtimeConfigurationService(db, settings)
        await reloaded.start()
        assert service.current_snapshot() == reloaded.current_snapshot()
    finally:
        await db.close()


def test_validation_never_echoes_secret_as_extra_property_name(tmp_path: Path) -> None:
    app = create_app(settings_at(tmp_path))
    client = TestClient(
        app, headers={"Authorization": f"Bearer {app.state.container.capability_token}"}
    )
    response = client.put(
        "/v1/realtime/configuration",
        json={**update().model_dump(), "review-dummy-secret": "unexpected"},
    )
    assert response.status_code == 422
    assert "review-dummy-secret" not in response.text


@pytest.mark.asyncio
async def test_saved_bootstrap_profile_outlives_environment_changes(tmp_path: Path) -> None:
    from chatwaifu_runtime.bootstrap.container import RuntimeContainer
    from chatwaifu_runtime.realtime.cloud.openai import OpenAIRealtimeBackend
    from chatwaifu_runtime.realtime.pipecat.session import PipecatMediaAdapter

    settings = Settings.model_validate(
        {
            "config_dir": tmp_path / "config",
            "data_dir": tmp_path,
            "storage": {"database_path": tmp_path / "runtime.db"},
            "llm": {"provider": "demo"},
            "tts": {"provider": "fake"},
            "privacy": {"cloud_egress": "allow"},
            "realtime": {
                "connection_mode": "cloud_realtime",
                "cloud_backend": "openai",
                "openai": {"model": "saved-model", "api_key": "saved-dummy-key"},
            },
        }
    )
    first = RuntimeContainer(settings)
    await first.start()
    await first.stop()
    changed = settings.model_dump()
    changed["realtime"]["openai"].update(model="later-env-model", api_key="later-dummy-key")
    second = RuntimeContainer(Settings.model_validate(changed))
    await second.start()
    try:
        snap = second.realtime_configuration.current_snapshot()
        assert snap.revision == 1 and snap.model == "saved-model"
        backend = second._create_cloud_backend_for_snapshot(snap)
        assert isinstance(backend, OpenAIRealtimeBackend)
        assert backend._config.model == "saved-model"
        assert backend._config.api_key is not None
        assert backend._config.api_key.get_secret_value() == "saved-dummy-key"
        adapter = second.voice_media._adapter
        assert isinstance(adapter, PipecatMediaAdapter)
        assert adapter._resolve_bridge_factory(snap) != adapter._cloud_bridge_factory
    finally:
        await second.stop()


@pytest.mark.asyncio
async def test_offer_freezes_revision_across_save_and_renegotiation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from collections.abc import Awaitable, Callable
    from unittest.mock import AsyncMock, MagicMock
    from uuid import UUID, uuid4

    from chatwaifu_runtime.config.settings import RealtimeConfig, SttConfig
    from chatwaifu_runtime.realtime.cloud.media import CloudRealtimeMediaBridge
    from chatwaifu_runtime.realtime.configuration import RealtimeConnectionSnapshot
    from chatwaifu_runtime.realtime.pipecat.session import PipecatMediaAdapter, WebRtcOffer
    from pipecat.transports.smallwebrtc.connection import SmallWebRTCConnection
    from pipecat.transports.smallwebrtc.request_handler import SmallWebRTCRequest

    settings = settings_at(tmp_path)
    assert settings.storage.database_path is not None
    db = Database(settings.storage.database_path, settings.storage)
    await db.open()
    service = RealtimeConfigurationService(db, settings)
    await service.start()
    built: list[RealtimeConnectionSnapshot] = []

    def build(
        snapshot: RealtimeConnectionSnapshot,
    ) -> Callable[[UUID], Awaitable[CloudRealtimeMediaBridge]]:
        built.append(snapshot)
        return AsyncMock(return_value=MagicMock(spec=CloudRealtimeMediaBridge))

    adapter = PipecatMediaAdapter(
        config=RealtimeConfig(),
        stt_config=SttConfig(),
        publisher=MagicMock(),
        event_hub=MagicMock(),
        conversation=MagicMock(),
        audio_assets=MagicMock(),
        stt=MagicMock(),
        companion_settings=MagicMock(),
        activity=MagicMock(),
        resource_activity=lambda: None,
        configuration_service=service,
        bridge_factory_builder=build,
    )
    entered = asyncio.Event()
    release = asyncio.Event()
    counter = 0

    async def signal(
        request: SmallWebRTCRequest, callback: Callable[[SmallWebRTCConnection], Awaitable[None]]
    ) -> dict[str, str]:
        nonlocal counter
        counter += 1
        connection = MagicMock(spec=SmallWebRTCConnection)
        connection.pc_id = request.pc_id or f"peer-{counter}"
        connection.disconnect = AsyncMock()
        await callback(connection)
        if counter == 1:
            entered.set()
            await release.wait()
        return {"pc_id": connection.pc_id, "sdp": "answer", "type": "answer"}

    async def run(_session: UUID, _connection: SmallWebRTCConnection, _mode: str) -> None:
        await asyncio.Event().wait()

    monkeypatch.setattr(adapter._handler, "handle_web_request", signal)
    monkeypatch.setattr(adapter, "_run_connection", run)
    sid = uuid4()
    first: asyncio.Task[dict[str, str]] | None = None
    try:
        async with asyncio.timeout(10):
            first = asyncio.create_task(adapter.offer(sid, WebRtcOffer(sdp="offer", type="offer")))
            await entered.wait()
            await service.update_configuration(
                RealtimeConfigurationUpdateRequest(
                    expected_revision=1,
                    connection_mode="cloud_realtime",
                    model="new-model",
                    api_key="test-only-key",
                    cloud_egress_consent=True,
                )
            )
            release.set()
            answer = await first
            pc = answer["pc_id"]
            assert adapter._pc_snapshots[pc].snapshot.revision == 1
            assert adapter._pc_snapshots[pc].snapshot.connection_mode == "cascade"
            await adapter.offer(sid, WebRtcOffer(sdp="renegotiate", type="offer", pc_id=pc))
            assert adapter._pc_snapshots[pc].snapshot.revision == 1
            assert built == []
            fresh = await adapter.offer(sid, WebRtcOffer(sdp="fresh", type="offer"))
            assert built[0].revision == 2 and built[0].model == "new-model"
            assert adapter._pc_snapshots[fresh["pc_id"]].snapshot == built[0]
            await adapter.close()
            assert not adapter._pc_snapshots and adapter.active_connections == 0
    finally:
        release.set()
        if first is not None and not first.done():
            first.cancel()
            await asyncio.gather(first, return_exceptions=True)
        await adapter.close()
        await db.close()
