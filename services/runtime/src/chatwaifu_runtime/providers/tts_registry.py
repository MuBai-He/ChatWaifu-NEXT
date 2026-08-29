"""Single registration point for configurable TTS provider adapters."""

from __future__ import annotations

from datetime import datetime

from chatwaifu_runtime.providers.contracts import TtsProvider
from chatwaifu_runtime.providers.tts_config import (
    ALIYUN_COSYVOICE_TTS_PROVIDER_ID,
    ALIYUN_QWEN_TTS_PROVIDER_ID,
    AliyunCosyVoiceTtsConfiguration,
    AliyunTtsConfiguration,
    TtsConfigurationService,
    TtsProviderPresentation,
    TtsProviderRegistration,
    TtsUiField,
    TtsUiOption,
)


def _build_qwen(configurations: TtsConfigurationService) -> TtsProvider:
    from chatwaifu_runtime.providers.tts_aliyun import AliyunQwenRealtimeTtsProvider

    return AliyunQwenRealtimeTtsProvider(configurations)


def _build_cosyvoice(configurations: TtsConfigurationService) -> TtsProvider:
    from chatwaifu_runtime.providers.tts_aliyun_cosyvoice import (
        AliyunCosyVoiceRealtimeTtsProvider,
    )

    return AliyunCosyVoiceRealtimeTtsProvider(configurations)


def _default_qwen(now: datetime) -> AliyunTtsConfiguration:
    return AliyunTtsConfiguration(updated_at=now)


def _default_cosyvoice(now: datetime) -> AliyunCosyVoiceTtsConfiguration:
    return AliyunCosyVoiceTtsConfiguration(updated_at=now)


_ENABLED = TtsUiField("enabled", "启用", "toggle")
_MODEL = TtsUiField("model", "模型", "text", placeholder="服务端模型 ID")
_VOICE = TtsUiField("voice_id", "复刻音色 ID", "text", placeholder="bailian voice id")
_REGION = TtsUiField(
    "region",
    "地域",
    "select",
    options=(TtsUiOption("beijing", "北京"), TtsUiOption("singapore", "新加坡")),
)
_WORKSPACE = TtsUiField(
    "workspace_id",
    "业务空间 ID",
    "text",
    advanced=True,
    help_text="仅专属业务空间需要填写。",
)
_SAMPLE_RATE = TtsUiField(
    "sample_rate",
    "采样率",
    "select",
    advanced=True,
    options=tuple(
        TtsUiOption(value, f"{value // 1000} kHz") for value in (8000, 16000, 24000, 48000)
    ),
)
_SPEECH_RATE = TtsUiField("speech_rate", "语速", "number", minimum=0.5, maximum=2.0, step=0.05)
_VOLUME = TtsUiField("volume", "音量", "number", minimum=0, maximum=100, step=1)
_PITCH = TtsUiField("pitch_rate", "音高", "number", minimum=0.5, maximum=2.0, step=0.05)
_TIMEOUT = TtsUiField(
    "timeout_seconds", "请求超时 (秒)", "number", advanced=True, minimum=1, maximum=300, step=1
)
_MAX_AUDIO = TtsUiField(
    "max_audio_bytes",
    "单次音频上限 (字节)",
    "number",
    advanced=True,
    minimum=1_000_000,
    maximum=128_000_000,
    step=1_000_000,
)
_API_KEY = TtsUiField(
    "api_key",
    "API Key",
    "secret",
    placeholder="只写入本机 Runtime，不会返回浏览器",
)


TTS_PROVIDER_REGISTRATIONS = (
    TtsProviderRegistration(
        provider_id=ALIYUN_QWEN_TTS_PROVIDER_ID,
        display_name="阿里云百炼 · Qwen3-TTS 实时音色",
        configuration_type=AliyunTtsConfiguration,
        default_factory=_default_qwen,
        build=_build_qwen,
        ui_fields=(
            _ENABLED,
            _MODEL,
            _VOICE,
            _REGION,
            _WORKSPACE,
            TtsUiField(
                "language_type",
                "语言",
                "select",
                options=tuple(
                    TtsUiOption(value, value)
                    for value in (
                        "Auto",
                        "Chinese",
                        "English",
                        "Japanese",
                        "Korean",
                        "French",
                        "German",
                        "Italian",
                        "Portuguese",
                        "Spanish",
                        "Russian",
                    )
                ),
            ),
            _SAMPLE_RATE,
            _SPEECH_RATE,
            _VOLUME,
            _PITCH,
            _TIMEOUT,
            _MAX_AUDIO,
            _API_KEY,
        ),
        presentation=TtsProviderPresentation(
            group_id="aliyun_bailian",
            group_display_name="阿里云百炼",
            variant_label="Qwen3-TTS VC",
        ),
    ),
    TtsProviderRegistration(
        provider_id=ALIYUN_COSYVOICE_TTS_PROVIDER_ID,
        display_name="阿里云百炼 · CosyVoice 实时音色",
        configuration_type=AliyunCosyVoiceTtsConfiguration,
        default_factory=_default_cosyvoice,
        build=_build_cosyvoice,
        ui_fields=(
            _ENABLED,
            _MODEL,
            _VOICE,
            _REGION,
            _WORKSPACE,
            TtsUiField(
                "language_type",
                "语言",
                "select",
                options=tuple(
                    TtsUiOption(value, value)
                    for value in (
                        "auto",
                        "zh",
                        "en",
                        "ja",
                        "ko",
                        "fr",
                        "de",
                        "ru",
                        "pt",
                        "th",
                        "id",
                        "vi",
                        "es",
                        "it",
                        "ms",
                        "fil",
                        "ar",
                    )
                ),
            ),
            TtsUiField(
                "instruction",
                "基础情绪指令",
                "textarea",
                placeholder="例如: 温柔自然，带一点害羞。",
                help_text="仅支持情绪指令的 CosyVoice 模型生效。",
            ),
            _SAMPLE_RATE,
            _SPEECH_RATE,
            _VOLUME,
            _PITCH,
            _TIMEOUT,
            _MAX_AUDIO,
            _API_KEY,
        ),
        api_key_fallback_provider_id=ALIYUN_QWEN_TTS_PROVIDER_ID,
        presentation=TtsProviderPresentation(
            group_id="aliyun_bailian",
            group_display_name="阿里云百炼",
            variant_label="CosyVoice",
            group_default=True,
        ),
    ),
)
