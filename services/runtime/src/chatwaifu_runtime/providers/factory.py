"""Provider composition based only on validated Runtime settings."""

import shutil
import sys
from dataclasses import dataclass

from chatwaifu_runtime.config.settings import Settings
from chatwaifu_runtime.providers.contracts import LlmProvider, TtsProvider, TtsProviderDescriptor
from chatwaifu_runtime.providers.demo_llm import DemoLlmProvider
from chatwaifu_runtime.providers.openai_compatible import OpenAiCompatibleLlmProvider
from chatwaifu_runtime.providers.tts import (
    FakeTtsProvider,
    MacOsSayTtsProvider,
    SherpaKokoroWorkerTtsProvider,
    WorkerTtsProvider,
)
from chatwaifu_runtime.providers.tts_aliyun import AliyunQwenRealtimeTtsProvider
from chatwaifu_runtime.providers.tts_config import TtsConfigurationService
from chatwaifu_runtime.providers.tts_router import TtsRouter


@dataclass(frozen=True, slots=True)
class ProviderSet:
    llm: LlmProvider
    tts: TtsRouter

    def public_status(self) -> dict[str, str]:
        return {"llm": self.llm.kind, "tts": self.tts.kind}


def build_providers(
    settings: Settings,
    *,
    llm_override: LlmProvider | None = None,
    tts_configurations: TtsConfigurationService | None = None,
) -> ProviderSet:
    if llm_override is not None:
        llm = llm_override
    elif settings.llm.provider == "demo":
        llm: LlmProvider = DemoLlmProvider(settings.llm.demo_chunk_delay_ms)
    elif settings.llm.provider == "openai_compatible":
        key = settings.llm.api_key.get_secret_value() if settings.llm.api_key else None
        llm = OpenAiCompatibleLlmProvider(
            base_url=settings.llm.base_url,
            model=settings.llm.model,
            api_key=key,
            timeout_seconds=settings.llm.timeout_seconds,
        )
    else:
        raise ValueError(f"unsupported LLM provider: {settings.llm.provider}")

    tts_kind = settings.tts.selected_provider
    tts_providers: dict[str, TtsProvider] = {}
    if tts_configurations is not None:
        tts_providers["aliyun_qwen_realtime"] = AliyunQwenRealtimeTtsProvider(tts_configurations)
    if settings.tts.provider is None:
        for provider_id, endpoint in settings.tts.workers.items():
            token = endpoint.token.get_secret_value() if endpoint.token else None
            tts_providers[provider_id] = WorkerTtsProvider(
                descriptor=TtsProviderDescriptor(
                    provider_id=provider_id,
                    display_name=endpoint.display_name,
                    model=endpoint.model,
                    languages=tuple(endpoint.languages),
                    supports_voice_cloning=endpoint.supports_voice_cloning,
                    supports_style=endpoint.supports_style,
                    supports_speed=endpoint.supports_speed,
                    supports_pitch=endpoint.supports_pitch,
                    native_streaming=endpoint.native_streaming,
                    local_only=True,
                ),
                base_url=endpoint.url,
                token=token,
                timeout_seconds=settings.tts.timeout_seconds,
            )
    if tts_kind == "auto":
        tts_kind = (
            "macos_say"
            if sys.platform == "darwin" and shutil.which("say") and shutil.which("afconvert")
            else "fake"
        )
    if tts_kind == "macos_say":
        tts_providers[tts_kind] = MacOsSayTtsProvider(
            voice=settings.tts.voice,
            sample_rate=settings.tts.sample_rate,
            rate=settings.tts.rate,
            timeout_seconds=settings.tts.timeout_seconds,
        )
    elif tts_kind == "fake":
        tts_providers[tts_kind] = FakeTtsProvider(settings.tts.sample_rate)
    elif tts_kind == "sherpa_kokoro_worker":
        if settings.tts.worker_token is None:
            raise ValueError("TTS worker token is required")
        tts_providers[tts_kind] = SherpaKokoroWorkerTtsProvider(
            base_url=settings.tts.worker_url,
            token=settings.tts.worker_token.get_secret_value(),
            timeout_seconds=settings.tts.timeout_seconds,
        )
    elif tts_kind not in tts_providers:
        raise ValueError(f"unsupported TTS provider: {tts_kind}")
    return ProviderSet(llm=llm, tts=TtsRouter(tts_providers, tts_kind))
