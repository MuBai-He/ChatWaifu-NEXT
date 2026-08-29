"""Generation-scoped TTS, PCM publication, playback registration, and fallback asset."""

from __future__ import annotations

from collections.abc import Callable
from typing import Literal, Protocol
from uuid import UUID

from chatwaifu_runtime.audio.store import AudioAssetStore
from chatwaifu_runtime.audio.streaming import AudioStreamHub, AudioStreamPacket
from chatwaifu_runtime.characters.service import CharacterVoiceProfile
from chatwaifu_runtime.conversation.models import GenerationAccepted
from chatwaifu_runtime.playback.service import PlaybackService
from chatwaifu_runtime.providers.contracts import (
    SynthesisRequest,
    SynthesisResult,
    TtsPcmChunk,
)
from chatwaifu_runtime.providers.factory import ProviderSet


class GenericEmitter(Protocol):
    async def __call__(
        self,
        accepted: GenerationAccepted,
        event_type: str,
        payload: dict[str, object],
    ) -> None: ...


class AvatarEmitter(Protocol):
    async def __call__(
        self,
        accepted: GenerationAccepted,
        kind: Literal["state", "expression", "motion", "gaze", "speech", "override"],
        name: str,
        *,
        priority: int,
        duration_ms: int | None = None,
    ) -> None: ...


class ConversationSpeechPipeline:
    def __init__(
        self,
        providers: ProviderSet,
        audio_assets: AudioAssetStore,
        audio_streams: AudioStreamHub,
        playback: PlaybackService,
    ) -> None:
        self._providers = providers
        self._audio_assets = audio_assets
        self._audio_streams = audio_streams
        self._playback = playback

    async def synthesize_segment(
        self,
        accepted: GenerationAccepted,
        text: str,
        segment_index: int,
        voice: CharacterVoiceProfile,
        *,
        style: str | None,
        ensure_current: Callable[[GenerationAccepted], None],
        emit_generic: GenericEmitter,
        emit_avatar: AvatarEmitter,
    ) -> None:
        ensure_current(accepted)
        normalized = text.strip()
        await emit_generic(accepted, "assistant.text_segment_committed", {"text": normalized})
        asset = self._audio_assets.allocate()
        await self._playback.register_segment(
            session_id=accepted.session_id,
            generation_id=accepted.generation_id,
            stream_id=accepted.audio_stream_id,
            segment_id=asset.asset_id,
            segment_index=segment_index,
            text=normalized,
            duration_ms=0,
            duration_finalized=False,
        )
        result: SynthesisResult | None = None
        stream_started = False
        native_streaming = False
        streamed_audio_bytes = 0
        stream_sample_rate = 24_000
        stream_channels = 1
        live_consumers: set[UUID] | None = None
        try:
            stream = self._providers.tts.stream(
                SynthesisRequest(
                    session_id=accepted.session_id,
                    turn_id=accepted.turn_id,
                    generation_id=accepted.generation_id,
                    segment_id=asset.asset_id,
                    text=normalized,
                    destination=asset.path,
                    language=voice.language,
                    voice_id=voice.voice_id,
                    speaker_id=voice.speaker_id,
                    speed=voice.speed,
                    style=style,
                )
            )
            async for event in stream:
                ensure_current(accepted)
                if isinstance(event, TtsPcmChunk):
                    native_streaming = native_streaming or event.native_streaming
                    streamed_audio_bytes += len(event.pcm16)
                    stream_sample_rate = event.sample_rate
                    stream_channels = event.channels
                    if not stream_started:
                        stream_started = True
                        live_consumers = set(
                            await self._audio_streams.publish_receipts(
                                AudioStreamPacket(
                                    phase="started",
                                    session_id=accepted.session_id,
                                    turn_id=accepted.turn_id,
                                    generation_id=accepted.generation_id,
                                    stream_id=accepted.audio_stream_id,
                                    segment_id=asset.asset_id,
                                    segment_index=segment_index,
                                    text=normalized,
                                    sample_rate=event.sample_rate,
                                    channels=event.channels,
                                    native_streaming=event.native_streaming,
                                    provider_id=self._providers.tts.provider_for(
                                        accepted.session_id
                                    ),
                                )
                            )
                        )
                    chunk_consumers = await self._audio_streams.publish_receipts(
                        AudioStreamPacket(
                            phase="chunk",
                            session_id=accepted.session_id,
                            turn_id=accepted.turn_id,
                            generation_id=accepted.generation_id,
                            stream_id=accepted.audio_stream_id,
                            segment_id=asset.asset_id,
                            segment_index=segment_index,
                            text=normalized,
                            sequence=event.sequence,
                            sample_rate=event.sample_rate,
                            channels=event.channels,
                            native_streaming=event.native_streaming,
                            pcm16=event.pcm16,
                            provider_id=self._providers.tts.provider_for(accepted.session_id),
                        )
                    )
                    if live_consumers is not None:
                        live_consumers.intersection_update(chunk_consumers)
                else:
                    result = event.result
            if result is None:
                raise RuntimeError("TTS provider ended without a completed result")
        except BaseException:
            asset.path.unlink(missing_ok=True)
            if stream_started:
                partial_duration_ms = (
                    streamed_audio_bytes * 1000 // max(1, stream_sample_rate * stream_channels * 2)
                )
                await self._playback.finalize_segment(asset.asset_id, partial_duration_ms)
                await self._audio_streams.publish(
                    AudioStreamPacket(
                        phase="cancelled",
                        session_id=accepted.session_id,
                        turn_id=accepted.turn_id,
                        generation_id=accepted.generation_id,
                        stream_id=accepted.audio_stream_id,
                        segment_id=asset.asset_id,
                        segment_index=segment_index,
                        text=normalized,
                        native_streaming=native_streaming,
                        duration_ms=partial_duration_ms,
                        reason="generation_cancelled",
                    )
                )
            else:
                await self._playback.discard_segment(asset.asset_id)
            raise
        ensure_current(accepted)
        await self._playback.finalize_segment(asset.asset_id, result.duration_ms)
        completion_consumers = await self._audio_streams.publish_receipts(
            AudioStreamPacket(
                phase="completed",
                session_id=accepted.session_id,
                turn_id=accepted.turn_id,
                generation_id=accepted.generation_id,
                stream_id=accepted.audio_stream_id,
                segment_id=asset.asset_id,
                segment_index=segment_index,
                text=normalized,
                sample_rate=result.sample_rate,
                duration_ms=result.duration_ms,
                native_streaming=native_streaming,
                provider_id=result.provider_id,
                model=result.model,
            )
        )
        if live_consumers is not None:
            live_consumers.intersection_update(completion_consumers)
        await emit_avatar(accepted, "speech", "speaking", priority=70)
        await emit_generic(
            accepted,
            "assistant.audio_chunk_queued",
            {
                "asset_id": str(asset.asset_id),
                "stream_id": str(accepted.audio_stream_id),
                "segment_id": str(asset.asset_id),
                "segment_index": segment_index,
                "url": asset.url,
                "text": normalized,
                "media_type": result.media_type,
                "sample_rate": result.sample_rate,
                "duration_ms": result.duration_ms,
                "tts_provider": result.provider_id,
                "tts_model": result.model,
                "streamed_live": stream_started and native_streaming and bool(live_consumers),
            },
        )
