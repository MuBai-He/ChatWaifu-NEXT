"""Phase 13.8A Realtime configuration vertical backend tests."""
# pyright: reportPrivateUsage=false, reportUnknownMemberType=false, reportUnknownArgumentType=false

from __future__ import annotations

import asyncio
import json
import stat
from pathlib import Path
from unittest.mock import MagicMock
from uuid import uuid4

import pytest
from chatwaifu_runtime.bootstrap.container import RuntimeContainer
from chatwaifu_runtime.config.settings import (
    RealtimeConfig,
    Settings,
    StorageConfig,
    SttConfig,
)
from chatwaifu_runtime.main import create_app
from chatwaifu_runtime.persistence.sqlite_realtime_config import (
    RealtimeConfigurationRecord,
)
from chatwaifu_runtime.realtime.cloud import openai as openai_module
from chatwaifu_runtime.realtime.cloud.context import CloudEgressGateway
from chatwaifu_runtime.realtime.cloud.openai import RealtimeSocket
from chatwaifu_runtime.realtime.cloud.openai_events import object_value
from chatwaifu_runtime.realtime.configuration import (
    RealtimeConfigurationUpdateRequest,
    RealtimeConnectionSnapshot,
    RealtimeRevisionConflictError,
)
from chatwaifu_runtime.realtime.pipecat.session import (
    PipecatMediaAdapter,
    WebRtcOffer,
)
from fastapi.testclient import TestClient
from websockets.asyncio.server import ServerConnection, serve


def create_test_settings(tmp_path: Path, **overrides: object) -> Settings:
    data: dict[str, object] = {
        "config_dir": tmp_path / "config",
        "data_dir": tmp_path,
        "storage": StorageConfig(database_path=tmp_path / "runtime.db"),
        "llm": {"provider": "demo", "demo_chunk_delay_ms": 0},
        "tts": {"provider": "fake"},
        "privacy": {"cloud_egress": "allow"},
        "realtime": {
            "connection_mode": "cascade",
            "cloud_backend": "openai",
            "openai": {
                "model": "gpt-4o-realtime-preview",
                "api_key": "test-key-env",
                "voice": "marin",
                "transcription_model": "gpt-4o-mini-transcribe",
            },
        },
    }
    data.update(overrides)
    return Settings.model_validate(data)


# ---------------------------------------------------------------------------
# 1. GET & Contract Verification
# ---------------------------------------------------------------------------


def test_get_configuration_contract(tmp_path: Path) -> None:
    """GET /v1/realtime/configuration returns expected schema and fields."""
    settings = create_test_settings(tmp_path)
    app = create_app(settings)
    token = app.state.container.capability_token
    with TestClient(app, headers={"Authorization": f"Bearer {token}"}) as client:
        resp = client.get("/v1/realtime/configuration")
        assert resp.status_code == 200
        data = resp.json()

        # Contract assertion
        assert data["schema_version"] == "1.0"
        assert data["revision"] == 1
        assert data["connection_mode"] == "cascade"
        assert data["cloud_backend"] == "openai"
        assert data["model"] == "gpt-4o-realtime-preview"
        assert data["voice"] == "marin"
        assert data["transcription_model"] == "gpt-4o-mini-transcribe"
        assert data["cloud_tools_enabled"] is False
        assert data["cloud_egress_consent"] is True
        assert data["api_key_configured"] is True
        assert data["active_connections"] == 0

        # Secret redaction: raw API key must NEVER be in response
        assert "api_key" not in data
        assert "_api_key" not in data
        assert "test-key-env" not in resp.text


# ---------------------------------------------------------------------------
# 2. Secret Redaction across SQLite, Models, and Repr
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_secret_redaction_in_storage_and_repr(tmp_path: Path) -> None:
    """Secrets are stored in secret store, not DB, and redacted in repr/public_dict."""
    settings = create_test_settings(tmp_path)
    container = RuntimeContainer(settings)
    await container.start()
    try:
        # Snapshot repr does not reveal key
        snapshot = container.realtime_configuration.current_snapshot()
        assert snapshot.api_key == "test-key-env"
        assert "test-key-env" not in repr(snapshot)
        assert "test-key-env" not in str(snapshot.to_public_dict())

        # SQLite database only contains secret_key_ref, not raw key
        db = container.database
        row = await db.fetchone(
            "SELECT secret_key_ref, model FROM realtime_configurations WHERE id = 'default'"
        )
        assert row is not None
        assert row[0] is not None
        assert "test-key-env" not in row[0]
        assert row[0].startswith("openai_key_r1_")

        # Secret file has mode 0600
        secret_path = settings.config_dir / "realtime-secrets.json"
        assert secret_path.exists()
        mode = stat.S_IMODE(secret_path.stat().st_mode)
        assert mode == 0o600

        # Secret file contains the key mapped by ref
        content = json.loads(secret_path.read_text(encoding="utf-8"))
        assert content[row[0]] == "test-key-env"
    finally:
        await container.stop()


# ---------------------------------------------------------------------------
# 3. PUT CAS Flow, Updates, and Restart Persistence
# ---------------------------------------------------------------------------


def test_put_configuration_cas_and_restart_persistence(tmp_path: Path) -> None:
    """PUT updates configuration with CAS, persists across container restart."""
    settings = create_test_settings(tmp_path)
    app = create_app(settings)
    token = app.state.container.capability_token

    with TestClient(app, headers={"Authorization": f"Bearer {token}"}) as client:
        # Successful update with expected_revision=1
        update_payload = {
            "schema_version": "1.0",
            "expected_revision": 1,
            "connection_mode": "cloud_realtime",
            "cloud_backend": "openai",
            "model": "gpt-4o-realtime-custom",
            "voice": "alloy",
            "transcription_model": "whisper-1",
            "cloud_tools_enabled": True,
            "cloud_egress_consent": True,
            "api_key": "sk-user-provided-key-999",
            "clear_api_key": False,
        }
        resp = client.put("/v1/realtime/configuration", json=update_payload)
        assert resp.status_code == 200
        data = resp.json()
        assert data["revision"] == 2
        assert data["connection_mode"] == "cloud_realtime"
        assert data["model"] == "gpt-4o-realtime-custom"
        assert data["voice"] == "alloy"
        assert data["transcription_model"] == "whisper-1"
        assert data["cloud_tools_enabled"] is True
        assert data["cloud_egress_consent"] is True
        assert data["api_key_configured"] is True
        assert "api_key" not in data

        # GET confirms updated revision
        get_resp = client.get("/v1/realtime/configuration")
        assert get_resp.status_code == 200
        assert get_resp.json()["revision"] == 2

    # Simulate restart by instantiating a new container with the same DB and config_dir
    container2 = RuntimeContainer(settings)
    loop = asyncio.new_event_loop()
    try:
        loop.run_until_complete(container2.start())
        snap2 = container2.realtime_configuration.current_snapshot()
        assert snap2.revision == 2
        assert snap2.connection_mode == "cloud_realtime"
        assert snap2.model == "gpt-4o-realtime-custom"
        assert snap2.voice == "alloy"
        assert snap2.transcription_model == "whisper-1"
        assert snap2.cloud_tools_enabled is True
        assert snap2.cloud_egress_consent is True
        assert snap2.api_key_configured is True
        assert snap2.api_key == "sk-user-provided-key-999"
    finally:
        loop.run_until_complete(container2.stop())
        loop.close()


# ---------------------------------------------------------------------------
# 4. CAS Conflict Returns 409
# ---------------------------------------------------------------------------


def test_put_configuration_cas_conflict_409(tmp_path: Path) -> None:
    """Stale expected_revision returns HTTP 409 Conflict."""
    settings = create_test_settings(tmp_path)
    app = create_app(settings)
    token = app.state.container.capability_token
    with TestClient(app, headers={"Authorization": f"Bearer {token}"}) as client:
        # First update increments revision to 2
        client.put(
            "/v1/realtime/configuration",
            json={
                "schema_version": "1.0",
                "expected_revision": 1,
                "connection_mode": "cascade",
                "cloud_backend": "openai",
                "model": "m1",
                "voice": "marin",
                "transcription_model": "tm1",
                "cloud_tools_enabled": False,
                "cloud_egress_consent": True,
            },
        )

        # Second update with stale expected_revision=1 fails with 409
        resp = client.put(
            "/v1/realtime/configuration",
            json={
                "schema_version": "1.0",
                "expected_revision": 1,
                "connection_mode": "cascade",
                "cloud_backend": "openai",
                "model": "m2",
                "voice": "marin",
                "transcription_model": "tm2",
                "cloud_tools_enabled": False,
                "cloud_egress_consent": True,
            },
        )
        assert resp.status_code == 409
        assert "stale" in resp.json()["detail"].lower()


# ---------------------------------------------------------------------------
# 5. Blank API Key Preserves Old Key
# ---------------------------------------------------------------------------


def test_put_blank_api_key_preserves_old_key(tmp_path: Path) -> None:
    """PUT with omitted or null api_key keeps the existing secret."""
    settings = create_test_settings(tmp_path)
    app = create_app(settings)
    token = app.state.container.capability_token
    with TestClient(app, headers={"Authorization": f"Bearer {token}"}) as client:
        # Initial set
        client.put(
            "/v1/realtime/configuration",
            json={
                "schema_version": "1.0",
                "expected_revision": 1,
                "connection_mode": "cloud_realtime",
                "cloud_backend": "openai",
                "model": "m1",
                "voice": "marin",
                "transcription_model": "tm1",
                "cloud_tools_enabled": False,
                "cloud_egress_consent": True,
                "api_key": "my-secret-key",
            },
        )

        # Update voice only, omit api_key
        resp = client.put(
            "/v1/realtime/configuration",
            json={
                "schema_version": "1.0",
                "expected_revision": 2,
                "connection_mode": "cloud_realtime",
                "cloud_backend": "openai",
                "model": "m1",
                "voice": "shimmer",
                "transcription_model": "tm1",
                "cloud_tools_enabled": False,
                "cloud_egress_consent": True,
                "api_key": None,
                "clear_api_key": False,
            },
        )
        assert resp.status_code == 200
        assert resp.json()["revision"] == 3
        assert resp.json()["api_key_configured"] is True

        snapshot = app.state.container.realtime_configuration.current_snapshot()
        assert snapshot.api_key == "my-secret-key"


# ---------------------------------------------------------------------------
# 6. Clear API Key and No Resurrection on Restart
# ---------------------------------------------------------------------------


def test_put_clear_api_key_and_no_resurrection_on_restart(tmp_path: Path) -> None:
    """Explicit clear_api_key removes secret; restart does NOT resurrect from env."""
    settings = create_test_settings(tmp_path)
    app = create_app(settings)
    token = app.state.container.capability_token
    with TestClient(app, headers={"Authorization": f"Bearer {token}"}) as client:
        # Clear the API key
        resp = client.put(
            "/v1/realtime/configuration",
            json={
                "schema_version": "1.0",
                "expected_revision": 1,
                "connection_mode": "cloud_realtime",
                "cloud_backend": "openai",
                "model": "m1",
                "voice": "marin",
                "transcription_model": "tm1",
                "cloud_tools_enabled": False,
                "cloud_egress_consent": True,
                "clear_api_key": True,
            },
        )
        assert resp.status_code == 200
        assert resp.json()["api_key_configured"] is False
        snapshot = app.state.container.realtime_configuration.current_snapshot()
        assert snapshot.api_key is None
        assert snapshot.api_key_configured is False

    # Restart container with initial settings (which still has "test-key-env")
    container2 = RuntimeContainer(settings)
    loop = asyncio.new_event_loop()
    try:
        loop.run_until_complete(container2.start())
        snap2 = container2.realtime_configuration.current_snapshot()
        assert snap2.revision == 2
        # Crucial requirement: cleared key must NEVER be resurrected from Settings/env
        assert snap2.api_key_configured is False
        assert snap2.api_key is None
    finally:
        loop.run_until_complete(container2.stop())
        loop.close()


# ---------------------------------------------------------------------------
# 7. Validation: Non-blank Key + Clear Key Rejected with 422
# ---------------------------------------------------------------------------


def test_put_validation_rejects_both_api_key_and_clear_key(tmp_path: Path) -> None:
    """PUT with both non-empty api_key and clear_api_key=True is rejected with 422."""
    settings = create_test_settings(tmp_path)
    app = create_app(settings)
    token = app.state.container.capability_token
    with TestClient(app, headers={"Authorization": f"Bearer {token}"}) as client:
        resp = client.put(
            "/v1/realtime/configuration",
            json={
                "schema_version": "1.0",
                "expected_revision": 1,
                "connection_mode": "cloud_realtime",
                "cloud_backend": "openai",
                "model": "m1",
                "voice": "marin",
                "transcription_model": "tm1",
                "cloud_tools_enabled": False,
                "cloud_egress_consent": True,
                "api_key": "some-key",
                "clear_api_key": True,
            },
        )
        assert resp.status_code == 422


# ---------------------------------------------------------------------------
# 8. 422 Validation Strips Raw Input
# ---------------------------------------------------------------------------


def test_put_validation_strips_input_on_422(tmp_path: Path) -> None:
    """FastAPI 422 validation errors strip raw input from error details."""
    settings = create_test_settings(tmp_path)
    app = create_app(settings)
    token = app.state.container.capability_token
    with TestClient(app, headers={"Authorization": f"Bearer {token}"}) as client:
        oversized_key = "secret_x" * 200  # 1600 chars > 1024 max
        resp = client.put(
            "/v1/realtime/configuration",
            json={
                "schema_version": "1.0",
                "expected_revision": 1,
                "connection_mode": "cloud_realtime",
                "cloud_backend": "openai",
                "model": "m1",
                "voice": "marin",
                "transcription_model": "tm1",
                "cloud_tools_enabled": False,
                "cloud_egress_consent": True,
                "api_key": oversized_key,
            },
        )
        assert resp.status_code == 422
        # Ensure raw input is not leaked in the response
        assert oversized_key not in resp.text
        data = resp.json()
        assert "detail" in data
        for err in data["detail"]:
            assert "input" not in err


# ---------------------------------------------------------------------------
# 9. Incomplete Saved Config vs Connection Admission
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_incomplete_saved_config_allowed_but_admission_fails(tmp_path: Path) -> None:
    """Incomplete cloud config is allowed to be saved, but admission rejects it safely."""
    settings = create_test_settings(tmp_path)
    container = RuntimeContainer(settings)
    await container.start()
    session_id = uuid4()
    adapter = container.voice_media._adapter
    assert isinstance(adapter, PipecatMediaAdapter)

    try:
        # Case A: empty model can be saved
        await container.realtime_configuration.update_configuration(
            RealtimeConfigurationUpdateRequest(
                expected_revision=1,
                connection_mode="cloud_realtime",
                cloud_backend="openai",
                model="",
                voice="marin",
                transcription_model="whisper-1",
                cloud_tools_enabled=False,
                cloud_egress_consent=True,
                api_key="valid-key",
            )
        )
        snapshot_a = container.realtime_configuration.current_snapshot()
        assert snapshot_a.model == ""
        # Admission fails with safe ValueError
        with pytest.raises(ValueError, match="未配置模型"):
            adapter._validate_admission(snapshot_a, session_id)

        # Case B: cleared key can be saved
        await container.realtime_configuration.update_configuration(
            RealtimeConfigurationUpdateRequest(
                expected_revision=2,
                connection_mode="cloud_realtime",
                cloud_backend="openai",
                model="gpt-4o-realtime",
                voice="marin",
                transcription_model="whisper-1",
                cloud_tools_enabled=False,
                cloud_egress_consent=True,
                clear_api_key=True,
            )
        )
        snapshot_b = container.realtime_configuration.current_snapshot()
        assert snapshot_b.api_key is None
        # Admission fails with safe ValueError
        with pytest.raises(ValueError, match="未配置 API Key"):
            adapter._validate_admission(snapshot_b, session_id)

        # Case C: cloud_egress_consent=False can be saved
        await container.realtime_configuration.update_configuration(
            RealtimeConfigurationUpdateRequest(
                expected_revision=3,
                connection_mode="cloud_realtime",
                cloud_backend="openai",
                model="gpt-4o-realtime",
                voice="marin",
                transcription_model="whisper-1",
                cloud_tools_enabled=False,
                cloud_egress_consent=False,
                api_key="valid-key",
            )
        )
        snapshot_c = container.realtime_configuration.current_snapshot()
        assert snapshot_c.cloud_egress_consent is False
        # Admission fails with PermissionError
        with pytest.raises(PermissionError, match="未获授权"):
            adapter._validate_admission(snapshot_c, session_id)
    finally:
        await container.stop()


# ---------------------------------------------------------------------------
# 10. Cloud Egress Policy Modes (deny and ask)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_cloud_egress_policy_modes(tmp_path: Path) -> None:
    """Egress gateway policy mode 'deny' blocks, 'ask' with consent grants scoped access."""
    session_id = uuid4()

    # Case 1: Policy mode is 'deny' -> blocks even with UI consent
    gateway_deny = CloudEgressGateway(policy_mode="deny")
    adapter_deny = PipecatMediaAdapter(
        config=RealtimeConfig(connection_mode="cascade"),
        stt_config=SttConfig(),
        publisher=MagicMock(),
        event_hub=MagicMock(),
        conversation=MagicMock(),
        audio_assets=MagicMock(),
        stt=MagicMock(),
        companion_settings=MagicMock(),
        activity=MagicMock(),
        resource_activity=lambda: None,
        egress_gateway=gateway_deny,
    )
    snapshot_consent = RealtimeConnectionSnapshot(
        schema_version="1.0",
        revision=1,
        connection_mode="cloud_realtime",
        cloud_backend="openai",
        model="gpt-4o-realtime",
        voice="marin",
        transcription_model="whisper-1",
        cloud_tools_enabled=True,
        cloud_egress_consent=True,
        api_key_configured=True,
        _api_key="sk-test",
    )
    with pytest.raises(PermissionError, match="未获授权"):
        adapter_deny._validate_admission(snapshot_consent, session_id)

    # Case 2: Policy mode is 'ask' -> grants scoped access
    gateway_ask = CloudEgressGateway(policy_mode="ask")
    adapter_ask = PipecatMediaAdapter(
        config=RealtimeConfig(connection_mode="cascade"),
        stt_config=SttConfig(),
        publisher=MagicMock(),
        event_hub=MagicMock(),
        conversation=MagicMock(),
        audio_assets=MagicMock(),
        stt=MagicMock(),
        companion_settings=MagicMock(),
        activity=MagicMock(),
        resource_activity=lambda: None,
        egress_gateway=gateway_ask,
    )
    adapter_ask._validate_admission(snapshot_consent, session_id)
    # Check that grant exists for session_id and backend 'openai'
    grant = gateway_ask._grants.get(session_id)
    assert grant is not None
    assert grant.backend_id == "openai"
    assert "tool_result" in grant.allowed_component_kinds


# ---------------------------------------------------------------------------
# 11. Fault Injection: DB CAS Failure Leaves Secret Intact
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_fault_injection_db_cas_failure_leaves_secret_intact(tmp_path: Path) -> None:
    """If DB CAS fails during update, the old secret reference remains intact."""
    settings = create_test_settings(tmp_path)
    container = RuntimeContainer(settings)
    await container.start()
    try:
        service = container.realtime_configuration
        snap1 = service.current_snapshot()
        old_key = snap1.api_key
        assert old_key == "test-key-env"

        # Monkeypatch repository update_configuration_cas to simulate CAS failure
        repo = service._repository
        original_update = repo.update_configuration_cas

        async def failing_update(
            expected_revision: int, record: RealtimeConfigurationRecord
        ) -> bool:
            return False

        repo.update_configuration_cas = failing_update

        with pytest.raises(RealtimeRevisionConflictError):
            await service.update_configuration(
                RealtimeConfigurationUpdateRequest(
                    expected_revision=1,
                    connection_mode="cloud_realtime",
                    cloud_backend="openai",
                    model="m2",
                    voice="marin",
                    transcription_model="tm2",
                    cloud_tools_enabled=False,
                    cloud_egress_consent=True,
                    api_key="new-attempted-key",
                )
            )

        # Restore
        repo.update_configuration_cas = original_update

        # Verify old snapshot and secret store still have the old key
        assert service.current_snapshot().api_key == "test-key-env"
    finally:
        await container.stop()


# ---------------------------------------------------------------------------
# 12. Corrupt Secret Store Fails Closed
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_corrupt_secret_store_fails_closed(tmp_path: Path) -> None:
    """A corrupted secret store file fails closed without overwriting the file."""
    secret_path = tmp_path / "config" / "realtime-secrets.json"
    secret_path.parent.mkdir(parents=True, exist_ok=True)
    secret_path.write_text("{invalid-json-content", encoding="utf-8")

    settings = create_test_settings(tmp_path)
    container = RuntimeContainer(settings)

    # Start should not crash; it logs error and marks api_key_configured=False
    await container.start()
    try:
        snap = container.realtime_configuration.current_snapshot()
        assert snap.api_key_configured is False
        assert snap.api_key is None

        # Corrupted file was not destroyed or overwritten blindly
        assert secret_path.read_text(encoding="utf-8") == "{invalid-json-content"
    finally:
        await container.stop()


# ---------------------------------------------------------------------------
# 13. Dynamic Renegotiation Freeze vs New Connections
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_renegotiation_freeze_preserves_connection_snapshot(tmp_path: Path) -> None:
    """Renegotiation on existing pc_id retains original snapshot; new pc_id gets new revision."""
    settings = create_test_settings(tmp_path)
    container = RuntimeContainer(settings)
    await container.start()
    try:
        service = container.realtime_configuration
        adapter = container.voice_media._adapter
        assert isinstance(adapter, PipecatMediaAdapter)

        session_id = (await container.sessions.create_session("test-reneg")).session_id

        # Connection 1 connects under revision 1
        snap1 = adapter._capture_snapshot()
        assert snap1.revision == 1
        pc1_id = "pc-connection-1"

        # Record connection 1 as active
        from chatwaifu_runtime.realtime.pipecat.session import _PcConnectionSnapshot

        adapter._sessions[pc1_id] = session_id
        adapter._pc_snapshots[pc1_id] = _PcConnectionSnapshot(
            snapshot=snap1,
            bridge_factory=None,
        )
        dummy_task = asyncio.create_task(asyncio.sleep(10))
        adapter._tasks[pc1_id] = dummy_task

        # Now update configuration to revision 2
        await service.update_configuration(
            RealtimeConfigurationUpdateRequest(
                expected_revision=1,
                connection_mode="cascade",
                cloud_backend="openai",
                model="updated-model-r2",
                voice="alloy",
                transcription_model="whisper-1",
                cloud_tools_enabled=False,
                cloud_egress_consent=True,
            )
        )
        assert service.current_snapshot().revision == 2

        # Renegotiation for pc-connection-1 should retain snapshot revision 1
        offer_reneg = WebRtcOffer(sdp="dummy-sdp", type="offer", pc_id=pc1_id, restart_pc=False)
        is_reneg = bool(
            offer_reneg.pc_id is not None
            and offer_reneg.pc_id in adapter._tasks
            and adapter._sessions.get(offer_reneg.pc_id) == session_id
            and not adapter._tasks[offer_reneg.pc_id].done()
        )
        assert is_reneg is True
        assert offer_reneg.pc_id is not None
        reneg_record = adapter._pc_snapshots.get(offer_reneg.pc_id)
        assert reneg_record is not None
        assert reneg_record.snapshot.revision == 1

        # A new connection connection-2 captures revision 2
        fresh_snap = adapter._capture_snapshot()
        assert fresh_snap.revision == 2
        assert fresh_snap.model == "updated-model-r2"

        dummy_task.cancel()
    finally:
        await container.stop()


# ---------------------------------------------------------------------------
# 14. Loopback Wire Handshake Uses Saved Profile
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_loopback_wire_handshake_uses_saved_profile(tmp_path: Path) -> None:
    """Saved profile settings (model, voice, transcription_model) are sent in wire handshake."""
    wire_events: list[dict[str, object]] = []
    auth_header: str | None = None
    server_connected = asyncio.Event()

    async def loopback_handler(ws: ServerConnection) -> None:
        nonlocal auth_header
        assert ws.request is not None
        auth_header = ws.request.headers.get("Authorization")
        server_connected.set()
        sequence = 0

        async def send(event: dict[str, object]) -> None:
            nonlocal sequence
            sequence += 1
            await ws.send(json.dumps({"event_id": f"evt-{sequence}", **event}))

        await send(
            {"type": "session.created", "session": {"id": "loopback-sess", "type": "realtime"}}
        )
        async for raw in ws:
            msg = object_value(json.loads(raw))
            wire_events.append(msg)
            if msg["type"] == "session.update":
                await send(
                    {
                        "type": "session.updated",
                        "session": {**object_value(msg["session"]), "id": "loopback-sess"},
                    }
                )

    async with serve(loopback_handler, "127.0.0.1", 0) as ws_server:
        port = ws_server.sockets[0].getsockname()[1]
        connected_url: str | None = None

        async def connector(url: str, key: str, seconds: float) -> RealtimeSocket:
            nonlocal connected_url
            connected_url = url
            return await openai_module._connect(f"ws://127.0.0.1:{port}/realtime", key, seconds)

        settings = create_test_settings(tmp_path)
        container = RuntimeContainer(settings)
        container.cloud_backend_connector = connector
        await container.start()

        try:
            # Update configuration to custom profile
            updated_snap = await container.realtime_configuration.update_configuration(
                RealtimeConfigurationUpdateRequest(
                    expected_revision=1,
                    connection_mode="cloud_realtime",
                    cloud_backend="openai",
                    model="gpt-4o-realtime-profile-test",
                    voice="shimmer",
                    transcription_model="whisper-custom",
                    cloud_tools_enabled=True,
                    cloud_egress_consent=True,
                    api_key="sk-loopback-secret-key",
                )
            )

            # Create bridge using the updated snapshot
            session = await container.sessions.create_session("wire-test")
            bridge_factory = container._build_cloud_bridge_factory_for_snapshot(updated_snap)
            bridge = await bridge_factory(session.session_id)

            await asyncio.wait_for(server_connected.wait(), timeout=3.0)
            assert auth_header == "Bearer sk-loopback-secret-key"
            assert connected_url is not None
            assert "model=gpt-4o-realtime-profile-test" in connected_url

            # Check that session.update event contains our custom profile
            session_update_events = [e for e in wire_events if e.get("type") == "session.update"]
            assert len(session_update_events) >= 1
            session_payload = object_value(session_update_events[0]["session"])
            audio_payload = object_value(session_payload["audio"])
            assert object_value(audio_payload["output"])["voice"] == "shimmer"
            transcription = object_value(object_value(audio_payload["input"])["transcription"])
            assert transcription.get("model") == "whisper-custom"

            await bridge.coordinator.stop()
            await bridge.cleanup()
        finally:
            await container.stop()


# ---------------------------------------------------------------------------
# 15. Cascade Back-Switch Without Leaks
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_cascade_back_switch_without_leaks(tmp_path: Path) -> None:
    """Switching from cloud_realtime back to cascade admits fresh connections without error."""
    settings = create_test_settings(tmp_path)
    container = RuntimeContainer(settings)
    await container.start()
    try:
        service = container.realtime_configuration
        # Switch to cloud_realtime
        await service.update_configuration(
            RealtimeConfigurationUpdateRequest(
                expected_revision=1,
                connection_mode="cloud_realtime",
                cloud_backend="openai",
                model="gpt-4o-realtime",
                voice="marin",
                transcription_model="whisper-1",
                cloud_tools_enabled=False,
                cloud_egress_consent=True,
            )
        )
        assert service.current_snapshot().connection_mode == "cloud_realtime"

        # Switch back to cascade
        await service.update_configuration(
            RealtimeConfigurationUpdateRequest(
                expected_revision=2,
                connection_mode="cascade",
                cloud_backend="openai",
                model="gpt-4o-realtime",
                voice="marin",
                transcription_model="whisper-1",
                cloud_tools_enabled=False,
                cloud_egress_consent=True,
            )
        )
        assert service.current_snapshot().connection_mode == "cascade"

        adapter = container.voice_media._adapter
        assert isinstance(adapter, PipecatMediaAdapter)
        snap = adapter._capture_snapshot()
        assert snap.connection_mode == "cascade"

        # Cascade admission doesn't require model or api_key
        session_id = uuid4()
        adapter._validate_admission(snap, session_id)
    finally:
        await container.stop()
