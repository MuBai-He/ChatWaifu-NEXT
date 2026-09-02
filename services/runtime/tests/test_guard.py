"""Tests for LocalClientGuard security perimeter and authentication."""

import asyncio
from pathlib import Path

import pytest
from chatwaifu_runtime.api.guard import WebSocketTicketStore
from chatwaifu_runtime.config.settings import Settings, StorageConfig
from chatwaifu_runtime.main import create_app
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect


@pytest.fixture
def guard_settings(tmp_path: Path) -> Settings:
    return Settings.model_validate(
        {
            "config_dir": tmp_path / "config",
            "data_dir": tmp_path,
            "storage": StorageConfig(database_path=tmp_path / "runtime.db"),
            "llm": {"provider": "demo", "demo_chunk_delay_ms": 0},
            "tts": {"provider": "fake"},
            "security": {
                "auth_enabled": True,
                "allowed_hosts": ["custom.local"],
                "allowed_origins": ["http://custom.local:3000"],
            },
        }
    )


def test_unauthenticated_request_rejected(guard_settings: Settings) -> None:
    app = create_app(guard_settings)
    with TestClient(app) as client:
        response = client.get("/v1/characters")
        assert response.status_code == 401
        assert response.json()["detail"] == "Unauthorized: invalid or missing token"


def test_invalid_token_rejected(guard_settings: Settings) -> None:
    app = create_app(guard_settings)
    with TestClient(app) as client:
        response = client.get("/v1/characters", headers={"Authorization": "Bearer invalid_secret"})
        assert response.status_code == 401


def test_valid_token_accepted(guard_settings: Settings) -> None:
    app = create_app(guard_settings)
    token = app.state.container.capability_token
    with TestClient(app) as client:
        response = client.get("/v1/characters", headers={"Authorization": f"Bearer {token}"})
        assert response.status_code == 200


def test_exempt_endpoints_accessible_without_auth(guard_settings: Settings) -> None:
    app = create_app(guard_settings)
    with TestClient(app) as client:
        health_resp = client.get("/v1/runtime/health")
        assert health_resp.status_code == 200
        assert health_resp.json()["status"] == "ok"

        openapi_resp = client.get("/openapi.json")
        assert openapi_resp.status_code == 200


def test_disallowed_host_rejected(guard_settings: Settings) -> None:
    app = create_app(guard_settings)
    token = app.state.container.capability_token
    with TestClient(app) as client:
        response = client.get(
            "/v1/runtime/health",
            headers={"Host": "attacker-dns-rebind.com", "Authorization": f"Bearer {token}"},
        )
        assert response.status_code == 403
        assert "invalid host" in response.json()["detail"]


def test_allowed_hosts_accepted(guard_settings: Settings) -> None:
    app = create_app(guard_settings)
    token = app.state.container.capability_token
    with TestClient(app) as client:
        for host in ["localhost:8765", "127.0.0.1:8765", "[::1]:8765", "custom.local:8765"]:
            response = client.get(
                "/v1/runtime/health",
                headers={"Host": host, "Authorization": f"Bearer {token}"},
            )
            assert response.status_code == 200


def test_disallowed_origin_rejected(guard_settings: Settings) -> None:
    app = create_app(guard_settings)
    token = app.state.container.capability_token
    with TestClient(app) as client:
        response = client.get(
            "/v1/runtime/health",
            headers={"Origin": "https://malicious-website.com", "Authorization": f"Bearer {token}"},
        )
        assert response.status_code == 403
        assert "untrusted origin" in response.json()["detail"]


def test_allowed_origins_accepted(guard_settings: Settings) -> None:
    app = create_app(guard_settings)
    token = app.state.container.capability_token
    with TestClient(app) as client:
        for origin in [
            "http://localhost:5173",
            "tauri://localhost",
            "http://tauri.localhost",
            "http://custom.local:3000",
        ]:
            response = client.get(
                "/v1/runtime/health",
                headers={"Origin": origin, "Authorization": f"Bearer {token}"},
            )
            assert response.status_code == 200


def test_cors_options_preflight_passes_through(guard_settings: Settings) -> None:
    app = create_app(guard_settings)
    with TestClient(app) as client:
        response = client.options(
            "/v1/characters",
            headers={
                "Origin": "http://localhost:5173",
                "Access-Control-Request-Method": "GET",
            },
        )
        assert response.status_code == 200


def test_ws_ticket_lifecycle_and_single_use(guard_settings: Settings) -> None:
    app = create_app(guard_settings)
    token = app.state.container.capability_token
    with TestClient(app) as client:
        # 1. WS connection without ticket or token is rejected
        with pytest.raises(WebSocketDisconnect):
            with client.websocket_connect("/v1/events"):
                pass

        # 2. Issue ticket
        ticket_resp = client.post(
            "/v1/runtime/ws-ticket", headers={"Authorization": f"Bearer {token}"}
        )
        assert ticket_resp.status_code == 200
        ticket = ticket_resp.json()["ticket"]
        assert ticket

        # 3. Connect with ticket succeeds
        with client.websocket_connect(f"/v1/events?ticket={ticket}") as websocket:
            websocket.send_json({"type": "ping"})

        # 4. Replaying consumed ticket fails
        with pytest.raises(WebSocketDisconnect):
            with client.websocket_connect(f"/v1/events?ticket={ticket}"):
                pass


@pytest.mark.asyncio
async def test_ticket_store_expiration() -> None:
    store = WebSocketTicketStore()
    ticket = await store.create_ticket(ttl_seconds=0.01)
    await asyncio.sleep(0.02)
    consumed = await store.consume_ticket(ticket)
    assert consumed is False


def test_mcp_endpoint_protected(guard_settings: Settings) -> None:
    app = create_app(guard_settings)
    token = app.state.container.capability_token
    with TestClient(app) as client:
        # Unauthenticated request to MCP mount
        unauth_resp = client.post("/", json={"jsonrpc": "2.0", "method": "ping", "id": 1})
        assert unauth_resp.status_code == 401

        # Authenticated request
        auth_resp = client.post(
            "/",
            json={"jsonrpc": "2.0", "method": "ping", "id": 1},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert auth_resp.status_code != 401


def test_capability_token_distinct_from_admin_token(tmp_path: Path) -> None:
    settings = Settings.model_validate(
        {
            "config_dir": tmp_path / "config",
            "data_dir": tmp_path,
            "storage": StorageConfig(database_path=tmp_path / "runtime.db"),
            "llm": {"provider": "demo", "demo_chunk_delay_ms": 0},
            "tts": {"provider": "fake"},
            "security": {
                "auth_enabled": True,
                "admin_token": "super-secret-admin-token-12345",
                "allowed_hosts": ["custom.local"],
                "allowed_origins": ["http://custom.local:3000"],
            },
        }
    )
    app = create_app(settings)
    cap_token = app.state.container.capability_token
    assert cap_token != "super-secret-admin-token-12345"
    assert len(cap_token) >= 32

    with TestClient(app) as client:
        # Both admin_token and ephemeral capability_token are valid
        admin_resp = client.get(
            "/v1/characters", headers={"Authorization": "Bearer super-secret-admin-token-12345"}
        )
        assert admin_resp.status_code == 200

        cap_resp = client.get("/v1/characters", headers={"Authorization": f"Bearer {cap_token}"})
        assert cap_resp.status_code == 200

        # Invalid token is still rejected
        bad_resp = client.get(
            "/v1/characters", headers={"Authorization": "Bearer not-a-valid-token"}
        )
        assert bad_resp.status_code == 401


def test_ws_ticket_purpose_isolation_and_origin_binding(guard_settings: Settings) -> None:
    app = create_app(guard_settings)
    token = app.state.container.capability_token
    with TestClient(app) as client:
        session_resp = client.post(
            "/v1/sessions",
            json={},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert session_resp.status_code == 201
        session_id = session_resp.json()["session_id"]

        # 1. Issue ticket for events with Origin binding
        events_resp = client.post(
            "/v1/runtime/ws-ticket?purpose=events",
            headers={"Authorization": f"Bearer {token}", "Origin": "http://localhost:5173"},
        )
        assert events_resp.status_code == 200
        events_ticket = events_resp.json()["ticket"]
        assert events_resp.json()["purpose"] == "events"

        # Events ticket on /v1/events with matching origin succeeds
        with client.websocket_connect(
            f"/v1/events?ticket={events_ticket}",
            headers={"Origin": "http://localhost:5173"},
        ) as ws:
            ws.send_json({"type": "ping"})

        # 2. Events ticket cannot connect to /v1/audio/stream
        events_resp2 = client.post(
            "/v1/runtime/ws-ticket?purpose=events",
            headers={"Authorization": f"Bearer {token}"},
        )
        events_ticket2 = events_resp2.json()["ticket"]
        with pytest.raises(WebSocketDisconnect):
            with client.websocket_connect(
                f"/v1/audio/stream?session_id={session_id}&ticket={events_ticket2}"
            ):
                pass

        # 3. Issue ticket for audio via GET
        audio_resp = client.get(
            "/v1/runtime/ws-ticket?purpose=audio",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert audio_resp.status_code == 200
        audio_ticket = audio_resp.json()["ticket"]
        assert audio_resp.json()["purpose"] == "audio"

        # Audio ticket cannot connect to /v1/events
        with pytest.raises(WebSocketDisconnect):
            with client.websocket_connect(f"/v1/events?ticket={audio_ticket}"):
                pass

        # Re-issue audio ticket and connect to /v1/audio/stream succeeds
        audio_resp2 = client.post(
            "/v1/runtime/ws-ticket?purpose=audio",
            headers={"Authorization": f"Bearer {token}"},
        )
        audio_ticket2 = audio_resp2.json()["ticket"]
        with client.websocket_connect(
            f"/v1/audio/stream?session_id={session_id}&ticket={audio_ticket2}"
        ):
            pass

        # 4. Ticket with bound origin rejected when origin differs
        bound_resp = client.post(
            "/v1/runtime/ws-ticket?purpose=events",
            headers={"Authorization": f"Bearer {token}", "Origin": "http://localhost:5173"},
        )
        bound_ticket = bound_resp.json()["ticket"]
        with pytest.raises(WebSocketDisconnect):
            with client.websocket_connect(
                f"/v1/events?ticket={bound_ticket}",
                headers={"Origin": "http://custom.local:3000"},
            ):
                pass
