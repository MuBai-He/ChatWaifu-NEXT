from collections.abc import AsyncIterator
from pathlib import Path
from types import SimpleNamespace
from typing import Literal, cast
from uuid import UUID

import pytest
from chatwaifu_protocol.session import GenerationState
from chatwaifu_runtime.audio.store import AudioAssetStore
from chatwaifu_runtime.audio.streaming import AudioStreamHub
from chatwaifu_runtime.characters.service import CharacterVoiceProfile
from chatwaifu_runtime.conversation.models import GenerationAccepted
from chatwaifu_runtime.conversation.speech import (
    ConversationSpeechPipeline,
    synthesis_language_for_text,
)
from chatwaifu_runtime.playback.service import PlaybackService
from chatwaifu_runtime.providers.contracts import (
    SynthesisRequest,
    SynthesisResult,
    TtsPcmChunk,
    TtsStreamCompleted,
    TtsStreamEvent,
)
from chatwaifu_runtime.providers.factory import ProviderSet


class _CapturingTtsRouter:
    def __init__(self) -> None:
        self.requests: list[SynthesisRequest] = []

    def provider_for(self, session_id: UUID) -> str:
        del session_id
        return "capturing-tts"

    async def stream(self, request: SynthesisRequest) -> AsyncIterator[TtsStreamEvent]:
        self.requests.append(request)
        yield TtsPcmChunk(
            sequence=0,
            pcm16=b"\x00\x00",
            sample_rate=24_000,
            native_streaming=False,
        )
        yield TtsStreamCompleted(
            result=SynthesisResult(
                path=request.destination,
                media_type="audio/wav",
                sample_rate=24_000,
                duration_ms=1,
                provider_id="capturing-tts",
                model="fixture",
            )
        )


class _RecordingPlayback:
    def __init__(self) -> None:
        self.segment_id: UUID | None = None

    async def register_segment(
        self,
        *,
        session_id: UUID,
        generation_id: UUID,
        stream_id: UUID,
        segment_id: UUID,
        segment_index: int,
        text: str,
        duration_ms: int,
        duration_finalized: bool = True,
    ) -> None:
        del (
            session_id,
            generation_id,
            stream_id,
            segment_index,
            text,
            duration_ms,
            duration_finalized,
        )
        self.segment_id = segment_id

    async def finalize_segment(self, segment_id: UUID, duration_ms: int) -> None:
        assert segment_id == self.segment_id
        assert duration_ms == 1

    async def discard_segment(self, segment_id: UUID) -> None:
        raise AssertionError(f"unexpected discarded segment: {segment_id}")


def test_japanese_kana_selects_the_japanese_tts_hint() -> None:
    assert synthesis_language_for_text("こんにちは、綾地寧々です。", "zh") == "ja"
    assert synthesis_language_for_text("ニンネンと一緒に頑張ろう。", "zh") == "ja"
    assert synthesis_language_for_text("ｺﾝﾆﾁﾊ", "zh") == "ja"
    assert synthesis_language_for_text("ㇰ", "zh") == "ja"


def test_shared_ideographs_and_punctuation_keep_the_character_default() -> None:
    assert synthesis_language_for_text("你好，我是绫地宁宁。", "zh") == "zh"
    assert synthesis_language_for_text("東京", "zh") == "zh"
    assert synthesis_language_for_text("Qwen3-TTS · 宁宁", "zh") == "zh"
    assert synthesis_language_for_text("Hello, Nene.", "en") == "en"


@pytest.mark.asyncio
async def test_pipeline_sets_kana_language_without_changing_generation_identity(
    tmp_path: Path,
) -> None:
    session_id = UUID("00000000-0000-4000-8000-000000000101")
    turn_id = UUID("00000000-0000-4000-8000-000000000102")
    generation_id = UUID("00000000-0000-4000-8000-000000000103")
    stream_id = UUID("00000000-0000-4000-8000-000000000104")
    accepted = GenerationAccepted(
        session_id=session_id,
        turn_id=turn_id,
        generation_id=generation_id,
        audio_stream_id=stream_id,
        state=GenerationState.RUNNING,
    )
    router = _CapturingTtsRouter()
    playback = _RecordingPlayback()
    assets = AudioAssetStore(tmp_path / "audio")
    assets.start()
    audio_streams = AudioStreamHub()
    pipeline = ConversationSpeechPipeline(
        cast(ProviderSet, cast(object, SimpleNamespace(tts=router))),
        assets,
        audio_streams,
        cast(PlaybackService, cast(object, playback)),
    )
    current_checks: list[GenerationAccepted] = []

    async def emit_generic(
        accepted: GenerationAccepted,
        event_type: str,
        payload: dict[str, object],
    ) -> None:
        del accepted, event_type, payload

    async def emit_avatar(
        accepted: GenerationAccepted,
        kind: Literal["state", "expression", "motion", "gaze", "speech", "override"],
        name: str,
        *,
        priority: int,
        duration_ms: int | None = None,
    ) -> None:
        del accepted, kind, name, priority, duration_ms

    await pipeline.synthesize_segment(
        accepted,
        "  こんにちは、綾地寧々です。  ",
        2,
        CharacterVoiceProfile(
            voice_id="ayachi_nene_local",
            display_name="绫地宁宁",
            language="zh",
            provider="capturing-tts",
            model="fixture",
            speaker_id=7,
            speed=1.1,
            license="test-only",
        ),
        style="gentle",
        ensure_current=current_checks.append,
        emit_generic=emit_generic,
        emit_avatar=emit_avatar,
    )
    await audio_streams.close()

    assert len(router.requests) == 1
    request = router.requests[0]
    assert request.session_id == session_id
    assert request.turn_id == turn_id
    assert request.generation_id == generation_id
    assert request.segment_id == playback.segment_id
    assert request.text == "こんにちは、綾地寧々です。"
    assert request.language == "ja"
    assert request.voice_id == "ayachi_nene_local"
    assert request.speaker_id == 7
    assert request.speed == 1.1
    assert request.style == "gentle"
    assert current_checks
    assert all(check == accepted for check in current_checks)
