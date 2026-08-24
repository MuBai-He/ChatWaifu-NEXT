"""Configuration precedence and secret redaction."""

from pathlib import Path

import pytest
from chatwaifu_runtime.config.settings import (
    SecurityConfig,
    Settings,
    SttConfig,
    TtsConfig,
    load_settings,
)
from pydantic import SecretStr


def test_environment_overrides_toml(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    config = tmp_path / "runtime.toml"
    config.write_text('[runtime]\nport = 8123\n[storage]\nkind = "sqlite"\n', encoding="utf-8")
    monkeypatch.setenv("CHATWAIFU_RUNTIME__PORT", "9001")
    settings = load_settings(config)
    assert settings.runtime.port == 9001


def test_dotenv_is_loaded_but_process_environment_wins(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    config = tmp_path / "default.toml"
    config.write_text('[runtime]\nport = 8123\n[storage]\nkind = "sqlite"\n', encoding="utf-8")
    env_file = tmp_path / ".env"
    env_file.write_text("CHATWAIFU_RUNTIME__PORT=8333\n", encoding="utf-8")
    monkeypatch.setenv("CHATWAIFU_RUNTIME__PORT", "8444")

    settings = load_settings(config, env_file)

    assert settings.runtime.port == 8444


def test_public_config_omits_secrets() -> None:
    settings = Settings(
        security=SecurityConfig(admin_token=SecretStr("never-log-me")),
        stt=SttConfig(worker_token=SecretStr("never-log-worker-token")),
        tts=TtsConfig(worker_token=SecretStr("never-log-tts-token")),
    )
    serialized = str(settings.public_dict())
    assert "never-log-me" not in serialized
    assert "never-log-worker-token" not in serialized
    assert "never-log-tts-token" not in serialized
    assert "security" not in settings.public_dict()
