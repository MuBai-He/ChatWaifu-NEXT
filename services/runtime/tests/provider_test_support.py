"""Test seam for injecting a chat stream through the per-turn provider factory."""

import pytest
from chatwaifu_runtime.providers.contracts import LlmProvider
from chatwaifu_runtime.providers.model_config import (
    ModelConfigurationService,
    ModelRoleConfig,
)


def use_chat_stream_stub(
    monkeypatch: pytest.MonkeyPatch, models: ModelConfigurationService
) -> None:
    def create(_config: ModelRoleConfig) -> LlmProvider:
        return models.chat

    monkeypatch.setattr(models, "create_chat_provider", create)


def use_recording_provider(
    monkeypatch: pytest.MonkeyPatch, models: ModelConfigurationService, provider: LlmProvider
) -> None:
    def create(_config: ModelRoleConfig) -> LlmProvider:
        return provider

    monkeypatch.setattr(models, "create_chat_provider", create)
