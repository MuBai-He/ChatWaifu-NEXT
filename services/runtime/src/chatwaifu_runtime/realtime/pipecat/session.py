"""SmallWebRTC signaling and per-connection Pipecat pipeline adapter."""

import asyncio
from dataclasses import dataclass
from uuid import UUID

from pipecat.audio.vad.silero import SileroVADAnalyzer
from pipecat.audio.vad.vad_analyzer import VADParams
from pipecat.pipeline.pipeline import Pipeline
from pipecat.pipeline.worker import PipelineParams, PipelineWorker
from pipecat.processors.audio.vad_processor import VADProcessor
from pipecat.transports.base_transport import TransportParams
from pipecat.transports.smallwebrtc.connection import SmallWebRTCConnection
from pipecat.transports.smallwebrtc.request_handler import (
    ConnectionMode,
    IceCandidate,
    SmallWebRTCPatchRequest,
    SmallWebRTCRequest,
    SmallWebRTCRequestHandler,
)
from pipecat.transports.smallwebrtc.transport import SmallWebRTCTransport
from pipecat.workers.runner import WorkerRunner

from chatwaifu_runtime.audio.store import AudioAssetStore
from chatwaifu_runtime.config.settings import RealtimeConfig
from chatwaifu_runtime.conversation.service import ConversationService
from chatwaifu_runtime.eventing.hub import EventHub
from chatwaifu_runtime.eventing.publisher import EventPublisher
from chatwaifu_runtime.realtime.contracts import SttBackend
from chatwaifu_runtime.realtime.pipecat.processor import VoiceDomainBridgeProcessor


@dataclass(frozen=True, slots=True)
class WebRtcOffer:
    sdp: str
    type: str
    pc_id: str | None = None
    restart_pc: bool = False


@dataclass(frozen=True, slots=True)
class WebRtcCandidate:
    candidate: str
    sdp_mid: str
    sdp_mline_index: int


class PipecatMediaAdapter:
    def __init__(
        self,
        *,
        config: RealtimeConfig,
        publisher: EventPublisher,
        event_hub: EventHub,
        conversation: ConversationService,
        audio_assets: AudioAssetStore,
        stt: SttBackend,
    ) -> None:
        self._config = config
        self._publisher = publisher
        self._event_hub = event_hub
        self._conversation = conversation
        self._audio_assets = audio_assets
        self._stt = stt
        self._handler = SmallWebRTCRequestHandler(connection_mode=ConnectionMode.MULTIPLE)
        self._tasks: dict[str, asyncio.Task[None]] = {}
        self._sessions: dict[str, UUID] = {}

    @property
    def active_connections(self) -> int:
        return sum(not task.done() for task in self._tasks.values())

    async def offer(self, session_id: UUID, offer: WebRtcOffer) -> dict[str, str]:
        request = SmallWebRTCRequest(
            sdp=offer.sdp,
            type=offer.type,
            pc_id=offer.pc_id,
            restart_pc=offer.restart_pc,
            request_data={"session_id": str(session_id)},
        )

        async def start_connection(connection: SmallWebRTCConnection) -> None:
            if connection.pc_id in self._tasks:
                return
            task = asyncio.create_task(
                self._run_connection(session_id, connection),
                name=f"webrtc-{connection.pc_id}",
            )
            self._tasks[connection.pc_id] = task
            self._sessions[connection.pc_id] = session_id
            task.add_done_callback(lambda _task, pc_id=connection.pc_id: self._discard(pc_id))

        answer = await self._handler.handle_web_request(request, start_connection)
        if answer is None:
            raise RuntimeError("WebRTC signaling did not produce an answer")
        return answer

    async def patch(self, pc_id: str, candidates: list[WebRtcCandidate]) -> None:
        await self._handler.handle_patch_request(
            SmallWebRTCPatchRequest(
                pc_id=pc_id,
                candidates=[
                    IceCandidate(
                        candidate=item.candidate,
                        sdp_mid=item.sdp_mid,
                        sdp_mline_index=item.sdp_mline_index,
                    )
                    for item in candidates
                ],
            )
        )

    async def close_session(self, session_id: UUID) -> int:
        tasks = [
            task
            for pc_id, task in self._tasks.items()
            if self._sessions.get(pc_id) == session_id and not task.done()
        ]
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        return len(tasks)

    async def close(self) -> None:
        tasks = [task for task in self._tasks.values() if not task.done()]
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self._tasks.clear()
        self._sessions.clear()
        await self._handler.close()

    async def _run_connection(
        self,
        session_id: UUID,
        connection: SmallWebRTCConnection,
    ) -> None:
        transport = SmallWebRTCTransport(
            webrtc_connection=connection,
            params=TransportParams(
                audio_in_enabled=True,
                audio_out_enabled=True,
                audio_in_sample_rate=self._config.input_sample_rate,
                audio_out_sample_rate=self._config.output_sample_rate,
                audio_in_channels=1,
                audio_out_channels=1,
                audio_out_auto_silence=True,
            ),
        )
        vad = VADProcessor(
            vad_analyzer=SileroVADAnalyzer(
                sample_rate=self._config.input_sample_rate,
                params=VADParams(
                    confidence=self._config.vad_confidence,
                    start_secs=self._config.vad_start_ms / 1000,
                    stop_secs=self._config.vad_stop_ms / 1000,
                ),
            )
        )
        bridge = VoiceDomainBridgeProcessor(
            session_id=session_id,
            sample_rate=self._config.input_sample_rate,
            channels=1,
            pre_roll_ms=self._config.pre_roll_ms,
            max_utterance_seconds=self._config.max_utterance_seconds,
            echo_enabled=self._config.echo_enabled,
            publisher=self._publisher,
            event_hub=self._event_hub,
            conversation=self._conversation,
            audio_assets=self._audio_assets,
            stt=self._stt,
        )
        worker = PipelineWorker(
            Pipeline([transport.input(), vad, bridge, transport.output()]),
            params=PipelineParams(
                audio_in_sample_rate=self._config.input_sample_rate,
                audio_out_sample_rate=self._config.output_sample_rate,
            ),
            enable_rtvi=False,
            idle_timeout_secs=None,
        )

        @transport.event_handler("on_client_disconnected")
        async def on_client_disconnected(_transport: object, _client: object) -> None:
            await worker.cancel(reason="webrtc_client_disconnected")

        _ = on_client_disconnected

        runner = WorkerRunner(handle_sigint=False, handle_sigterm=False)
        await runner.add_workers(worker)
        await runner.run()

    def _discard(self, pc_id: str) -> None:
        self._tasks.pop(pc_id, None)
        self._sessions.pop(pc_id, None)
