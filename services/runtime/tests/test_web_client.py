"""Public product documents never grant access to private Runtime data."""

from pathlib import Path

import pytest
from chatwaifu_runtime.config.settings import Settings
from chatwaifu_runtime.main import create_app
from fastapi.testclient import TestClient


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    web = tmp_path / "web"
    (web / "assets").mkdir(parents=True)
    (web / "index.html").write_text("<!doctype html><title>Login shell</title>")
    (web / "assets/app.js").write_text("console.log('public app')")
    (tmp_path / "private.txt").write_text("PRIVATE-CONTENT")
    return Settings.model_validate(
        {
            "config_dir": tmp_path / "config",
            "data_dir": tmp_path / "data",
            "storage": {"database_path": tmp_path / "runtime.db"},
            "runtime": {"web_client_dir": web},
            "llm": {"provider": "demo", "demo_chunk_delay_ms": 0},
            "tts": {"provider": "fake"},
        }
    )


def test_pages_assets_and_private_api_boundary(settings: Settings) -> None:
    with TestClient(create_app(settings)) as client:
        for path in ["/", "/knowledge", "/settings/knowledge", "/settings/connection"]:
            response = client.get(path)
            assert response.status_code == 200
            assert "Login shell" in response.text
            assert response.headers["Cache-Control"] == "no-store"
            assert "frame-ancestors 'none'" in response.headers["Content-Security-Policy"]
            assert client.head(path).status_code == 200
        assert client.get("/web-client/assets/app.js").status_code == 200
        assert client.get("/v1/characters").status_code == 401
        assert client.get("/v1/runtime/password").status_code == 401
        assert client.post("/").status_code == 401
        for path in ["/web-client/../private.txt", "/web-client/%2e%2e%2fprivate.txt"]:
            response = client.get(path)
            assert response.status_code in [401, 404]
            assert "PRIVATE-CONTENT" not in response.text


def test_web_documents_keep_host_and_origin_checks(settings: Settings) -> None:
    with TestClient(create_app(settings)) as client:
        assert (
            client.get("/settings/knowledge", headers={"Host": "evil.example"}).status_code == 403
        )
        assert (
            client.get("/knowledge", headers={"Origin": "https://evil.example"}).status_code == 403
        )


def test_default_runtime_keeps_existing_root_authentication(settings: Settings) -> None:
    disabled = settings.model_copy(
        update={"runtime": settings.runtime.model_copy(update={"web_client_dir": None})}
    )
    with TestClient(create_app(disabled)) as client:
        assert client.get("/").status_code == 401
        assert client.get("/web-client/assets/app.js").status_code == 401


def test_web_directory_is_operator_only(settings: Settings) -> None:
    runtime = settings.public_dict()["runtime"]
    assert isinstance(runtime, dict)
    assert "web_client_dir" not in runtime


def test_missing_web_build_fails_before_serving(settings: Settings, tmp_path: Path) -> None:
    missing = settings.model_copy(
        update={
            "runtime": settings.runtime.model_copy(update={"web_client_dir": tmp_path / "missing"})
        }
    )
    with pytest.raises(ValueError, match="runtime_web_client_build_missing"):
        create_app(missing)
