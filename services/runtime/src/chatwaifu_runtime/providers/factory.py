"""Provider composition based only on validated Runtime settings."""

import shutil
import sys
from dataclasses import dataclass

from chatwaifu_runtime.config.settings import Settings
from chatwaifu_runtime.providers.contracts import LlmProvider, TtsProvider
from chatwaifu_runtime.providers.demo_llm import DemoLlmProvider
from chatwaifu_runtime.providers.openai_compatible import OpenAiCompatibleLlmProvider
from chatwaifu_runtime.providers.tts import (
    FakeTtsProvider,
    MacOsSayTtsProvider,
    SherpaKokoroWorkerTtsProvider,
)


@dataclass(frozen=True, slots=True)
class ProviderSet:
    llm: LlmProvider
    tts: TtsProvider

    def public_status(self) -> dict[str, str]:
        return {"llm": self.llm.kind, "tts": self.tts.kind}


def build_providers(settings: Settings) -> ProviderSet:
    if settings.llm.provider == "demo":
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

    tts_kind = settings.tts.provider
    if tts_kind == "auto":
        tts_kind = (
            "macos_say"
            if sys.platform == "darwin" and shutil.which("say") and shutil.which("afconvert")
            else "fake"
        )
    if tts_kind == "macos_say":
        tts: TtsProvider = MacOsSayTtsProvider(
            voice=settings.tts.voice,
            sample_rate=settings.tts.sample_rate,
            rate=settings.tts.rate,
            timeout_seconds=settings.tts.timeout_seconds,
        )
    elif tts_kind == "fake":
        tts = FakeTtsProvider(settings.tts.sample_rate)
    elif tts_kind == "sherpa_kokoro_worker":
        if settings.tts.worker_token is None:
            raise ValueError("TTS worker token is required")
        tts = SherpaKokoroWorkerTtsProvider(
            base_url=settings.tts.worker_url,
            token=settings.tts.worker_token.get_secret_value(),
            timeout_seconds=settings.tts.timeout_seconds,
        )
    else:
        raise ValueError(f"unsupported TTS provider: {tts_kind}")
    return ProviderSet(llm=llm, tts=tts)
