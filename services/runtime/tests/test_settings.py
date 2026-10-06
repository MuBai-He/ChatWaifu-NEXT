"""Configuration precedence and secret redaction."""

from pathlib import Path

import pytest
from chatwaifu_runtime.config.settings import (
    PublicWebConfig,
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


def test_qwen_is_default_and_both_neural_tts_workers_are_declared() -> None:
    settings = Settings()

    assert settings.stt.language == "auto"
    assert settings.tts.selected_provider == "qwen3_tts_mlx"
    assert set(settings.tts.workers) == {"qwen3_tts_mlx", "gpt_sovits"}


def test_public_web_defaults_are_local_and_external_secrets_are_redacted() -> None:
    assert PublicWebConfig().qq_owner_reads_enabled is False
    settings = Settings(
        public_web=PublicWebConfig(
            search_provider="firecrawl",
            reader_provider="jina",
            firecrawl_api_key=SecretStr("fire-secret"),
            jina_api_key=SecretStr("jina-secret"),
            crawl4ai_api_token=SecretStr("crawl-token"),
        )
    )
    assert settings.public_web.search_provider == "firecrawl"
    assert settings.public_web.reader_provider == "jina"
    public = str(settings.public_dict())
    assert "fire-secret" not in public
    assert "jina-secret" not in public
    assert "crawl-token" not in public


def test_public_web_config_accepts_so360_search_origin() -> None:
    settings = PublicWebConfig(search_provider="so360", so360_endpoint="https://www.so.com")
    assert settings.search_provider == "so360"


def test_public_web_config_accepts_jina_sogou_search_provider() -> None:
    settings = PublicWebConfig(search_provider="jina_sogou")
    assert settings.search_provider == "jina_sogou"


def test_public_web_config_defaults_to_desktop_so360_endpoint() -> None:
    assert PublicWebConfig(search_provider="so360").so360_endpoint == "https://www.so.com"


def test_public_web_builtin_recovery_requires_explicit_configuration() -> None:
    assert PublicWebConfig().crawl4ai_builtin_fallback is False
    assert (
        PublicWebConfig(
            reader_provider="crawl4ai", crawl4ai_builtin_fallback=True
        ).crawl4ai_builtin_fallback
        is True
    )


@pytest.mark.parametrize(
    "endpoint",
    [
        "http://api.example.com",
        "https://user:secret@example.com",
        "https://api.example.com/v2",
    ],
)
def test_public_web_provider_origins_require_https_origins(endpoint: str) -> None:
    with pytest.raises(ValueError):
        PublicWebConfig(firecrawl_endpoint=endpoint)


@pytest.mark.parametrize(
    "endpoint",
    [
        "https://10.0.0.2:8080",
        "http://127.0.0.1/path",
        "http://user:pass@127.0.0.1:8080",
        "http://127.0.0.1:0",
    ],
)
def test_local_public_web_provider_origins_are_loopback_only(endpoint: str) -> None:
    with pytest.raises(ValueError):
        PublicWebConfig(searxng_endpoint=endpoint)
