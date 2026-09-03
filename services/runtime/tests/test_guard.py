"""Tests for LocalClientGuard security perimeter and authentication."""

import asyncio
from pathlib import Path

import pytest
from chatwaifu_runtime.api.guard import WebSocketTicketStore
from chatwaifu_runtime.config.settings import Settings, StorageConfig
from chatwaifu_runtime.main import create_app
from fastapi.testclient import TestClient
from pydantic import ValidationError
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

        # When an allowed origin is provided, CORS headers must be attached even on 401
        cors_response = client.get(
            "/v1/characters",
            headers={"Origin": "http://127.0.0.1:5173"},
        )
        assert cors_response.status_code == 401
        assert cors_response.headers.get("access-control-allow-origin") == "http://127.0.0.1:5173"
        assert cors_response.headers.get("vary") == "Origin"


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
        session_resp = client.post(
            "/v1/sessions",
            json={},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert session_resp.status_code == 201
        session_id = session_resp.json()["session_id"]
        # 1. WS connection without ticket or token is rejected
        with pytest.raises(WebSocketDisconnect):
            with client.websocket_connect("/v1/events"):
                pass

        # 2. Events ticket without session_id is rejected with 400
        bad_req = client.post(
            "/v1/runtime/ws-ticket?purpose=events",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert bad_req.status_code == 400

        # 3. Issue ticket with session_id
        ticket_resp = client.post(
            f"/v1/runtime/ws-ticket?purpose=events&session_id={session_id}",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert ticket_resp.status_code == 200
        ticket = ticket_resp.json()["ticket"]
        assert ticket

        # 4. Connect with ticket and matching session_id succeeds
        ws_url = f"/v1/events?session_id={session_id}&ticket={ticket}"
        with client.websocket_connect(ws_url) as websocket:
            websocket.send_json({"type": "ping"})

        # 5. Replaying consumed ticket fails
        with pytest.raises(WebSocketDisconnect):
            with client.websocket_connect(f"/v1/events?session_id={session_id}&ticket={ticket}"):
                pass


@pytest.mark.asyncio
async def test_ticket_store_expiration() -> None:
    store = WebSocketTicketStore()
    ticket = await store.create_ticket(ttl_seconds=0.01)
    await asyncio.sleep(0.02)
    consumed = await store.consume_ticket(ticket)
    assert consumed is None


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

        # 1. Issue ticket for events with Origin and session binding
        events_resp = client.post(
            f"/v1/runtime/ws-ticket?purpose=events&session_id={session_id}",
            headers={"Authorization": f"Bearer {token}", "Origin": "http://localhost:5173"},
        )
        assert events_resp.status_code == 200
        events_ticket = events_resp.json()["ticket"]
        assert events_resp.json()["purpose"] == "events"
        assert events_resp.json()["session_id"] == session_id

        # Events ticket on /v1/events with matching origin and matching session succeeds
        with client.websocket_connect(
            f"/v1/events?session_id={session_id}&ticket={events_ticket}",
            headers={"Origin": "http://localhost:5173"},
        ) as ws:
            ws.send_json({"type": "ping"})

        # 2. Events ticket without session_id param is rejected
        events_resp_no_session = client.post(
            f"/v1/runtime/ws-ticket?purpose=events&session_id={session_id}",
            headers={"Authorization": f"Bearer {token}"},
        )
        ticket_no_session = events_resp_no_session.json()["ticket"]
        with pytest.raises(WebSocketDisconnect):
            with client.websocket_connect(f"/v1/events?ticket={ticket_no_session}"):
                pass

        # 3. Events ticket with mismatched session_id is rejected
        events_resp_mismatch = client.post(
            f"/v1/runtime/ws-ticket?purpose=events&session_id={session_id}",
            headers={"Authorization": f"Bearer {token}"},
        )
        ticket_mismatch = events_resp_mismatch.json()["ticket"]
        with pytest.raises(WebSocketDisconnect):
            with client.websocket_connect(
                f"/v1/events?session_id=different-session-uuid&ticket={ticket_mismatch}"
            ):
                pass

        # 4. Events ticket cannot connect to /v1/audio/stream
        events_resp2 = client.post(
            f"/v1/runtime/ws-ticket?purpose=events&session_id={session_id}",
            headers={"Authorization": f"Bearer {token}"},
        )
        events_ticket2 = events_resp2.json()["ticket"]
        with pytest.raises(WebSocketDisconnect):
            with client.websocket_connect(
                f"/v1/audio/stream?session_id={session_id}&ticket={events_ticket2}"
            ):
                pass

        # 5. Audio ticket requires session_id
        audio_resp_no_session = client.get(
            "/v1/runtime/ws-ticket?purpose=audio",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert audio_resp_no_session.status_code == 400

        # Issue ticket for audio with session_id via GET
        audio_resp = client.get(
            f"/v1/runtime/ws-ticket?purpose=audio&session_id={session_id}",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert audio_resp.status_code == 200
        audio_ticket = audio_resp.json()["ticket"]
        assert audio_resp.json()["purpose"] == "audio"

        # Audio ticket cannot connect to /v1/events
        with pytest.raises(WebSocketDisconnect):
            ws_url = f"/v1/events?session_id={session_id}&ticket={audio_ticket}"
            with client.websocket_connect(ws_url):
                pass

        # Re-issue audio ticket and connect to /v1/audio/stream succeeds
        audio_resp2 = client.post(
            f"/v1/runtime/ws-ticket?purpose=audio&session_id={session_id}",
            headers={"Authorization": f"Bearer {token}"},
        )
        audio_ticket2 = audio_resp2.json()["ticket"]
        with client.websocket_connect(
            f"/v1/audio/stream?session_id={session_id}&ticket={audio_ticket2}"
        ):
            pass

        # Audio ticket cannot connect to different session_id on /v1/audio/stream
        audio_resp_mismatch = client.post(
            f"/v1/runtime/ws-ticket?purpose=audio&session_id={session_id}",
            headers={"Authorization": f"Bearer {token}"},
        )
        audio_ticket_mismatch = audio_resp_mismatch.json()["ticket"]
        with pytest.raises(WebSocketDisconnect):
            with client.websocket_connect(
                f"/v1/audio/stream?session_id=different-session&ticket={audio_ticket_mismatch}"
            ):
                pass

        # Duplicate query parameters are rejected immediately (parser differential defense)
        dup_ticket_resp = client.post(
            f"/v1/runtime/ws-ticket?purpose=events&session_id={session_id}",
            headers={"Authorization": f"Bearer {token}"},
        )
        dup_ticket = dup_ticket_resp.json()["ticket"]
        # Duplicate session_id: A then B
        with pytest.raises(WebSocketDisconnect):
            with client.websocket_connect(
                f"/v1/events?session_id={session_id}&session_id=different-session&ticket={dup_ticket}"
            ):
                pass

        # Re-issue ticket for second test
        dup_ticket_resp2 = client.post(
            f"/v1/runtime/ws-ticket?purpose=events&session_id={session_id}",
            headers={"Authorization": f"Bearer {token}"},
        )
        dup_ticket2 = dup_ticket_resp2.json()["ticket"]
        # Duplicate session_id: B then A
        with pytest.raises(WebSocketDisconnect):
            with client.websocket_connect(
                f"/v1/events?session_id=different-session&session_id={session_id}&ticket={dup_ticket2}"
            ):
                pass

        # Duplicate ticket parameter
        with pytest.raises(WebSocketDisconnect):
            with client.websocket_connect(
                f"/v1/events?session_id={session_id}&ticket={dup_ticket2}&ticket=another-ticket"
            ):
                pass

        # Audio stream duplicate session_id
        audio_dup_resp = client.post(
            f"/v1/runtime/ws-ticket?purpose=audio&session_id={session_id}",
            headers={"Authorization": f"Bearer {token}"},
        )
        audio_dup_ticket = audio_dup_resp.json()["ticket"]
        with pytest.raises(WebSocketDisconnect):
            with client.websocket_connect(
                f"/v1/audio/stream?session_id={session_id}&session_id=different-session&ticket={audio_dup_ticket}"
            ):
                pass

        # 6. Ticket with bound origin rejected when origin differs
        bound_resp = client.post(
            f"/v1/runtime/ws-ticket?purpose=events&session_id={session_id}",
            headers={"Authorization": f"Bearer {token}", "Origin": "http://localhost:5173"},
        )
        bound_ticket = bound_resp.json()["ticket"]
        with pytest.raises(WebSocketDisconnect):
            with client.websocket_connect(
                f"/v1/events?session_id={session_id}&ticket={bound_ticket}",
                headers={"Origin": "http://custom.local:3000"},
            ):
                pass

        # 7. admin_events: capability token rejected with 403
        admin_req_cap = client.post(
            "/v1/runtime/ws-ticket?purpose=admin_events",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert admin_req_cap.status_code == 403


def test_admin_events_ticket_with_admin_token(tmp_path: Path) -> None:
    settings = Settings.model_validate(
        {
            "config_dir": tmp_path / "config",
            "data_dir": tmp_path,
            "storage": StorageConfig(database_path=tmp_path / "runtime.db"),
            "llm": {"provider": "demo", "demo_chunk_delay_ms": 0},
            "tts": {"provider": "fake"},
            "security": {
                "auth_enabled": True,
                "admin_token": "my-admin-token-xyz",
                "allowed_hosts": ["custom.local"],
                "allowed_origins": ["http://custom.local:3000"],
            },
        }
    )
    app = create_app(settings)
    with TestClient(app) as client:
        # Admin token requests admin_events ticket
        resp = client.post(
            "/v1/runtime/ws-ticket?purpose=admin_events",
            headers={"Authorization": "Bearer my-admin-token-xyz"},
        )
        assert resp.status_code == 200
        admin_ticket = resp.json()["ticket"]

        # admin_events ticket can connect to /v1/events without session_id
        with client.websocket_connect(f"/v1/events?ticket={admin_ticket}") as ws:
            ws.send_json({"type": "ping"})


def test_explicit_capability_token_used_when_configured(tmp_path: Path) -> None:
    settings = Settings.model_validate(
        {
            "config_dir": tmp_path / "config",
            "data_dir": tmp_path,
            "storage": StorageConfig(database_path=tmp_path / "runtime.db"),
            "llm": {"provider": "demo", "demo_chunk_delay_ms": 0},
            "tts": {"provider": "fake"},
            "security": {
                "auth_enabled": True,
                "capability_token": "explicit-preset-capability-token-32b",
                "allowed_hosts": ["custom.local"],
                "allowed_origins": ["http://custom.local:3000"],
            },
        }
    )
    app = create_app(settings)
    assert app.state.container.capability_token == "explicit-preset-capability-token-32b"
    with TestClient(app) as client:
        response = client.get(
            "/v1/characters",
            headers={"Authorization": "Bearer explicit-preset-capability-token-32b"},
        )
        assert response.status_code == 200


def test_capability_token_rejects_too_short(tmp_path: Path) -> None:
    with pytest.raises(ValidationError):
        Settings.model_validate(
            {
                "config_dir": tmp_path / "config",
                "data_dir": tmp_path,
                "storage": StorageConfig(database_path=tmp_path / "runtime.db"),
                "security": {
                    "capability_token": "short-token",
                },
            }
        )


def test_vulnerable_nltk_apis_not_referenced() -> None:
    """Ensure vulnerable NLTK pathsec bypass APIs (PYSEC-2026-3740) are not referenced."""
    import ast

    banned = {"TransitionParser", "AveragedPerceptron", "save_to_json", "save_maxent_params"}
    runtime_root = Path(__file__).resolve().parent.parent / "src" / "chatwaifu_runtime"
    for py_path in runtime_root.rglob("*.py"):
        tree = ast.parse(py_path.read_text(encoding="utf-8"), filename=str(py_path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Name) and node.id in banned:
                pytest.fail(f"Banned NLTK API '{node.id}' referenced in {py_path}")
            if isinstance(node, ast.Attribute) and node.attr in banned:
                pytest.fail(f"Banned NLTK API '{node.attr}' referenced in {py_path}")
