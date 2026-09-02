"""Runtime test fixtures."""

from collections.abc import Iterator
from pathlib import Path

import pytest
from chatwaifu_runtime.config.settings import Settings, StorageConfig
from chatwaifu_runtime.main import create_app
from fastapi.testclient import TestClient


@pytest.fixture
def runtime_settings(tmp_path: Path) -> Settings:
    return Settings.model_validate(
        {
            "config_dir": tmp_path / "config",
            "data_dir": tmp_path,
            "storage": StorageConfig(database_path=tmp_path / "runtime.db"),
            "llm": {"provider": "demo", "demo_chunk_delay_ms": 0},
            "tts": {"provider": "fake"},
        }
    )


@pytest.fixture
def client(runtime_settings: Settings) -> Iterator[TestClient]:
    app = create_app(runtime_settings)
    token = app.state.container.capability_token
    with TestClient(app, headers={"Authorization": f"Bearer {token}"}) as test_client:
        yield test_client
