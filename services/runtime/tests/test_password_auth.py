"""Password exchange preserves the existing authenticated Runtime perimeter."""

import json
import stat
from pathlib import Path

import pytest
from chatwaifu_runtime.config.settings import Settings
from chatwaifu_runtime.main import create_app
from chatwaifu_runtime.persistence.atomic_secret_store import AtomicSecretStore, SecretStoreError
from chatwaifu_runtime.security.password import (
    SESSION_TTL_SECONDS,
    PasswordAuthError,
    PasswordUpdate,
    RuntimePasswordAuth,
)
from fastapi.testclient import TestClient
from pydantic import SecretStr


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings.model_validate(
        {
            "config_dir": tmp_path / "config",
            "data_dir": tmp_path,
            "storage": {"database_path": tmp_path / "runtime.db"},
            "llm": {"provider": "demo", "demo_chunk_delay_ms": 0},
            "tts": {"provider": "fake"},
        }
    )


def test_password_setup_login_change_and_restart(settings: Settings) -> None:
    app = create_app(settings)
    owner = {"Authorization": f"Bearer {app.state.container.capability_token}"}
    password = " 初始密码 with spaces "
    new_password = " 新密码 with spaces 123 "
    with TestClient(app) as client:
        assert client.put("/v1/runtime/password", json={}).status_code == 401
        assert client.get("/v1/runtime/password", headers=owner).json() == {
            "schema_version": 1,
            "configured": False,
            "revision": 0,
        }
        setup = client.put(
            "/v1/runtime/password",
            headers=owner,
            json={"new_password": password, "expected_revision": 0},
        )
        assert setup.status_code == 200, setup.text
        token = setup.json()["token"]
        assert token != password and len(token) >= 32
        session = {"Authorization": f"Bearer {token}"}
        assert client.get("/v1/characters", headers=session).status_code == 200
        assert client.get("/v1/characters", headers=owner).status_code == 200
        assert (
            client.post(
                "/v1/runtime/password/login", json={"password": password.strip()}
            ).status_code
            == 401
        )
        login = client.post("/v1/runtime/password/login", json={"password": password})
        assert login.status_code == 200
        assert (
            client.get(
                "/v1/characters", headers={"Authorization": f"Bearer {login.json()['token']}"}
            ).status_code
            == 200
        )
        # A password session uses the ordinary session-scoped WS ticket path.
        conversation = client.post("/v1/sessions", json={}, headers=session).json()
        ticket = client.post(
            "/v1/runtime/ws-ticket",
            headers=session,
            json={"purpose": "events", "session_id": conversation["session_id"]},
        )
        assert ticket.status_code == 200
        assert (
            client.post("/v1/runtime/ws-ticket?purpose=admin_events", headers=session).status_code
            == 403
        )
        wrong = client.put(
            "/v1/runtime/password",
            headers=session,
            json={
                "new_password": new_password,
                "current_password": "wrong password",
                "expected_revision": 1,
            },
        )
        assert wrong.status_code == 401
        stale = client.put(
            "/v1/runtime/password",
            headers=owner,
            json={
                "new_password": new_password,
                "current_password": password,
                "expected_revision": 0,
            },
        )
        assert stale.status_code == 409
        changed = client.put(
            "/v1/runtime/password",
            headers=session,
            json={
                "new_password": new_password,
                "current_password": password,
                "expected_revision": 1,
            },
        )
        assert changed.status_code == 200, changed.text
        assert client.get("/v1/characters", headers=session).status_code == 401
        assert (
            client.get(
                "/v1/characters", headers={"Authorization": f"Bearer {login.json()['token']}"}
            ).status_code
            == 401
        )
        assert not app.state.container.ws_ticket_store._tickets
        assert (
            client.post("/v1/runtime/password/login", json={"password": password}).status_code
            == 401
        )

    path = settings.config_dir / "runtime-password.json"
    raw = path.read_text()
    assert password not in raw and new_password not in raw and token not in raw
    assert json.loads(raw)
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    restarted = create_app(settings)
    with TestClient(restarted) as client:
        assert client.get("/v1/characters", headers=session).status_code == 401
        assert (
            client.post("/v1/runtime/password/login", json={"password": new_password}).status_code
            == 200
        )


def test_login_perimeter_limits_and_secret_redaction(settings: Settings) -> None:
    app = create_app(settings)
    owner = {"Authorization": f"Bearer {app.state.container.capability_token}"}
    with TestClient(app) as client:
        client.put(
            "/v1/runtime/password",
            headers=owner,
            json={"new_password": "test password 123", "expected_revision": 0},
        )
        assert (
            client.post(
                "/v1/runtime/password/login",
                json={"password": "test password 123"},
                headers={"Origin": "https://evil.example"},
            ).status_code
            == 403
        )
        assert (
            client.post(
                "/v1/runtime/password/login",
                json={"password": "test password 123"},
                headers={"Host": "evil.example"},
            ).status_code
            == 403
        )
        invalid = client.post("/v1/runtime/password/login", json={"password": "SECRET" * 100})
        assert invalid.status_code == 422 and "SECRET" not in invalid.text
        oversized = client.post(
            "/v1/runtime/password/login",
            content='{"password":"' + "SECRET" * 1000 + '"}',
            headers={"Content-Type": "application/json"},
        )
        assert oversized.status_code == 413 and "SECRET" not in oversized.text
        invalid = client.put(
            "/v1/runtime/password",
            headers=owner,
            json={"new_password": "tiny", "expected_revision": 1},
        )
        assert invalid.status_code == 422 and "tiny" not in invalid.text
        for _ in range(5):
            assert (
                client.post(
                    "/v1/runtime/password/login", json={"password": "wrong password"}
                ).status_code
                == 401
            )
        limited = client.post("/v1/runtime/password/login", json={"password": "test password 123"})
        assert limited.status_code == 429 and int(limited.headers["Retry-After"]) > 0
        assert client.get("/v1/characters", headers=owner).status_code == 200


def test_disabled_guard_does_not_allow_anonymous_password_setup(settings: Settings) -> None:
    app = create_app(
        settings.model_copy(
            update={"security": settings.security.model_copy(update={"auth_enabled": False})}
        )
    )
    with TestClient(app) as client:
        assert (
            client.put(
                "/v1/runtime/password",
                json={"new_password": "test password 123", "expected_revision": 0},
            ).status_code
            == 401
        )


def test_password_session_expiry_and_attempt_window(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    clock = [1000.0]
    monkeypatch.setattr("chatwaifu_runtime.security.password.time.monotonic", lambda: clock[0])
    auth = RuntimePasswordAuth(tmp_path)
    session = auth.update(
        PasswordUpdate(new_password=SecretStr("test password 123"), expected_revision=0)
    )
    assert auth.is_token_valid(session.token)
    for _ in range(5):
        with pytest.raises(PasswordAuthError, match="密码不正确"):
            auth.login("wrong")
    with pytest.raises(PasswordAuthError) as error:
        auth.login("test password 123")
    assert error.value.status == 429
    clock[0] += 60
    assert auth.login("test password 123")
    clock[0] += SESSION_TTL_SECONDS
    assert not auth.is_token_valid(session.token)


def test_corrupt_password_storage_fails_closed(tmp_path: Path) -> None:
    path = tmp_path / "runtime-password.json"
    path.write_text('{"password":"invalid record"}')
    with pytest.raises(SecretStoreError):
        RuntimePasswordAuth(tmp_path)
    assert path.read_text() == '{"password":"invalid record"}'


def test_invalid_unicode_password_does_not_write_state(tmp_path: Path) -> None:
    auth = RuntimePasswordAuth(tmp_path)
    with pytest.raises(PasswordAuthError) as error:
        auth.update(
            PasswordUpdate(new_password=SecretStr("test password 123\ud800"), expected_revision=0)
        )
    assert error.value.status == 422
    assert not auth.status().configured
    assert not (tmp_path / "runtime-password.json").exists()


def test_failed_password_write_preserves_previous_login(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    auth = RuntimePasswordAuth(tmp_path)
    session = auth.update(
        PasswordUpdate(new_password=SecretStr("test password 123"), expected_revision=0)
    )

    def fail_write(_self: AtomicSecretStore, _name: str, _value: str | None) -> None:
        raise OSError("disk unavailable")

    monkeypatch.setattr(AtomicSecretStore, "set", fail_write)
    with pytest.raises(OSError):
        auth.update(
            PasswordUpdate(
                new_password=SecretStr("new password 123"),
                current_password=SecretStr("test password 123"),
                expected_revision=1,
            )
        )
    assert auth.status().revision == 1
    assert auth.is_token_valid(session.token)
    assert auth.login("test password 123")
