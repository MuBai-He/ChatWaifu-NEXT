"""Aliyun Bailian CosyVoice realtime TTS adapter."""

from __future__ import annotations

import asyncio
import json
import logging
import wave
from collections.abc import AsyncIterator, Callable
from contextlib import AbstractAsyncContextManager
from pathlib import Path
from tempfile import TemporaryDirectory
from time import monotonic
from typing import Any, cast
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
    ALIYUN_COSYVOICE_TTS_PROVIDER_ID,
    AliyunCosyVoiceTtsConfiguration,
    TtsConfigurationService,
)

logger = logging.getLogger(__name__)

type WebSocketFactory = Callable[..., AbstractAsyncContextManager[Any]]
DEFAULT_WEBSOCKET_FACTORY = cast(WebSocketFactory, websocket_connect)


class AliyunCosyVoiceRealtimeTtsProvider:
    """Translate the CosyVoice task protocol into the Runtime PCM stream contract."""

    kind = ALIYUN_COSYVOICE_TTS_PROVIDER_ID

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
        config = self._configurations.get_cosyvoice()
        return TtsProviderDescriptor(
            provider_id=self.kind,
            display_name="阿里云百炼 · CosyVoice 宁宁",
            model=config.model,
            languages=("zh", "ja", "en"),
            supports_voice_cloning=True,
            supports_style=True,
            supports_speed=True,
            supports_pitch=True,
            native_streaming=True,
            local_only=False,
        )

    async def health(self) -> TtsProviderHealth:
        config = self._configurations.get_cosyvoice()
        if not config.enabled:
            return TtsProviderHealth(
                status="unavailable",
                model_loaded=False,
                device="cloud",
                detail="请先在声音设置中启用 CosyVoice 实时语音",
            )
        if self._configurations.api_key(self.kind) is None:
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
            raise RuntimeError("CosyVoice realtime TTS ended without a completed result")
        return completed

    async def probe(self) -> dict[str, object]:
        with TemporaryDirectory(prefix="chatwaifu-cosyvoice-tts-probe-") as directory:
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
                    voice_id=self._configurations.get_cosyvoice().voice_id,
                    speaker_id=0,
                    speed=1.0,
                    style="温柔、自然地打招呼。",
                )
            )
        return {
            "status": "ok",
            "provider_id": self.kind,
            "sample_rate": result.sample_rate,
            "duration_ms": result.duration_ms,
        }

    async def stream(self, request: SynthesisRequest) -> AsyncIterator[TtsStreamEvent]:
        config = self._configurations.get_cosyvoice()
        api_key = self._configurations.api_key(self.kind)
        if not config.enabled:
            raise RuntimeError("阿里云百炼 CosyVoice 实时语音尚未启用")
        if api_key is None:
            raise RuntimeError("阿里云百炼 API Key 尚未配置")

        await self._validate_voice_binding(config, api_key)
        request.destination.parent.mkdir(parents=True, exist_ok=True)
        request.destination.unlink(missing_ok=True)
        headers = {
            "Authorization": f"Bearer {api_key}",
            "User-Agent": "ChatWaifu-NEXT/0.1",
        }
        if config.workspace_id:
            headers["X-DashScope-WorkSpace"] = config.workspace_id

        task_id = str(uuid4())
        started_at = monotonic()
        audio = bytearray()
        sequence = 0
        try:
            async with self._websocket_factory(
                config.websocket_base_url,
                additional_headers=headers,
                open_timeout=config.timeout_seconds,
                close_timeout=2,
                max_size=4_000_000,
            ) as websocket:
                await _send_task(
                    websocket,
                    "run-task",
                    task_id,
                    payload=_run_payload(config, request.style),
                )
                await _wait_for_task_event(
                    websocket,
                    task_id,
                    "task-started",
                    config.timeout_seconds,
                )
                await _send_task(
                    websocket,
                    "continue-task",
                    task_id,
                    payload={"input": {"text": request.text}},
                )
                await _send_task(
                    websocket,
                    "finish-task",
                    task_id,
                    payload={"input": {}},
                )

                try:
                    while True:
                        frame = await _receive_frame(websocket, config.timeout_seconds)
                        if isinstance(frame, bytes):
                            if len(frame) % 2:
                                raise RuntimeError("CosyVoice returned unaligned PCM16 audio")
                            if len(audio) + len(frame) > config.max_audio_bytes:
                                raise RuntimeError(
                                    "CosyVoice output exceeded the configured safety limit"
                                )
                            audio.extend(frame)
                            yield TtsPcmChunk(
                                sequence=sequence,
                                pcm16=frame,
                                sample_rate=config.sample_rate,
                                channels=1,
                                native_streaming=True,
                            )
                            sequence += 1
                            continue
                        event_type = _task_event_type(frame, task_id)
                        if event_type == "task-finished":
                            break
                except asyncio.CancelledError:
                    try:
                        await _send_task(
                            websocket,
                            "finish-task",
                            task_id,
                            payload={"input": {"directive": "cancel"}},
                        )
                    except Exception:
                        logger.debug(
                            "CosyVoice cancel directive could not be delivered task=%s",
                            task_id,
                            exc_info=True,
                        )
                    raise

            if not audio:
                raise RuntimeError("CosyVoice returned no completed audio")
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
            "CosyVoice realtime TTS completed generation=%s segment=%s "
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

    async def _validate_voice_binding(
        self,
        config: AliyunCosyVoiceTtsConfiguration,
        api_key: str,
    ) -> None:
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
        try:
            response = await self._http_client.post(
                config.voice_catalog_url,
                headers=headers,
                json={
                    "model": "voice-enrollment",
                    "input": {
                        "action": "query_voice",
                        "voice_id": config.voice_id,
                    },
                },
                timeout=config.timeout_seconds,
            )
            response.raise_for_status()
        except httpx2.HTTPStatusError as error:
            if error.response.status_code in {401, 403}:
                raise RuntimeError(
                    "百炼 API Key 无法访问当前地域的 CosyVoice 音色; 请确认 Key 与地域设置一致"
                ) from error
            raise RuntimeError(
                f"CosyVoice 音色元数据查询失败 (HTTP {error.response.status_code})"
            ) from error
        except httpx2.TimeoutException as error:
            raise RuntimeError("CosyVoice 音色元数据查询超时") from error
        except httpx2.HTTPError as error:
            raise RuntimeError("无法连接百炼 CosyVoice 音色元数据服务") from error

        output = _response_output(response)
        target_model = output.get("target_model")
        if target_model != config.model:
            raise RuntimeError(
                "CosyVoice 音色与模型不匹配: 该音色绑定的是 "
                f"{target_model or '未知模型'}，当前模型是 {config.model}"
            )
        status = output.get("status")
        if status != "OK":
            raise RuntimeError(f"CosyVoice 音色当前不可用: {status or '未知状态'}")
        self._validated_voice_signature = signature


def _run_payload(
    config: AliyunCosyVoiceTtsConfiguration,
    request_style: str | None,
) -> dict[str, object]:
    parameters: dict[str, object] = {
        "text_type": "PlainText",
        "voice": config.voice_id,
        "format": "pcm",
        "sample_rate": config.sample_rate,
        "volume": config.volume,
        "rate": config.speech_rate,
        "pitch": config.pitch_rate,
        "enable_ssml": False,
    }
    if config.language_type != "auto":
        parameters["language_hints"] = [config.language_type]
    instruction = _combine_instruction(config.instruction, request_style)
    if instruction:
        parameters["instruction"] = instruction
    return {
        "task_group": "audio",
        "task": "tts",
        "function": "SpeechSynthesizer",
        "model": config.model,
        "parameters": parameters,
        "input": {},
    }


async def _send_task(
    websocket: Any,
    action: str,
    task_id: str,
    *,
    payload: dict[str, object],
) -> None:
    await websocket.send(
        json.dumps(
            {
                "header": {
                    "action": action,
                    "task_id": task_id,
                    "streaming": "duplex",
                },
                "payload": payload,
            },
            ensure_ascii=False,
        )
    )


async def _wait_for_task_event(
    websocket: Any,
    task_id: str,
    expected: str,
    limit_seconds: float,
) -> dict[str, object]:
    while True:
        frame = await _receive_frame(websocket, limit_seconds)
        if isinstance(frame, bytes):
            raise RuntimeError("CosyVoice returned audio before task-started")
        if _task_event_type(frame, task_id) == expected:
            return frame


async def _receive_frame(websocket: Any, limit_seconds: float) -> bytes | dict[str, object]:
    async with asyncio.timeout(limit_seconds):
        raw = await websocket.recv()
    if isinstance(raw, bytes):
        return raw
    if not isinstance(raw, str):
        raise RuntimeError("CosyVoice returned an unsupported WebSocket frame")
    try:
        parsed: object = json.loads(raw)
    except json.JSONDecodeError as error:
        raise RuntimeError("CosyVoice returned invalid JSON") from error
    if not isinstance(parsed, dict):
        raise RuntimeError("CosyVoice returned an invalid event")
    return cast(dict[str, object], parsed)


def _task_event_type(event: dict[str, object], task_id: str) -> str:
    header = event.get("header")
    if not isinstance(header, dict):
        raise RuntimeError("CosyVoice event is missing its header")
    details = cast(dict[str, object], header)
    if details.get("task_id") != task_id:
        raise RuntimeError("CosyVoice returned an event for a different task")
    event_type = details.get("event")
    if not isinstance(event_type, str):
        raise RuntimeError("CosyVoice event is missing its type")
    if event_type == "task-failed":
        code = str(details.get("error_code", "provider_error"))
        message = str(details.get("error_message", "CosyVoice request failed"))
        raise RuntimeError(f"CosyVoice {code}: {message}")
    return event_type


def _combine_instruction(configured: str, request_style: str | None) -> str:
    parts = [item.strip() for item in (configured, request_style or "") if item.strip()]
    value = " ".join(parts)
    used = 0
    output: list[str] = []
    for character in value:
        units = 2 if "\u2e80" <= character <= "\u9fff" else 1
        if used + units > 100:
            break
        output.append(character)
        used += units
    return "".join(output)


def _response_output(response: httpx2.Response) -> dict[str, object]:
    try:
        payload: object = response.json()
    except ValueError as error:
        raise RuntimeError("CosyVoice 音色查询返回了非 JSON 数据") from error
    if not isinstance(payload, dict):
        raise RuntimeError("CosyVoice 音色查询返回了无效数据")
    output = cast(dict[str, object], payload).get("output")
    if not isinstance(output, dict):
        raise RuntimeError("CosyVoice 音色查询缺少 output")
    return cast(dict[str, object], output)


def _write_pcm_wave(destination: Path, pcm16: bytes, sample_rate: int, channels: int) -> None:
    with wave.open(str(destination), "wb") as output:
        output.setnchannels(channels)
        output.setsampwidth(2)
        output.setframerate(sample_rate)
        output.writeframes(pcm16)
