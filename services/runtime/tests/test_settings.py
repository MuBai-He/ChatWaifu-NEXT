"""Configuration precedence and secret redaction."""

from pathlib import Path

import pytest
from chatwaifu_runtime.config.settings import SecurityConfig, Settings, load_settings
from pydantic import SecretStr


def test_environment_overrides_toml(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    config = tmp_path / "runtime.toml"
    config.write_text('[runtime]\nport = 8123\n[storage]\nkind = "sqlite"\n', encoding="utf-8")
    monkeypatch.setenv("CHATWAIFU_RUNTIME__PORT", "9001")
    settings = load_settings(config)
    assert settings.runtime.port == 9001


def test_public_config_omits_secrets() -> None:
    settings = Settings(security=SecurityConfig(admin_token=SecretStr("never-log-me")))
    serialized = str(settings.public_dict())
    assert "never-log-me" not in serialized
    assert "security" not in settings.public_dict()
