"""Aliyun Bailian Qwen voice-clone realtime TTS adapter."""

from __future__ import annotations

import asyncio
import base64
import binascii
import json
import logging
import wave
from collections.abc import AsyncIterator, Callable
from contextlib import AbstractAsyncContextManager
from pathlib import Path
from tempfile import TemporaryDirectory
from time import monotonic
from typing import Any, cast
from urllib.parse import urlencode
from uuid import uuid4

from websockets.asyncio.client import connect as websocket_connect

from chatwaifu_runtime.providers.contracts import (
    SynthesisRequest,
    SynthesisResult,
    TtsPcmChunk,
    TtsProviderDescriptor,
    TtsProviderHealth,
    TtsStreamCompleted,
    TtsStreamEvent,
)
from chatwaifu_runtime.providers.tts_config import (
    ALIYUN_TTS_PROVIDER_ID,
    TtsConfigurationService,
)

logger = logging.getLogger(__name__)

type WebSocketFactory = Callable[..., AbstractAsyncContextManager[Any]]
DEFAULT_WEBSOCKET_FACTORY = cast(WebSocketFactory, websocket_connect)


class AliyunQwenRealtimeTtsProvider:
    kind = ALIYUN_TTS_PROVIDER_ID

    def __init__(
        self,
        configurations: TtsConfigurationService,
        *,
        websocket_factory: WebSocketFactory = DEFAULT_WEBSOCKET_FACTORY,
    ) -> None:
        self._configurations = configurations
        self._websocket_factory = websocket_factory

    @property
    def descriptor(self) -> TtsProviderDescriptor:
        config = self._configurations.get()
        return TtsProviderDescriptor(
            provider_id=self.kind,
            display_name="阿里云百炼 · 宁宁实时音色",
            model=config.model,
            languages=("zh", "ja", "en"),
            supports_voice_cloning=True,
            supports_style=False,
            supports_speed=True,
            supports_pitch=True,
            native_streaming=True,
            local_only=False,
        )

    async def health(self) -> TtsProviderHealth:
        config = self._configurations.get()
        if not config.enabled:
            return TtsProviderHealth(
                status="unavailable",
                model_loaded=False,
                device="cloud",
                detail="请先在声音设置中启用百炼实时语音",
            )
        if self._configurations.api_key() is None:
            return TtsProviderHealth(
                status="unavailable",
                model_loaded=False,
                device="cloud",
                detail="尚未配置阿里云百炼 API Key",
            )
        return TtsProviderHealth(status="ready", model_loaded=True, device="cloud")

    async def synthesize(self, request: SynthesisRequest) -> SynthesisResult:
        completed: SynthesisResult | None = None
        async for event in self.stream(request):
            if isinstance(event, TtsStreamCompleted):
                completed = event.result
        if completed is None:
            raise RuntimeError("Aliyun realtime TTS ended without a completed result")
        return completed

    async def probe(self) -> dict[str, object]:
        with TemporaryDirectory(prefix="chatwaifu-aliyun-tts-probe-") as directory:
            request_id = uuid4()
            result = await self.synthesize(
                SynthesisRequest(
                    session_id=request_id,
                    turn_id=uuid4(),
                    generation_id=uuid4(),
                    segment_id=uuid4(),
                    text="你好。",
                    destination=Path(directory) / "probe.wav",
                    language="zh",
                    voice_id=self._configurations.get().voice_id,
                    speaker_id=0,
                    speed=1.0,
                )
            )
        return {
            "status": "ok",
            "provider_id": self.kind,
            "sample_rate": result.sample_rate,
            "duration_ms": result.duration_ms,
        }

    async def stream(self, request: SynthesisRequest) -> AsyncIterator[TtsStreamEvent]:
        config = self._configurations.get()
        api_key = self._configurations.api_key()
        if not config.enabled:
            raise RuntimeError("阿里云百炼实时语音尚未启用")
        if api_key is None:
            raise RuntimeError("阿里云百炼 API Key 尚未配置")

        request.destination.parent.mkdir(parents=True, exist_ok=True)
        request.destination.unlink(missing_ok=True)
        url = f"{config.websocket_base_url}?{urlencode({'model': config.model})}"
        headers = {
            "Authorization": f"Bearer {api_key}",
            "User-Agent": "ChatWaifu-NEXT/0.1",
        }
        if config.workspace_id:
            headers["X-DashScope-WorkSpace"] = config.workspace_id
        started_at = monotonic()
        audio = bytearray()
        sequence = 0
        response_done = False
        try:
            async with self._websocket_factory(
                url,
                additional_headers=headers,
                open_timeout=config.timeout_seconds,
                close_timeout=2,
                max_size=4_000_000,
            ) as websocket:
                await _wait_for_event(websocket, "session.created", config.timeout_seconds)
                await _send_event(
                    websocket,
                    "session.update",
                    session={
                        "voice": config.voice_id,
                        "mode": "commit",
                        "language_type": config.language_type,
                        "response_format": "pcm",
                        "sample_rate": config.sample_rate,
                        "speech_rate": config.speech_rate,
                        "volume": config.volume,
                        "pitch_rate": config.pitch_rate,
                    },
                )
                await _wait_for_event(websocket, "session.updated", config.timeout_seconds)
                await _send_event(websocket, "input_text_buffer.append", text=request.text)
                await _send_event(websocket, "input_text_buffer.commit")
                await _send_event(websocket, "session.finish")

                while True:
                    event = await _receive_event(websocket, config.timeout_seconds)
                    event_type = event.get("type")
                    if event_type == "response.audio.delta":
                        delta = event.get("delta")
                        if not isinstance(delta, str):
                            raise RuntimeError("Aliyun TTS returned an invalid audio delta")
                        try:
                            pcm16 = base64.b64decode(delta, validate=True)
                        except (ValueError, binascii.Error) as error:
                            raise RuntimeError(
                                "Aliyun TTS returned malformed base64 audio"
                            ) from error
                        if len(pcm16) % 2:
                            raise RuntimeError("Aliyun TTS returned unaligned PCM16 audio")
                        if len(audio) + len(pcm16) > config.max_audio_bytes:
                            raise RuntimeError(
                                "Aliyun TTS output exceeded the configured safety limit"
                            )
                        audio.extend(pcm16)
                        yield TtsPcmChunk(
                            sequence=sequence,
                            pcm16=pcm16,
                            sample_rate=config.sample_rate,
                            channels=1,
                            native_streaming=True,
                        )
                        sequence += 1
                    elif event_type == "response.done":
                        response = event.get("response")
                        if isinstance(response, dict):
                            response_value = cast(dict[str, object], response)
                            if response_value.get("status") == "failed":
                                raise RuntimeError("Aliyun TTS response failed")
                        response_done = True
                    elif event_type == "session.finished":
                        break
                if not response_done or not audio:
                    raise RuntimeError("Aliyun TTS returned no completed audio")
        except asyncio.CancelledError:
            request.destination.unlink(missing_ok=True)
            raise
        except BaseException:
            request.destination.unlink(missing_ok=True)
            raise

        await asyncio.to_thread(
            _write_pcm_wave,
            request.destination,
            bytes(audio),
            config.sample_rate,
            1,
        )
        duration_ms = len(audio) * 1000 // (config.sample_rate * 2)
        logger.info(
            "Aliyun realtime TTS completed generation=%s segment=%s "
            "chunks=%d duration_ms=%d latency_ms=%d",
            request.generation_id,
            request.segment_id,
            sequence,
            duration_ms,
            round((monotonic() - started_at) * 1000),
        )
        yield TtsStreamCompleted(
            result=SynthesisResult(
                path=request.destination,
                media_type="audio/wav",
                sample_rate=config.sample_rate,
                duration_ms=duration_ms,
                provider_id=self.kind,
                model=config.model,
            )
        )

    async def deactivate(self) -> None:
        return None

    async def close(self) -> None:
        return None


async def _send_event(websocket: Any, event_type: str, **payload: object) -> None:
    await websocket.send(
        json.dumps(
            {"event_id": f"event_{uuid4().hex}", "type": event_type, **payload},
            ensure_ascii=False,
        )
    )


async def _wait_for_event(websocket: Any, expected: str, limit_seconds: float) -> dict[str, object]:
    while True:
        event = await _receive_event(websocket, limit_seconds)
        if event.get("type") == expected:
            return event


async def _receive_event(websocket: Any, limit_seconds: float) -> dict[str, object]:
    async with asyncio.timeout(limit_seconds):
        raw = await websocket.recv()
    if not isinstance(raw, str):
        raise RuntimeError("Aliyun TTS returned a non-JSON WebSocket frame")
    try:
        parsed: object = json.loads(raw)
    except json.JSONDecodeError as error:
        raise RuntimeError("Aliyun TTS returned invalid JSON") from error
    if not isinstance(parsed, dict):
        raise RuntimeError("Aliyun TTS returned an invalid event")
    event = cast(dict[str, object], parsed)
    if event.get("type") == "error":
        error_value = event.get("error")
        if isinstance(error_value, dict):
            error_details = cast(dict[str, object], error_value)
            code = str(error_details.get("code", "provider_error"))
            message = str(error_details.get("message", "Aliyun TTS request failed"))
            raise RuntimeError(f"Aliyun TTS {code}: {message}")
        raise RuntimeError("Aliyun TTS request failed")
    return event


def _write_pcm_wave(destination: Path, pcm16: bytes, sample_rate: int, channels: int) -> None:
    with wave.open(str(destination), "wb") as output:
        output.setnchannels(channels)
        output.setsampwidth(2)
        output.setframerate(sample_rate)
        output.writeframes(pcm16)
