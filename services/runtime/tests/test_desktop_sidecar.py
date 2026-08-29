"""Packaged desktop Runtime path and fallback behavior."""

from pathlib import Path

from chatwaifu_runtime.desktop_sidecar import prepare_environment


def test_packaged_environment_separates_resources_from_writable_data(tmp_path: Path) -> None:
    resources = tmp_path / "runtime-resources"
    config = tmp_path / "user" / "config"
    data = tmp_path / "user" / "data"
    environment = {
        "CHATWAIFU_CONFIG_DIR": str(config),
        "CHATWAIFU_DATA_DIR": str(data),
    }

    prepared = prepare_environment(environment, frozen=True, resource_root=resources)

    assert prepared["CHATWAIFU_RESOURCE_ROOT"] == str(resources)
    assert prepared["CHATWAIFU_CHARACTERS_DIR"] == str(resources / "characters")
    assert prepared["CHATWAIFU_SKILLS_DIR"] == str(resources / "skills")
    assert prepared["NLTK_DATA"] == str(resources / "nltk_data")
    assert prepared["CHATWAIFU_CONFIG_DIR"] == str(config)
    assert prepared["CHATWAIFU_DATA_DIR"] == str(data)
    assert config.is_dir()
    assert data.is_dir()


def test_packaged_environment_starts_without_optional_local_models(tmp_path: Path) -> None:
    environment = {
        "CHATWAIFU_CONFIG_DIR": str(tmp_path / "config"),
        "CHATWAIFU_DATA_DIR": str(tmp_path / "data"),
    }

    prepared = prepare_environment(
        environment,
        frozen=True,
        resource_root=tmp_path / "resources",
    )

    assert prepared["CHATWAIFU_STT__PROVIDER"] == "disabled"
    assert prepared["CHATWAIFU_TTS__PROVIDER"] == "fake"
    assert prepared["CHATWAIFU_TTS__DEFAULT_PROVIDER"] == "fake"
    assert prepared["CHATWAIFU_TTS__WORKERS"] == "{}"
