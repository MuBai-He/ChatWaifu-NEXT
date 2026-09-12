"""SmallWebRTC signaling and per-connection Pipecat pipeline adapter."""

import asyncio
import logging
from collections.abc import Awaitable, Callable
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
from chatwaifu_runtime.companion.activity import ActivityTracker
from chatwaifu_runtime.companion.settings import CompanionSettingsService
from chatwaifu_runtime.config.settings import RealtimeConfig, SttConfig
from chatwaifu_runtime.conversation.service import ConversationService
from chatwaifu_runtime.eventing.hub import EventHub
from chatwaifu_runtime.eventing.publisher import EventPublisher
from chatwaifu_runtime.realtime.cloud.context import (
    CloudEgressGateway,
    ConsentRequiredError,
    EgressGrant,
    PolicyDeniedError,
)
from chatwaifu_runtime.realtime.cloud.media import CloudRealtimeMediaBridge
from chatwaifu_runtime.realtime.configuration import (
    RealtimeConfigurationService,
    RealtimeConnectionSnapshot,
)
from chatwaifu_runtime.realtime.contracts import SttBackend
from chatwaifu_runtime.realtime.pipecat.processor import VoiceDomainBridgeProcessor

_LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class WebRtcOffer:
    sdp: str
    type: str
    pc_id: str | None = None
    restart_pc: bool = False
    activation_mode: str = "push_to_talk"


@dataclass(frozen=True, slots=True)
class WebRtcCandidate:
    candidate: str
    sdp_mid: str
    sdp_mline_index: int


@dataclass(slots=True)
class _PcConnectionSnapshot:
    snapshot: RealtimeConnectionSnapshot
    bridge_factory: Callable[[UUID], Awaitable[CloudRealtimeMediaBridge]] | None


class PipecatMediaAdapter:
    def __init__(
        self,
        *,
        config: RealtimeConfig,
        stt_config: SttConfig,
        publisher: EventPublisher,
        event_hub: EventHub,
        conversation: ConversationService,
        audio_assets: AudioAssetStore,
        stt: SttBackend,
        companion_settings: CompanionSettingsService,
        activity: ActivityTracker,
        resource_activity: Callable[[], None],
        cloud_bridge_factory: Callable[[UUID], Awaitable[CloudRealtimeMediaBridge]] | None = None,
        configuration_service: RealtimeConfigurationService | None = None,
        egress_gateway: CloudEgressGateway | Callable[[], CloudEgressGateway] | None = None,
        bridge_factory_builder: (
            Callable[
                [RealtimeConnectionSnapshot],
                Callable[[UUID], Awaitable[CloudRealtimeMediaBridge]],
            ]
            | None
        ) = None,
    ) -> None:
        self._config = config
        self._stt_config = stt_config
        self._stt_language = (
            None if stt_config.language.strip().casefold() == "auto" else stt_config.language
        )
        self._publisher = publisher
        self._event_hub = event_hub
        self._conversation = conversation
        self._audio_assets = audio_assets
        self._stt = stt
        self._companion_settings = companion_settings
        self._activity = activity
        self._resource_activity = resource_activity
        self._cloud_bridge_factory = cloud_bridge_factory
        self._configuration_service = configuration_service
        self._egress_gateway = egress_gateway
        self._bridge_factory_builder = bridge_factory_builder
        self._handler = SmallWebRTCRequestHandler(connection_mode=ConnectionMode.MULTIPLE)
        self._tasks: dict[str, asyncio.Task[None]] = {}
        self._sessions: dict[str, UUID] = {}
        self._session_locks: dict[UUID, asyncio.Lock] = {}
        self._admin_lock = asyncio.Lock()
        self._session_lock_users: dict[UUID, int] = {}
        self._prepared_bridges: dict[str, CloudRealtimeMediaBridge] = {}
        self._pc_snapshots: dict[str, _PcConnectionSnapshot] = {}
        self._cleanup_tasks: set[asyncio.Task[None]] = set()
        self._connections: dict[str, SmallWebRTCConnection] = {}
        self._closing: bool = False

    @property
    def active_connections(self) -> int:
        return sum(not task.done() for task in self._tasks.values())

    def _capture_snapshot(self) -> RealtimeConnectionSnapshot:
        if self._configuration_service is not None:
            return self._configuration_service.current_snapshot()
        api_key_secret = self._config.openai.api_key
        raw_key = api_key_secret.get_secret_value() if api_key_secret else None
        return RealtimeConnectionSnapshot(
            schema_version="1.0",
            revision=1,
            connection_mode=self._config.connection_mode,
            cloud_backend=self._config.cloud_backend or "openai",
            model=self._config.openai.model or "",
            voice=self._config.openai.voice,
            transcription_model=self._config.openai.transcription_model,
            cloud_tools_enabled=self._config.cloud_tools_enabled,
            cloud_egress_consent=True,
            api_key_configured=bool(raw_key),
            _api_key=raw_key,
        )

    def _get_egress_gateway(self) -> CloudEgressGateway | None:
        if callable(self._egress_gateway):
            return self._egress_gateway()
        return self._egress_gateway

    def _validate_admission(self, snapshot: RealtimeConnectionSnapshot, session_id: UUID) -> None:
        if snapshot.connection_mode != "cloud_realtime":
            return
        if snapshot.cloud_backend == "openai":
            if not snapshot.model.strip():
                raise ValueError("云端语音配置无效，未配置模型。")
            if not snapshot.api_key or not snapshot.api_key.strip():
                raise ValueError("云端语音配置无效，未配置 API Key。")
        if not snapshot.cloud_egress_consent:
            raise PermissionError("云端语音未获授权，请检查出网设置。")
        egress_gw = self._get_egress_gateway()
        if egress_gw is not None:
            if egress_gw.policy_mode == "deny":
                raise PermissionError("云端语音未获授权，请检查出网设置。")
            if egress_gw.policy_mode == "ask":
                allowed_kinds = {
                    "safety",
                    "persona",
                    "relationship",
                    "affect",
                    "memory",
                    "skills",
                    "recent_history",
                }
                if snapshot.cloud_tools_enabled:
                    allowed_kinds.add("tool_result")
                egress_gw.grant_consent(
                    EgressGrant(
                        session_id=session_id,
                        backend_id=snapshot.cloud_backend,
                        allowed_component_kinds=frozenset(allowed_kinds),
                    )
                )

    def _resolve_bridge_factory(
        self, snapshot: RealtimeConnectionSnapshot
    ) -> Callable[[UUID], Awaitable[CloudRealtimeMediaBridge]] | None:
        if snapshot.connection_mode == "cascade":
            return None
        bootstrap_key = self._config.openai.api_key
        bootstrap_matches = (
            snapshot.connection_mode == self._config.connection_mode
            and snapshot.cloud_backend == (self._config.cloud_backend or "openai")
            and snapshot.model == (self._config.openai.model or "")
            and snapshot.voice == self._config.openai.voice
            and snapshot.transcription_model == self._config.openai.transcription_model
            and snapshot.cloud_tools_enabled == self._config.cloud_tools_enabled
            and snapshot.api_key == (bootstrap_key.get_secret_value() if bootstrap_key else None)
        )
        if snapshot.revision == 1 and bootstrap_matches and self._cloud_bridge_factory is not None:
            return self._cloud_bridge_factory
        if self._bridge_factory_builder is not None:
            return self._bridge_factory_builder(snapshot)
        return self._cloud_bridge_factory

    async def _get_session_lock(self, session_id: UUID) -> asyncio.Lock:
        async with self._admin_lock:
            if session_id not in self._session_locks:
                self._session_locks[session_id] = asyncio.Lock()
            self._session_lock_users[session_id] = self._session_lock_users.get(session_id, 0) + 1
            return self._session_locks[session_id]

    async def _maybe_cleanup_session_lock(self, session_id: UUID) -> None:
        async with self._admin_lock:
            users = self._session_lock_users.get(session_id, 1) - 1
            if users:
                self._session_lock_users[session_id] = users
            else:
                self._session_lock_users.pop(session_id, None)
                self._session_locks.pop(session_id, None)

    def _track_cleanup(self, operation: Awaitable[None]) -> asyncio.Task[None]:
        async def run() -> None:
            await operation

        task = asyncio.create_task(run())
        self._cleanup_tasks.add(task)

        def finished(done: asyncio.Task[None]) -> None:
            self._cleanup_tasks.discard(done)
            if not done.cancelled():
                error = done.exception()
                if error is not None:
                    _LOGGER.error("Realtime cleanup failed: %s", type(error).__name__)

        task.add_done_callback(finished)
        return task

    async def _join_cleanup(self, operation: Awaitable[None]) -> None:
        task = self._track_cleanup(operation)
        done, _ = await asyncio.wait({task}, timeout=2.0)
        if not done:
            task.cancel()
            raise TimeoutError("Realtime cleanup has not finished")
        await task

    async def _prepare_cloud_bridge(
        self,
        session_id: UUID,
        bridge_factory: Callable[[UUID], Awaitable[CloudRealtimeMediaBridge]] | None,
    ) -> CloudRealtimeMediaBridge:
        if bridge_factory is None:
            raise ValueError("Cloud realtime is not configured")
        try:
            async with asyncio.timeout(15.0):
                return await bridge_factory(session_id)
        except (ConsentRequiredError, PolicyDeniedError) as error:
            raise PermissionError("云端语音未获授权，请检查出网设置。") from error
        except Exception as error:
            if str(error) in {
                "openai_realtime_authentication_failed",
                "openai_realtime_configuration_missing",
                "openai_realtime_output_format_unsupported",
            }:
                raise ValueError("云端语音配置无效，请检查模型和凭据设置。") from error
            raise ConnectionError("云端语音暂时无法连接，请稍后重试。") from error

    async def offer(self, session_id: UUID, offer: WebRtcOffer) -> dict[str, str]:
        # Pipecat offer captures ONE immutable config revision before await
        snapshot = self._capture_snapshot()

        session_lock = await self._get_session_lock(session_id)
        try:
            async with session_lock:
                if self._closing:
                    raise RuntimeError("PipecatMediaAdapter is closed or closing")

                # Validate connection ownership inside the lock
                if offer.pc_id is not None:
                    owner = self._sessions.get(offer.pc_id)
                    if owner != session_id:
                        raise ValueError(
                            f"Connection {offer.pc_id} belongs to session {owner}, not {session_id}"
                        )
                    is_renegotiation = bool(
                        offer.pc_id in self._tasks
                        and owner == session_id
                        and not self._tasks[offer.pc_id].done()
                    )
                else:
                    is_renegotiation = False

                if is_renegotiation:
                    assert offer.pc_id is not None
                    pc_record = self._pc_snapshots.get(offer.pc_id)
                    if pc_record is not None:
                        snapshot = pc_record.snapshot
                        bridge_factory = pc_record.bridge_factory
                    else:
                        bridge_factory = self._resolve_bridge_factory(snapshot)
                else:
                    self._validate_admission(snapshot, session_id)
                    bridge_factory = self._resolve_bridge_factory(snapshot)

                if not is_renegotiation:
                    # Serialized admission: supersede previous connections for this session
                    existing_for_session = [
                        (pc_id, task)
                        for pc_id, task in list(self._tasks.items())
                        if self._sessions.get(pc_id) == session_id
                        and pc_id != offer.pc_id
                        and not task.done()
                    ]
                    for old_pc_id, task in existing_for_session:
                        _LOGGER.info(
                            "Superseding previous connection %s for session %s with new offer",
                            old_pc_id,
                            session_id,
                        )
                        task.cancel()
                        done, _ = await asyncio.wait({task}, timeout=2.0)
                        if task not in done:
                            _LOGGER.error(
                                "Superseded task %s for session %s failed to terminate; "
                                "failing closed",
                                old_pc_id,
                                session_id,
                            )
                            raise RuntimeError(
                                f"Superseded task {old_pc_id} for session {session_id} "
                                "failed to terminate within timeout"
                            )

                # Canceled pipeline tasks can leave bounded cleanup operations still running.
                # Do not admit replacement media until those operations have actually exited.
                if self._cleanup_tasks:
                    _, pending = await asyncio.wait(self._cleanup_tasks, timeout=2.0)
                    if pending:
                        raise TimeoutError("Previous realtime cleanup is still running")

                request = SmallWebRTCRequest(
                    sdp=offer.sdp,
                    type=offer.type,
                    pc_id=offer.pc_id,
                    restart_pc=offer.restart_pc,
                    request_data={"session_id": str(session_id)},
                )

                created_tasks: list[asyncio.Task[None]] = []
                created_connections: list[SmallWebRTCConnection] = []
                prepared: CloudRealtimeMediaBridge | None = None
                if not is_renegotiation and snapshot.connection_mode == "cloud_realtime":
                    prepared = await self._prepare_cloud_bridge(session_id, bridge_factory)

                async def start_connection(connection: SmallWebRTCConnection) -> None:
                    created_connections.append(connection)
                    if self._closing:
                        raise RuntimeError("Realtime adapter closed during admission")
                    self._pc_snapshots[connection.pc_id] = _PcConnectionSnapshot(
                        snapshot=snapshot, bridge_factory=bridge_factory
                    )
                    if prepared is not None:
                        self._prepared_bridges[connection.pc_id] = prepared
                    if connection.pc_id in self._tasks and not self._tasks[connection.pc_id].done():
                        return
                    task = asyncio.create_task(
                        self._run_connection(session_id, connection, offer.activation_mode),
                        name=f"webrtc-{connection.pc_id}",
                    )
                    created_tasks.append(task)
                    self._connections[connection.pc_id] = connection
                    self._tasks[connection.pc_id] = task
                    self._sessions[connection.pc_id] = session_id
                    task.add_done_callback(
                        lambda _task, p_id=connection.pc_id, t=task: self._discard(p_id, t)
                    )

                try:
                    answer = await self._handler.handle_web_request(request, start_connection)
                    if answer is None:
                        raise RuntimeError("WebRTC signaling did not produce an answer")
                    if self._closing:
                        raise RuntimeError("Realtime adapter closed during admission")
                    return answer
                except BaseException:
                    try:
                        for task in created_tasks:
                            task.cancel()
                        if created_tasks:
                            await asyncio.wait(created_tasks, timeout=2.0)
                        elif prepared is not None:
                            await self._join_cleanup(prepared.cleanup())
                    finally:
                        for connection in created_connections:
                            self._pc_snapshots.pop(connection.pc_id, None)
                            await self._join_cleanup(connection.disconnect())
                    raise
        finally:
            await self._maybe_cleanup_session_lock(session_id)

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

    async def close_session(self, session_id: UUID, pc_id: str | None = None) -> int:
        session_lock = await self._get_session_lock(session_id)
        try:
            async with session_lock:
                if pc_id is not None:
                    # Safe targeted closure: only close if pc_id belongs to this session_id.
                    if self._sessions.get(pc_id) != session_id:
                        return 0
                    task = self._tasks.get(pc_id)
                    if task is not None and not task.done():
                        task.cancel()
                        await asyncio.wait({task}, timeout=2.0)
                        return 1
                    return 0

                tasks = [
                    task
                    for p_id, task in list(self._tasks.items())
                    if self._sessions.get(p_id) == session_id and not task.done()
                ]
                for task in tasks:
                    task.cancel()
                if tasks:
                    await asyncio.wait(tasks, timeout=2.0)
                return len(tasks)
        finally:
            await self._maybe_cleanup_session_lock(session_id)

    async def close(self) -> None:
        self._closing = True
        tasks = [task for task in list(self._tasks.values()) if not task.done()]
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.wait(tasks, timeout=2.0)
        if any(not task.done() for task in tasks):
            raise TimeoutError("Realtime connections are still closing")
        await self._join_cleanup(self._handler.close())
        if self._cleanup_tasks:
            _, pending = await asyncio.wait(self._cleanup_tasks, timeout=2.0)
            if pending:
                raise TimeoutError("Realtime cleanup tasks are still closing")
        self._tasks.clear()
        self._sessions.clear()
        self._pc_snapshots.clear()

    async def _run_connection(
        self,
        session_id: UUID,
        connection: SmallWebRTCConnection,
        activation_mode: str,
    ) -> None:
        cloud_bridge: CloudRealtimeMediaBridge | None = None
        worker: PipelineWorker | None = None
        watcher_task: asyncio.Task[None] | None = None

        pc_record = self._pc_snapshots.get(connection.pc_id)
        current_snapshot = pc_record.snapshot if pc_record is not None else self._capture_snapshot()
        current_bridge_factory = (
            pc_record.bridge_factory
            if pc_record is not None
            else self._resolve_bridge_factory(current_snapshot)
        )

        async def _safe_close_connection() -> None:
            await self._join_cleanup(connection.disconnect())

        try:
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
            if current_snapshot.connection_mode == "cloud_realtime":
                if current_bridge_factory is None:
                    raise RuntimeError(
                        "Cloud realtime mode enabled but no cloud_bridge_factory provided"
                    )
                cloud_bridge = self._prepared_bridges.pop(connection.pc_id, None)
                if cloud_bridge is None:
                    cloud_bridge = await current_bridge_factory(session_id)
                pipeline = Pipeline([transport.input(), vad, cloud_bridge, transport.output()])
            else:
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
                    stt_language=self._stt_language,
                    companion_settings=self._companion_settings,
                    activity=self._activity,
                    resource_activity=self._resource_activity,
                    activation_mode=activation_mode,
                )
                pipeline = Pipeline([transport.input(), vad, bridge, transport.output()])

            worker = PipelineWorker(
                pipeline,
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

            if cloud_bridge is not None:
                bridge_ref = cloud_bridge

                async def watch_cloud_termination() -> None:
                    await bridge_ref.closed_event.wait()
                    _LOGGER.info(
                        "Cloud bridge terminated for pc_id=%s session=%s; closing transport",
                        connection.pc_id,
                        session_id,
                    )
                    try:
                        await self._join_cleanup(worker.cancel(reason="cloud_bridge_terminated"))
                    finally:
                        await _safe_close_connection()

                watcher_task = asyncio.create_task(
                    watch_cloud_termination(),
                    name=f"webrtc-term-{connection.pc_id}",
                )

            await runner.run()
        finally:
            if watcher_task is not None and not watcher_task.done():
                watcher_task.cancel()
                await asyncio.gather(watcher_task, return_exceptions=True)
            try:
                if worker is not None:
                    await self._join_cleanup(worker.cancel(reason="run_connection_cleanup"))
            finally:
                try:
                    bridge = cloud_bridge or self._prepared_bridges.pop(connection.pc_id, None)
                    if bridge is not None:
                        await self._join_cleanup(bridge.cleanup())
                finally:
                    await _safe_close_connection()

    def _discard(self, pc_id: str, task: asyncio.Task[None] | None = None) -> None:
        if task is not None and self._tasks.get(pc_id) is not task:
            return
        if task is not None and task.done() and not task.cancelled():
            error = task.exception()
            if error is not None:
                _LOGGER.error("Realtime connection %s ended: %s", pc_id, type(error).__name__)
        self._tasks.pop(pc_id, None)
        self._sessions.pop(pc_id, None)
        self._pc_snapshots.pop(pc_id, None)
        bridge = self._prepared_bridges.pop(pc_id, None)
        connection = self._connections.pop(pc_id, None)
        if bridge is not None:
            self._track_cleanup(bridge.cleanup())
        if connection is not None:
            self._track_cleanup(connection.disconnect())
