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

import httpx2
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
    AliyunTtsConfiguration,
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
        http_client: httpx2.AsyncClient | None = None,
    ) -> None:
        self._configurations = configurations
        self._websocket_factory = websocket_factory
        self._http_client = http_client or httpx2.AsyncClient()
        self._owns_http_client = http_client is None
        self._validated_voice_signature: tuple[str, ...] | None = None

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

        await self._validate_voice_binding(config, api_key)

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
        if self._owns_http_client:
            await self._http_client.aclose()

    async def _validate_voice_binding(self, config: AliyunTtsConfiguration, api_key: str) -> None:
        signature = (
            config.region,
            config.workspace_id,
            config.voice_id,
            config.model,
            config.updated_at.isoformat(),
        )
        if self._validated_voice_signature == signature:
            return

        headers = {"Authorization": f"Bearer {api_key}"}
        if config.workspace_id:
            headers["X-DashScope-WorkSpace"] = config.workspace_id
        page_size = 100
        for page_index in range(20):
            try:
                response = await self._http_client.post(
                    config.voice_catalog_url,
                    headers=headers,
                    json={
                        "model": "qwen-voice-enrollment",
                        "input": {
                            "action": "list",
                            "page_size": page_size,
                            "page_index": page_index,
                        },
                    },
                    timeout=config.timeout_seconds,
                )
                response.raise_for_status()
            except httpx2.HTTPStatusError as error:
                if error.response.status_code in {401, 403}:
                    raise RuntimeError(
                        "百炼 API Key 无法访问当前地域的 Qwen 音色; "
                        "请确认 Key 与北京/新加坡设置一致"
                    ) from error
                raise RuntimeError(
                    f"百炼音色元数据查询失败 (HTTP {error.response.status_code})"
                ) from error
            except httpx2.TimeoutException as error:
                raise RuntimeError("百炼音色元数据查询超时") from error
            except httpx2.HTTPError as error:
                raise RuntimeError("无法连接百炼音色元数据服务") from error

            payload = _json_object(response)
            output = payload.get("output")
            if not isinstance(output, dict):
                raise RuntimeError("百炼音色列表返回了无效数据")
            output_value = cast(dict[str, object], output)
            items = output_value.get("voice_list")
            if not isinstance(items, list):
                raise RuntimeError("百炼音色列表缺少 voice_list")
            item_values = cast(list[object], items)
            for item in item_values:
                if not isinstance(item, dict):
                    continue
                item_value = cast(dict[str, object], item)
                if item_value.get("voice") != config.voice_id:
                    continue
                target_model = item_value.get("target_model")
                if not isinstance(target_model, str) or not target_model:
                    raise RuntimeError("百炼音色缺少 target_model，无法安全调用")
                if target_model != config.model:
                    raise RuntimeError(
                        "百炼音色与模型不匹配: 该音色绑定的是 "
                        f"{target_model}，当前实时模型是 {config.model}。"
                        "要保持实时流式，请使用当前实时模型重新复刻音色"
                    )
                self._validated_voice_signature = signature
                return

            total_count = output_value.get("total_count")
            if (
                not isinstance(total_count, int)
                or total_count <= (page_index + 1) * page_size
                or not item_values
            ):
                break
        raise RuntimeError(
            "当前 API Key、地域和业务空间下未找到该 Qwen 音色; 请检查音色 ID 与地域设置"
        )


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


def _json_object(response: httpx2.Response) -> dict[str, object]:
    try:
        payload: object = response.json()
    except ValueError as error:
        raise RuntimeError("百炼音色列表返回了非 JSON 数据") from error
    if not isinstance(payload, dict):
        raise RuntimeError("百炼音色列表返回了无效数据")
    return cast(dict[str, object], payload)
