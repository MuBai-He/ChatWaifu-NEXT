"""Composition root and ordered Runtime lifecycle."""

import asyncio
import secrets
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import cast
from uuid import UUID

from chatwaifu_runtime import __version__
from chatwaifu_runtime.agent.tool_calling import AgentTurnOrchestrator
from chatwaifu_runtime.api.guard import WebSocketTicketStore
from chatwaifu_runtime.audio.store import AudioAssetStore
from chatwaifu_runtime.audio.streaming import AudioStreamHub
from chatwaifu_runtime.character_kernel.prompt import PromptCompiler
from chatwaifu_runtime.character_kernel.service import CharacterKernelService
from chatwaifu_runtime.characters.service import CharacterService
from chatwaifu_runtime.companion.activity import ActivityTracker
from chatwaifu_runtime.companion.ambient import AmbientCompanionService
from chatwaifu_runtime.companion.resources import ResourceLifecycleService
from chatwaifu_runtime.companion.settings import CompanionSettingsService
from chatwaifu_runtime.config.settings import Settings
from chatwaifu_runtime.conversation.service import ConversationService
from chatwaifu_runtime.eventing.hub import EventHub
from chatwaifu_runtime.eventing.publisher import EventPublisher
from chatwaifu_runtime.external_channels.adapters.weixin_ilink.client import WeixinILinkClient
from chatwaifu_runtime.external_channels.credentials import KeyringChannelCredentialStore
from chatwaifu_runtime.external_channels.management import ChannelManagementService
from chatwaifu_runtime.external_channels.service import ExternalChannelService
from chatwaifu_runtime.memory.semantic_index import SQLiteSemanticMemoryIndex
from chatwaifu_runtime.memory.service import MemoryService
from chatwaifu_runtime.persistence.database import Database
from chatwaifu_runtime.persistence.event_store import EventStore
from chatwaifu_runtime.persistence.sqlite_conversation import SQLiteConversationRepository
from chatwaifu_runtime.persistence.sqlite_experience_reset import SQLiteExperienceResetRepository
from chatwaifu_runtime.persistence.sqlite_external_channels import (
    SQLiteExternalChannelRepository,
)
from chatwaifu_runtime.persistence.sqlite_memory_repository import SQLiteMemoryRepository
from chatwaifu_runtime.persistence.sqlite_runtime_skills import SQLiteRuntimeSkillRepository
from chatwaifu_runtime.playback.service import PlaybackService
from chatwaifu_runtime.providers.factory import build_providers
from chatwaifu_runtime.providers.model_config import ModelConfigurationService
from chatwaifu_runtime.providers.tts_config import TtsConfigurationService
from chatwaifu_runtime.providers.tts_registry import TTS_PROVIDER_REGISTRATIONS
from chatwaifu_runtime.realtime.admission import RuntimeRealtimeTurnAdmission
from chatwaifu_runtime.realtime.cloud.context import CloudEgressGateway
from chatwaifu_runtime.realtime.cloud.factory import RuntimeCloudRealtimeFactory
from chatwaifu_runtime.realtime.cloud.fake import FakeCloudRealtimeBackend
from chatwaifu_runtime.realtime.cloud.media import CloudRealtimeMediaBridge
from chatwaifu_runtime.realtime.pipecat.session import PipecatMediaAdapter
from chatwaifu_runtime.realtime.service import VoiceMediaService
from chatwaifu_runtime.realtime.stt import build_stt_backend
from chatwaifu_runtime.runtime_skills.agent_router import RuntimeSkillRouter
from chatwaifu_runtime.runtime_skills.sandbox import RuntimeSandboxLauncher, SandboxPlanner
from chatwaifu_runtime.runtime_skills.service import RuntimeSkillService
from chatwaifu_runtime.sessions.service import SessionService

type AsyncCleanup = Callable[[], Awaitable[None]]


@dataclass(frozen=True, slots=True)
class _CleanupStep:
    name: str
    callback: AsyncCleanup


class RuntimeCleanupError(RuntimeError):
    def __init__(self, component: str, cause: Exception) -> None:
        super().__init__(f"{component} cleanup failed: {cause}")
        self.component = component
        self.__cause__ = cause


class RuntimeLifecycleError(ExceptionGroup):
    """Multiple Runtime lifecycle operations failed but cleanup still continued."""


class RuntimeContainer:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.capability_token = (
            settings.security.capability_token.get_secret_value()
            if settings.security.capability_token
            and settings.security.capability_token.get_secret_value().strip()
            else secrets.token_urlsafe(32)
        )
        self.ws_ticket_store = WebSocketTicketStore()
        self.database = Database(settings.database_path, settings.storage)
        self.event_hub = EventHub(settings.runtime.event_queue_size)
        self.event_store = EventStore(self.database)
        self.event_publisher = EventPublisher(self.event_store, self.event_hub)
        self.sessions = SessionService(self.database, self.event_store, self.event_hub)
        self.activity = ActivityTracker()
        self.companion_settings = CompanionSettingsService(self.database)
        self.model_configurations = ModelConfigurationService(self.database, settings)
        self.tts_configurations = TtsConfigurationService(
            self.database, settings, TTS_PROVIDER_REGISTRATIONS
        )
        self.providers = build_providers(
            settings,
            llm_override=self.model_configurations.chat,
            tts_configurations=self.tts_configurations,
        )
        self.audio_assets = AudioAssetStore(settings.data_dir / "audio")
        self.audio_streams = AudioStreamHub()
        self.characters = CharacterService(settings.characters_dir)
        self.character_kernel = CharacterKernelService(
            self.database, self.characters, self.event_publisher
        )
        self.prompt_compiler = PromptCompiler(self.model_configurations)
        self.memory_repository = SQLiteMemoryRepository(self.database)
        self.runtime_skill_repository = SQLiteRuntimeSkillRepository(self.database)
        self.semantic_memory_index = SQLiteSemanticMemoryIndex(
            self.database, self.model_configurations
        )
        self.memory = MemoryService(
            self.memory_repository,
            self.event_publisher,
            semantic_index=self.semantic_memory_index,
            models=self.model_configurations,
        )
        self.playback = PlaybackService(
            self.database,
            self.event_store,
            self.event_publisher,
        )
        self.conversation_repository = SQLiteConversationRepository(self.database, self.event_store)
        self.external_channel_repository = SQLiteExternalChannelRepository(
            self.database, self.event_store
        )
        self.experience_reset_repository = SQLiteExperienceResetRepository(
            self.database, self.event_store
        )
        self.stt = build_stt_backend(settings)
        sandbox_launcher = RuntimeSandboxLauncher(
            SandboxPlanner(
                windows_launcher=settings.security.windows_appcontainer_launcher,
                windows_state_dir=(
                    settings.data_dir / "runtime-skills" / "windows-appcontainer"
                ).resolve(),
            )
        )
        self.runtime_skills = RuntimeSkillService(
            settings.skills_dir,
            settings.data_dir,
            self.runtime_skill_repository,
            self.event_publisher,
            self.providers,
            self.stt.kind,
            __version__,
            sandbox_launcher=sandbox_launcher,
        )
        self.agent = AgentTurnOrchestrator(
            self.providers.llm,
            self.runtime_skills,
            RuntimeSkillRouter(self.runtime_skills.list),
        )
        self.conversation = ConversationService(
            self.conversation_repository,
            self.experience_reset_repository,
            self.event_publisher,
            self.sessions,
            self.providers,
            self.audio_assets,
            self.audio_streams,
            self.characters,
            self.memory,
            self.playback,
            self.character_kernel,
            self.prompt_compiler,
            self.agent,
        )
        self.external_channels = ExternalChannelService(
            self.external_channel_repository,
            self.conversation_repository,
            self.sessions,
            self.conversation,
            self.characters,
            self.event_hub,
            self.event_publisher,
        )
        self.channel_management = ChannelManagementService(
            self.external_channels,
            self.external_channel_repository,
            KeyringChannelCredentialStore(),
            WeixinILinkClient(),
            event_hub=self.event_hub,
            event_publisher=self.event_publisher,
        )
        self.resources = ResourceLifecycleService(
            self.companion_settings,
            self.activity,
            self.providers.tts,
            self.stt,
        )
        self.resources.set_busy_probe(
            lambda: self.conversation.active_count > 0 or self.providers.tts.active_jobs > 0
        )
        self.ambient = AmbientCompanionService(
            self.database,
            self.companion_settings,
            self.activity,
            self.sessions,
            self.conversation,
            self.event_publisher,
            self.resources.status,
            on_trigger=self.resources.touch,
        )
        cloud_bridge_factory: Callable[[UUID], Awaitable[CloudRealtimeMediaBridge]] | None = None
        self.cloud_realtime_backend: FakeCloudRealtimeBackend | None = None
        self.cloud_egress_gateway: CloudEgressGateway | None = None
        self.realtime_admission: RuntimeRealtimeTurnAdmission | None = None
        self.cloud_realtime_factory: RuntimeCloudRealtimeFactory | None = None

        if settings.realtime.connection_mode == "cloud_realtime":
            if settings.realtime.cloud_backend != "fake":
                raise ValueError(
                    f"Unsupported cloud realtime backend: {settings.realtime.cloud_backend}. "
                    "Phase 13.0-13.3 supports only 'fake'."
                )

            self.cloud_realtime_backend = FakeCloudRealtimeBackend()
            self.cloud_egress_gateway = CloudEgressGateway(
                policy_mode=settings.privacy.cloud_egress,
                event_store=self.event_store,
                event_hub=self.event_hub,
            )
            self.realtime_admission = RuntimeRealtimeTurnAdmission(self.conversation)
            self.cloud_realtime_factory = RuntimeCloudRealtimeFactory(
                backend=self.cloud_realtime_backend,
                egress_gateway=self.cloud_egress_gateway,
                conversation=self.conversation,
                sessions=self.sessions,
                admission=self.realtime_admission,
                characters=self.characters,
                character_kernel=self.character_kernel,
                memory=self.memory,
                skills_source=self.runtime_skills,
                event_hub=self.event_hub,
            )
            cloud_bridge_factory = self.cloud_realtime_factory.create_bridge

        self.voice_media = VoiceMediaService(
            PipecatMediaAdapter(
                config=settings.realtime,
                stt_config=settings.stt,
                publisher=self.event_publisher,
                event_hub=self.event_hub,
                conversation=self.conversation,
                audio_assets=self.audio_assets,
                stt=self.stt,
                companion_settings=self.companion_settings,
                activity=self.activity,
                resource_activity=self.resources.touch,
                cloud_bridge_factory=cloud_bridge_factory,
            )
        )
        self._state = "new"
        # Ownership begins at construction, not after successful startup. Several
        # adapters allocate HTTP clients and queues in __init__, so a late startup
        # failure must close every owned component, including ones with no start().
        self._cleanup_steps = self._shutdown_steps()
        self._lifecycle_lock = asyncio.Lock()

    async def start(self) -> None:
        async with self._lifecycle_lock:
            if self._state == "started":
                return
            if self._state != "new":
                raise RuntimeError(
                    "this RuntimeContainer is terminal after stop or failed startup; "
                    "construct a new container"
                )

            self._state = "starting"
            try:
                self.characters.start()
                self.audio_assets.start()

                await self.database.open()
                self.audio_assets.recover_staged_removals(
                    await self.experience_reset_repository.all_audio_asset_ids()
                )
                await self._drain_pending_outbox()
                await self.companion_settings.start()
                await self.model_configurations.start()
                await self.tts_configurations.start()
                await self.providers.tts.refresh_capabilities()

                await self.memory.start()
                await self.runtime_skills.start()
                await self.external_channels.start()
                await self.channel_management.start()
                await self.resources.start()
                await self.ambient.start()
            except BaseException as error:
                failed, cleanup_errors = await _drain_cleanup_steps(self._cleanup_steps)
                self._cleanup_steps = failed
                self._state = "failed" if failed else "stopped"
                if cleanup_errors:
                    _raise_lifecycle_group(
                        "Runtime start and rollback failed", error, cleanup_errors
                    )
                raise

            self._state = "started"

    async def _drain_pending_outbox(self, page_size: int = 100) -> None:
        """Republish every durable event left by an interrupted Runtime.

        Each row is marked only after the Hub accepted it. A failure therefore
        aborts startup with the failed row and the rest of the page still pending;
        constructing a fresh container safely resumes from that durable boundary.
        """

        while True:
            pending = await self.event_store.pending_outbox(limit=page_size)
            if not pending:
                return
            for event in pending:
                event_id = event.get("event_id")
                if not isinstance(event_id, str) or not event_id:
                    raise RuntimeError("pending outbox event is missing a valid event_id")
                await self.event_hub.publish(event)
                await self.event_store.mark_published(event_id)

    async def stop(self) -> None:
        async with self._lifecycle_lock:
            if self._state == "stopped":
                return
            self._state = "stopping"
            failed, errors = await _drain_cleanup_steps(self._cleanup_steps)
            self._cleanup_steps = failed
            self._state = "failed" if failed else "stopped"
            if errors:
                _raise_lifecycle_group("Runtime shutdown failed", None, errors)

    def _shutdown_steps(self) -> list[_CleanupStep]:
        steps = [
            _CleanupStep("ambient", lambda: self.ambient.stop()),
            _CleanupStep("resources", lambda: self.resources.stop()),
            _CleanupStep("voice_media", lambda: self.voice_media.close()),
        ]
        if self.cloud_realtime_backend is not None:
            backend = self.cloud_realtime_backend
            steps.append(_CleanupStep("cloud_realtime_backend", lambda: backend.close()))
        steps.extend(
            [
                _CleanupStep("channel_management", lambda: self.channel_management.stop()),
                _CleanupStep("external_channels", lambda: self.external_channels.stop()),
                _CleanupStep("conversation", lambda: self.conversation.stop()),
                _CleanupStep("runtime_skills", lambda: self.runtime_skills.stop()),
                _CleanupStep("memory", lambda: self.memory.stop()),
                _CleanupStep("stt", lambda: self.stt.close()),
                _CleanupStep("tts", lambda: self.providers.tts.close()),
                _CleanupStep("audio_streams", lambda: self.audio_streams.close()),
                _CleanupStep("event_hub", lambda: self.event_hub.close()),
                _CleanupStep("database", lambda: self.database.close()),
            ]
        )
        return steps


async def _drain_cleanup_steps(
    steps: list[_CleanupStep],
) -> tuple[list[_CleanupStep], list[BaseException]]:
    failed: list[_CleanupStep] = []
    errors: list[BaseException] = []
    for step in steps:
        try:
            await step.callback()
        except BaseException as error:
            failed.append(step)
            errors.append(
                RuntimeCleanupError(step.name, error) if isinstance(error, Exception) else error
            )
    return failed, errors


def _raise_lifecycle_group(
    message: str,
    primary: BaseException | None,
    cleanup_errors: list[BaseException],
) -> None:
    errors = ([primary] if primary is not None else []) + cleanup_errors
    if all(isinstance(error, Exception) for error in errors):
        raise RuntimeLifecycleError(message, cast(list[Exception], errors))
    raise BaseExceptionGroup(message, errors)
