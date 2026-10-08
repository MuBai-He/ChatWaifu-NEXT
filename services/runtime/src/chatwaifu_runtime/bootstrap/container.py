"""Composition root and ordered Runtime lifecycle."""

import asyncio
import json
import logging
import secrets
import shutil
import sys
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import cast
from uuid import UUID, uuid4

from chatwaifu_protocol.agent import (
    AgentEvent,
    AgentTask,
    AgentTaskCreate,
    CandidateCreate,
    CapabilityStatus,
    DecisionRecord,
    GroupAutonomyPolicy,
    TaskAuthorization,
    TaskChannelBinding,
)
from chatwaifu_protocol.base import JsonObject
from chatwaifu_protocol.channel_settings import ChannelRuntimePolicy
from chatwaifu_protocol.channels import ChannelInboundTextMessage
from pydantic import SecretStr

from chatwaifu_runtime import __version__
from chatwaifu_runtime.agent.artifacts import ArtifactService
from chatwaifu_runtime.agent.behavior import BehaviorDecisionService
from chatwaifu_runtime.agent.capabilities import CapabilityCatalog
from chatwaifu_runtime.agent.development import CandidateDevelopmentService
from chatwaifu_runtime.agent.task_skills import TaskSkills
from chatwaifu_runtime.agent.tasks import AgentTaskService
from chatwaifu_runtime.agent.tool_calling import AgentTurnOrchestrator
from chatwaifu_runtime.agent.workspace import WorkspaceSkills
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
from chatwaifu_runtime.config.settings import OpenAIRealtimeConfig, Settings
from chatwaifu_runtime.conversation.service import ConversationService
from chatwaifu_runtime.eventing.hub import EventHub
from chatwaifu_runtime.eventing.publisher import EventPublisher
from chatwaifu_runtime.external_channels.adapters.qq_napcat.catalog import catalog_versions
from chatwaifu_runtime.external_channels.adapters.qq_napcat.management import NapCatManagement
from chatwaifu_runtime.external_channels.adapters.qq_napcat.registration import (
    NAPCAT_PROVIDER,
    channel_tool_policy,
)
from chatwaifu_runtime.external_channels.adapters.weixin_ilink.client import WeixinILinkClient
from chatwaifu_runtime.external_channels.autonomy import GroupAutonomyService, GroupObservation
from chatwaifu_runtime.external_channels.credentials import KeyringChannelCredentialStore
from chatwaifu_runtime.external_channels.encrypted_credentials import (
    EncryptedFileChannelCredentialStore,
)
from chatwaifu_runtime.external_channels.files import ChannelFileSkill
from chatwaifu_runtime.external_channels.group_memory import GroupMemoryBridge
from chatwaifu_runtime.external_channels.group_models import (
    ChannelGroupInboundDescriptor,
    ChannelGroupRouteRecord,
)
from chatwaifu_runtime.external_channels.groups import ChannelGroupService
from chatwaifu_runtime.external_channels.management import ChannelManagementService
from chatwaifu_runtime.external_channels.models import (
    ChannelConnectionRecord,
    ChannelDeliveryPlanRecord,
    ChannelTurnRecord,
)
from chatwaifu_runtime.external_channels.proactive import ChannelProactiveService
from chatwaifu_runtime.external_channels.public_web import ChannelPublicWebPolicy
from chatwaifu_runtime.external_channels.qq_account import QQAccountCapabilities
from chatwaifu_runtime.external_channels.qq_capabilities import QQSceneCapabilities
from chatwaifu_runtime.external_channels.service import (
    WEIXIN_ILINK_PROVIDER,
    ExternalChannelService,
)
from chatwaifu_runtime.external_channels.settings import ChannelSettingsService
from chatwaifu_runtime.external_channels.stickers import PresetStickerCatalog
from chatwaifu_runtime.external_channels.voice import ChannelVoiceSkill
from chatwaifu_runtime.index_orchestration.service import IndexRebuildService
from chatwaifu_runtime.memory.semantic_index import SQLiteSemanticMemoryIndex
from chatwaifu_runtime.memory.service import MemoryService
from chatwaifu_runtime.memory.spoken_observer import SpokenMemoryObserver
from chatwaifu_runtime.persistence.database import Database
from chatwaifu_runtime.persistence.event_store import EventStore
from chatwaifu_runtime.persistence.sqlite_agent_artifacts import SQLiteArtifactRepository
from chatwaifu_runtime.persistence.sqlite_agent_behavior import SQLiteBehaviorRepository
from chatwaifu_runtime.persistence.sqlite_agent_development import SQLiteDevelopmentRepository
from chatwaifu_runtime.persistence.sqlite_agent_tasks import SQLiteAgentTaskRepository
from chatwaifu_runtime.persistence.sqlite_assistant_tasks import SQLiteTaskRepository
from chatwaifu_runtime.persistence.sqlite_channel_groups import SQLiteChannelGroupRepository
from chatwaifu_runtime.persistence.sqlite_channel_proactive import SQLiteChannelProactiveRepository
from chatwaifu_runtime.persistence.sqlite_channel_settings import SQLiteChannelSettingsRepository
from chatwaifu_runtime.persistence.sqlite_conversation import SQLiteConversationRepository
from chatwaifu_runtime.persistence.sqlite_experience_reset import SQLiteExperienceResetRepository
from chatwaifu_runtime.persistence.sqlite_external_channels import (
    SQLiteExternalChannelRepository,
)
from chatwaifu_runtime.persistence.sqlite_interaction_diagnostics import (
    SQLiteInteractionDiagnosticsReader,
)
from chatwaifu_runtime.persistence.sqlite_memory_repository import SQLiteMemoryRepository
from chatwaifu_runtime.persistence.sqlite_personal_assistant import SQLiteAssistantRepository
from chatwaifu_runtime.persistence.sqlite_photo_memory import SQLitePhotoMemoryRepository
from chatwaifu_runtime.persistence.sqlite_photo_semantic import SQLitePhotoSemanticAdapter
from chatwaifu_runtime.persistence.sqlite_runtime_skills import SQLiteRuntimeSkillRepository
from chatwaifu_runtime.persistence.sqlite_spoken_memory import SQLiteSpokenMemoryRepository
from chatwaifu_runtime.persistence.sqlite_sticker_library import SqliteStickerLibraryRepository
from chatwaifu_runtime.persistence.sqlite_sticker_usage import SQLiteStickerUsageRepository
from chatwaifu_runtime.personal_assistant.agenda_skill import (
    AgendaManageSkill,
    GoogleTasksReadSkill,
)
from chatwaifu_runtime.personal_assistant.integration import PersonalAssistantIntegration
from chatwaifu_runtime.personal_assistant.organizer_skill import OrganizerSkill
from chatwaifu_runtime.personal_assistant.skill import CalendarReadSkill
from chatwaifu_runtime.photo_memory.annotations import PhotoAnnotationService
from chatwaifu_runtime.photo_memory.classifier import PhotoClassifier
from chatwaifu_runtime.photo_memory.observer import PhotoMemoryObserver
from chatwaifu_runtime.photo_memory.recall import PhotoRecallService
from chatwaifu_runtime.photo_memory.semantic import PhotoSemanticService
from chatwaifu_runtime.playback.service import PlaybackService
from chatwaifu_runtime.providers.contracts import LlmProvider
from chatwaifu_runtime.providers.factory import build_providers
from chatwaifu_runtime.providers.model_config import ModelConfigurationService
from chatwaifu_runtime.providers.tts_config import TtsConfigurationService
from chatwaifu_runtime.providers.tts_registry import TTS_PROVIDER_REGISTRATIONS
from chatwaifu_runtime.realtime.admission import RuntimeRealtimeTurnAdmission
from chatwaifu_runtime.realtime.cloud.context import CloudEgressGateway
from chatwaifu_runtime.realtime.cloud.contracts import CloudRealtimeBackend
from chatwaifu_runtime.realtime.cloud.factory import RuntimeCloudRealtimeFactory
from chatwaifu_runtime.realtime.cloud.fake import FakeCloudRealtimeBackend
from chatwaifu_runtime.realtime.cloud.media import CloudRealtimeMediaBridge
from chatwaifu_runtime.realtime.cloud.openai import OpenAIRealtimeBackend, SocketConnector
from chatwaifu_runtime.realtime.configuration import (
    RealtimeConfigurationService,
    RealtimeConnectionSnapshot,
)
from chatwaifu_runtime.realtime.pipecat.session import PipecatMediaAdapter
from chatwaifu_runtime.realtime.service import VoiceMediaService
from chatwaifu_runtime.realtime.stt import build_stt_backend
from chatwaifu_runtime.runtime_skills.adapters import GenerationSkillContext
from chatwaifu_runtime.runtime_skills.agent_router import RuntimeSkillRouter
from chatwaifu_runtime.runtime_skills.execution_context import authorized_task
from chatwaifu_runtime.runtime_skills.sandbox import RuntimeSandboxLauncher, SandboxPlanner
from chatwaifu_runtime.runtime_skills.service import RuntimeSkillService
from chatwaifu_runtime.sessions.service import SessionService
from chatwaifu_runtime.sticker_library.classifier import StickerClassifier
from chatwaifu_runtime.sticker_library.service import StickerLibraryService
from chatwaifu_runtime.sticker_library.usage import StickerUsageRepository

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
    def __init__(self, settings: Settings, *, source_answer_frames: bool = False) -> None:
        self.settings = settings
        self.capability_token = (
            settings.security.capability_token.get_secret_value()
            if settings.security.capability_token
            and settings.security.capability_token.get_secret_value().strip()
            else secrets.token_urlsafe(32)
        )
        self.ws_ticket_store = WebSocketTicketStore()
        self.database = Database(settings.database_path, settings.storage)
        self.personal_assistant = PersonalAssistantIntegration(
            settings, SQLiteAssistantRepository(self.database), SQLiteTaskRepository(self.database)
        )
        self.event_hub = EventHub(settings.runtime.event_queue_size)
        self.event_store = EventStore(self.database)
        self.interaction_diagnostics = SQLiteInteractionDiagnosticsReader(self.database)
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
        self.channel_proactive_repository = SQLiteChannelProactiveRepository(
            self.database, self.event_store, deliveries=self.external_channel_repository
        )
        self.channel_group_repository = SQLiteChannelGroupRepository(
            self.database, self.event_store
        )
        self.channel_settings = ChannelSettingsService(
            SQLiteChannelSettingsRepository(self.database),
            ChannelRuntimePolicy(
                qq_owner_public_web_enabled=settings.public_web.qq_owner_reads_enabled,
                group_discussion=settings.group_discussion,
            ),
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
        self.channel_voice = ChannelVoiceSkill(
            self.external_channel_repository,
            self.conversation_repository,
            self.characters,
            self.providers.tts,
            self.event_publisher,
            settings.data_dir / "channel-audio",
            self._channel_active_generation,
            self._channel_supports_audio,
            private_voice_enabled=lambda: (
                self.channel_settings.get().policy.qq_owner_voice_reply_enabled
            ),
        )
        self.artifacts = ArtifactService(
            settings.data_dir / "agent-artifacts",
            SQLiteArtifactRepository(self.database),
            self.sessions,
        )
        self.workspace_skills = WorkspaceSkills(
            settings.data_dir / "agent-workspace",
            self.artifacts,
            settings.skills_dir,
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
            mcp_private_origins=settings.security.mcp_private_origins,
            public_web_config=settings.public_web,
            authorized_generation_handlers={"channel_voice": self.channel_voice},
            shared_generation_handler_targets=frozenset({"channel_voice"}),
            generation_permission_policy=ChannelPublicWebPolicy(
                self.external_channel_repository,
                self._channel_active_generation,
                enabled=lambda: self.channel_settings.get().policy.qq_owner_public_web_enabled,
            ),
            session_builtin_handlers={
                "workspace_list": self.workspace_skills.list,
                "workspace_read": self.workspace_skills.read,
                "workspace_write": self.workspace_skills.write,
                "artifact_inspect": self.workspace_skills.inspect,
                "document_word": self.workspace_skills.word,
                "document_powerpoint": self.workspace_skills.powerpoint,
                "calendar_read": CalendarReadSkill(self.personal_assistant),
                "agenda_manage": AgendaManageSkill(self.personal_assistant),
                "google_tasks_read": GoogleTasksReadSkill(self.personal_assistant),
                **{
                    name: OrganizerSkill(self.personal_assistant, name)
                    for name in (
                        "organizer_read",
                        "schedule_create",
                        "schedule_change",
                        "schedule_update",
                        "apple_read",
                        "apple_manage",
                    )
                },
            },
        )
        self.capabilities = CapabilityCatalog(
            self.runtime_skills.list,
            self.runtime_skills.instructions,
            availability=self._agent_capability_availability,
        )
        self.agent_tasks = AgentTaskService(
            SQLiteAgentTaskRepository(self.database),
            self.sessions,
            self.runtime_skills,
            self.capabilities,
            self.providers.llm,
            model_factory=self._agent_model,
            context_builder=self._agent_task_context,
        )
        self.runtime_skills.task_permission_policy = self.agent_tasks.authorize
        self.agent_development = CandidateDevelopmentService(
            SQLiteDevelopmentRepository(self.database),
            self.artifacts,
            self.runtime_skills,
            settings.data_dir / "agent-candidates",
            sandbox_launcher or RuntimeSandboxLauncher(),
            self._agent_model,
        )
        self.runtime_skills.register_session_builtin(
            "agent_develop_candidate", self.agent_development.create_skill
        )
        task_skills = TaskSkills(self.agent_tasks)
        self.runtime_skills.register_session_builtin("agent_task_create", task_skills.create)
        self.runtime_skills.register_session_builtin("agent_task_status", task_skills.status)
        self.runtime_skills.register_session_builtin("agent_task_defer", task_skills.defer)
        self.agent = AgentTurnOrchestrator(
            self.providers.llm,
            self.runtime_skills,
            RuntimeSkillRouter(self.runtime_skills.list),
            catalog=self.capabilities,
        )
        self.photo_repository = SQLitePhotoMemoryRepository(self.database)
        self.photo_semantic_adapter = SQLitePhotoSemanticAdapter(self.database)
        self.photo_semantic = PhotoSemanticService(
            self.photo_semantic_adapter,
            self.model_configurations,
        )
        self.index_rebuild = IndexRebuildService(
            models=self.model_configurations,
            memory_repository=self.memory_repository,
            semantic_memory_index=self.semantic_memory_index,
            photo_repository=self.photo_repository,
            photo_semantic=self.photo_semantic,
            photo_semantic_adapter=self.photo_semantic_adapter,
        )
        self.photo_annotations = PhotoAnnotationService(
            self.photo_repository, self.model_configurations, self.photo_semantic
        )
        self.photo_observer = PhotoMemoryObserver(
            self.photo_repository,
            PhotoClassifier(self.providers.llm),
            semantic_service=self.photo_semantic,
            annotations=self.photo_annotations,
        )
        self.spoken_memory_repository = SQLiteSpokenMemoryRepository(self.database)
        self.memory.set_spoken_repository(self.spoken_memory_repository)
        self.spoken_memory_observer = SpokenMemoryObserver(
            sessions=self.sessions,
            memory=self.memory,
            event_hub=self.event_hub,
            repository=self.spoken_memory_repository,
            playback=self.playback,
        )
        self.photo_recall = PhotoRecallService(
            self.photo_repository,
            semantic_service=self.photo_semantic,
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
            photo_recall=self.photo_recall,
            photo_annotations=self.photo_annotations,
            models=self.model_configurations,
            source_context=self.runtime_skills,
            source_answer_frames=source_answer_frames,
        )
        self.sticker_repository = SqliteStickerLibraryRepository(self.database)
        self.sticker_usage: StickerUsageRepository = SQLiteStickerUsageRepository(self.database)
        self.sticker_library = StickerLibraryService(
            self.sticker_repository, StickerClassifier(self.providers.llm), usage=self.sticker_usage
        )
        self.sticker_catalog = PresetStickerCatalog()
        self.external_channels = ExternalChannelService(
            self.external_channel_repository,
            self.conversation_repository,
            self.sessions,
            self.conversation,
            self.characters,
            self.event_hub,
            self.event_publisher,
            providers=(WEIXIN_ILINK_PROVIDER, NAPCAT_PROVIDER),
            tool_policy=lambda configuration, text: channel_tool_policy(
                configuration,
                text,
                public_web_enabled=self.channel_settings.get().policy.qq_owner_public_web_enabled,
                voice_reply_enabled=self.channel_settings.get().policy.qq_owner_voice_reply_enabled,
                owner_skill_ids=(
                    frozenset(
                        s.skill_id
                        for s in self.runtime_skills.list()
                        if s.skill_id != "channel.voice"
                    )
                    if self.channel_settings.get().policy.qq_owner_agent_enabled
                    else frozenset[str]()
                )
                | (
                    frozenset({"qq.scene", "qq.account"})
                    if self.channel_settings.get().policy.qq_account_enabled
                    else frozenset[str]()
                ),
            ),
            qq_voice_input_enabled=lambda: (
                self.channel_settings.get().policy.qq_owner_voice_input_enabled
            ),
            sticker_catalog=self.sticker_catalog,
            sticker_library=self.sticker_library,
            photo_observer=self.photo_observer,
        )
        self.channel_proactive = ChannelProactiveService(
            self.channel_proactive_repository,
            self.conversation,
            self.conversation_repository,
            self.external_channels,
            self.event_publisher,
        )
        self.external_channels.set_proactive_service(self.channel_proactive)
        self.channel_groups = ChannelGroupService(
            self.channel_group_repository,
            self.external_channel_repository,
            self.conversation,
            self.sessions,
            self.event_publisher,
            conversation_repository=self.conversation_repository,
            sticker_library=self.sticker_library,
            discussion_policy=self.channel_settings.get().policy.group_discussion,
        )
        self.channel_groups.set_authenticator(self.external_channels.authenticate_group_transport)
        self.behavior_decisions = BehaviorDecisionService(self.providers.llm, self._agent_model)
        self.external_channels.response_decider = self._qq_response_decision
        self.agent_tasks.wake_decider = self._task_wake_decision
        self.group_autonomy = GroupAutonomyService(
            SQLiteBehaviorRepository(self.database), self.behavior_decisions, self.characters
        )
        self.channel_groups.observation_handler = self.group_autonomy.observe
        self.channel_groups.autonomous_admission = self.group_autonomy.repository.mark_turn
        self.channel_groups.autonomous_authorization = self.group_autonomy.repository.authorize_turn
        self.group_autonomy.revoke_actions = self.channel_groups.revoke_autonomous
        self.group_memory = GroupMemoryBridge(self.sessions, self.memory, self.event_publisher)
        self.group_autonomy.context_reader = self._group_working_context
        self.group_autonomy.memory_writer = self.group_memory.observe
        self.external_channels.set_group_service(self.channel_groups)
        self.channel_voice.set_group_service(self.channel_groups)
        self.conversation.set_before_scope_reset_hook(self.channel_groups.before_scope_reset)
        self.channel_credentials = (
            EncryptedFileChannelCredentialStore(
                settings.data_dir / "channel-vault",
                settings.config_dir / "channel-vault-key",
            )
            if settings.channel_credential_backend == "encrypted_file"
            else KeyringChannelCredentialStore()
        )
        self.channel_management = ChannelManagementService(
            self.external_channels,
            self.external_channel_repository,
            self.channel_credentials,
            WeixinILinkClient(),
            sticker_catalog=self.sticker_catalog,
            sticker_library=self.sticker_library,
            photo_observer=self.photo_observer,
            event_hub=self.event_hub,
            event_publisher=self.event_publisher,
        )
        self.qq_channels = NapCatManagement(
            self.external_channels,
            self.external_channel_repository,
            self.channel_credentials,
            self.characters,
            self.event_publisher,
            self.event_hub,
            self.channel_voice.audio_root,
            self._channel_plan_terminal,
            sticker_catalog=self.sticker_catalog,
            sticker_library=self.sticker_library,
            stt_backend=self.stt,
            proactive_authorization=self.external_channels.authorize_proactive_delivery,
            proactive_on_terminal=self.external_channels.proactive_delivery_terminal,
            groups=self.channel_groups,
            private_voice_enabled=lambda: (
                self.channel_settings.get().policy.qq_owner_voice_reply_enabled
            ),
            native_favorites_enabled=lambda: (
                self.channel_settings.get().policy.qq_native_favorites_enabled
            ),
            artifacts=self.artifacts,
            catalog_versions=catalog_versions(settings.skills_dir),
        )
        self.qq_channels.free_chat_enabled = lambda: (
            self.channel_settings.get().policy.qq_free_chat_enabled
        )
        self.qq_channels.group_free_chat = self._qq_group_free_chat
        self.qq_scene_capabilities = QQSceneCapabilities(
            self.external_channel_repository,
            self.conversation_repository,
            self.channel_groups,
            self._channel_active_generation,
            self.qq_channels.scoped_agent_call,
        )
        self.qq_scene_capabilities.scene_enabled = self._qq_agent_scene_enabled
        self.qq_scene_capabilities.task_reader = self.agent_tasks.repository.get
        self.qq_scene_capabilities.binding_authorizer = self._authorize_task_channel
        self.agent_tasks.scene_authorizer = self._authorize_task_channel
        self.qq_channels.task_authorization = self._authorize_task_delivery
        task_skills.scene = self.qq_scene_capabilities
        self.runtime_skills.scene_session_policy = self._scene_session_skills
        self.group_autonomy.task_creator = self._group_task
        self.runtime_skills.register_generation_handler(
            "qq_scene", self.qq_scene_capabilities, shared=True
        )
        self.qq_account_capabilities = QQAccountCapabilities(
            self.qq_scene_capabilities,
            lambda: self.channel_settings.get().policy.qq_account_enabled,
            self.qq_channels.account_agent_call,
            settings.skills_dir / "builtin" / "qq-account" / "openapi.json",
        )
        self.runtime_skills.register_generation_handler(
            "qq_account", self.qq_account_capabilities, shared=True
        )
        self.channel_files = ChannelFileSkill(
            self.external_channel_repository,
            self.artifacts,
            self.qq_scene_capabilities,
            self.event_publisher,
            lambda: self.channel_settings.get().policy.qq_owner_agent_enabled,
        )
        self.runtime_skills.register_generation_handler(
            "channel_file", self.channel_files, shared=True
        )
        self.channel_groups.scene_skill_policy = self.group_agent_skills
        self.channel_settings.set_apply_callback(self._apply_channel_policy)
        self.resources = ResourceLifecycleService(
            self.companion_settings,
            self.activity,
            self.providers.tts,
            self.stt,
        )
        self.resources.set_busy_probe(
            lambda: (
                self.conversation.active_count > 0
                or self.providers.tts.active_jobs > 0
                or self.external_channels.active_preprocessing_count > 0
                or self.channel_groups.active_count > 0
            )
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
            session_allowed=self._desktop_proactive_session_allowed,
        )
        self.ambient.model_decider = self._ambient_decision
        self.agent_tasks.result_publisher = self.channel_files.publish_task_result
        self.personal_assistant.task_calendar_scope = self._task_calendar_scope
        self.agent_tasks.capability_gap_handler = self._task_capability_gap
        cloud_bridge_factory: Callable[[UUID], Awaitable[CloudRealtimeMediaBridge]] | None = None
        self.cloud_realtime_backend: CloudRealtimeBackend | None = None
        self.cloud_egress_gateway: CloudEgressGateway | None = None
        self.realtime_admission: RuntimeRealtimeTurnAdmission | None = None
        self.cloud_realtime_factory: RuntimeCloudRealtimeFactory | None = None
        self.cloud_backend_connector: SocketConnector | None = None
        self.realtime_configuration = RealtimeConfigurationService(
            self.database,
            settings,
        )

        if settings.realtime.connection_mode == "cloud_realtime":
            self.cloud_egress_gateway = CloudEgressGateway(
                policy_mode=settings.privacy.cloud_egress,
                event_store=self.event_store,
                event_hub=self.event_hub,
            )
            self.realtime_admission = RuntimeRealtimeTurnAdmission(self.conversation)
            self.cloud_realtime_backend = (
                OpenAIRealtimeBackend(settings.realtime.openai)
                if settings.realtime.cloud_backend == "openai"
                else FakeCloudRealtimeBackend()
            )
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
                tools_enabled=settings.realtime.cloud_tools_enabled,
                event_hub=self.event_hub,
                playback=self.playback,
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
                configuration_service=self.realtime_configuration,
                egress_gateway=self._get_or_create_egress_gateway,
                bridge_factory_builder=self._build_cloud_bridge_factory_for_snapshot,
            )
        )
        self._state = "new"
        # Ownership begins at construction, not after successful startup. Several
        # adapters allocate HTTP clients and queues in __init__, so a late startup
        # failure must close every owned component, including ones with no start().
        self._cleanup_steps = self._shutdown_steps()
        self._lifecycle_lock = asyncio.Lock()

    async def _apply_channel_policy(
        self, previous: ChannelRuntimePolicy, current: ChannelRuntimePolicy
    ) -> None:
        self.channel_groups.configure_discussion(current.group_discussion)
        if previous.qq_free_chat_enabled != current.qq_free_chat_enabled:
            await self.external_channels.cancel_response_decisions()
        if previous.qq_owner_voice_input_enabled and not current.qq_owner_voice_input_enabled:
            await self.external_channels.revoke_qq_voice_input()

    async def _qq_group_free_chat(self, descriptor: ChannelGroupInboundDescriptor) -> bool:
        if not self.channel_settings.get().policy.qq_free_chat_enabled:
            return False
        route = await self.channel_group_repository.find_route(
            descriptor.connection_id, descriptor.group_id
        )
        if route is None or not route.enabled or route.deleted_at is not None:
            return False
        policy = await self.group_autonomy.policy(route)
        return policy.mode == "member" and policy.route_revision == route.revision

    async def _qq_response_decision(
        self,
        connection: ChannelConnectionRecord,
        message: ChannelInboundTextMessage,
        turn: ChannelTurnRecord,
    ) -> bool:
        if connection.configuration.provider_id != "qq_napcat":
            return True
        before = self.channel_settings.get()
        if not before.policy.qq_free_chat_enabled:
            return True
        profile = self.characters.get(connection.configuration.character_id)
        if profile is None:
            raise ValueError("character unavailable")
        ref = message.external_message_id
        history = await self.conversation_repository.recent_history(
            turn.session_id, turn.turn_id, limit=6
        )
        decision = await self.behavior_decisions.decide(
            profile.system_prompt,
            {
                "chat_type": "owner_private",
                "recent_history": [{"role": h.role, "text": h.text[:1200]} for h in history],
                "messages": [
                    {"source_ref": ref, "speaker": message.sender_key, "text": message.text}
                ],
                "available_actions": ["wait", "respond", "clarify"],
                "reply_policy": (
                    "Choose whether to reply to this owner message or QQ event. "
                    "A poke is an event, not a mandatory request. Respect silence and avoid "
                    "automatic acknowledgements. Respond includes performing an ordinary QQ "
                    "action with tools during the reply, such as poking back."
                ),
            },
            frozenset({ref}),
        )
        if self.channel_settings.get() != before:
            return False
        logging.getLogger(__name__).info(
            "agent.private_decision generation=%s action=%s", turn.generation_id, decision.action
        )
        return decision.action in {"respond", "clarify"}

    async def group_agent_skills(self, route: ChannelGroupRouteRecord) -> frozenset[str]:
        policy = await self.group_autonomy.policy(route)
        return (
            frozenset(
                {"qq.scene", "agent.tasks", "workspace.files", "documents.create", "channel.file"}
            )
            if policy.mode != "off" and policy.route_revision == route.revision
            else frozenset[str]()
        ) | (
            frozenset({"qq.scene", "qq.account"})
            if self.channel_settings.get().policy.qq_account_enabled
            else frozenset[str]()
        )

    async def _qq_agent_scene_enabled(self, turn: ChannelTurnRecord) -> bool:
        if turn.group_route_id is None:
            return (
                self.channel_settings.get().policy.qq_owner_agent_enabled
                or self.channel_settings.get().policy.qq_account_enabled
            )
        route = await self.channel_group_repository.get_route(turn.group_route_id)
        return route is not None and "qq.scene" in await self.group_agent_skills(route)

    async def _scene_session_skills(self, context: GenerationSkillContext, skill_id: str) -> bool:
        turn = await self.qq_scene_capabilities.current(context)
        if turn is None or turn.group_route_id is None:
            return False
        route = await self.channel_group_repository.get_route(turn.group_route_id)
        return route is not None and skill_id in await self.group_agent_skills(route)

    async def _authorize_task_channel(self, binding: TaskChannelBinding) -> bool:
        connection = await self.external_channel_repository.get_connection(binding.connection_id)
        if (
            connection is None
            or not connection.configuration.enabled
            or connection.configuration.provider_id != "qq_napcat"
            or connection.configuration.account_key != binding.account_key
        ):
            return False
        if binding.route_id is None:
            return (
                self.channel_settings.get().policy.qq_owner_agent_enabled
                and connection.configuration.allowed_sender_keys == [binding.sender_key]
                and binding.conversation_key == "direct:" + binding.sender_key
            )
        route = await self.channel_group_repository.get_route(binding.route_id)
        if route is None:
            return False
        policy = await self.group_autonomy.policy(route)
        return (
            policy.mode != "off"
            and policy.route_revision == binding.route_revision
            and (
                binding.policy_revision is None
                or (policy.mode == "member" and policy.revision == binding.policy_revision)
            )
            and await self.channel_groups.authorize_task_binding(binding)
        )

    async def _authorize_task_delivery(self, plan: ChannelDeliveryPlanRecord) -> bool:
        target = plan.task_target
        if target is None:
            return False
        task = await self.agent_tasks.repository.get(target.task_id)
        return bool(
            task
            and task.session_id == target.session_id
            and task.channel_binding == target.binding
            and (
                (
                    target.purpose == "result"
                    and task.state.value
                    in {"succeeded", "failed", "waiting_input", "waiting_authorization"}
                )
                or (
                    target.purpose == "file"
                    and task.state.value in {"running", "waiting_authorization"}
                    and "channel.file" in task.authorization.allowed_skill_ids
                    and task.authorization.allow_writes
                )
            )
            and task.authorization.expires_at > datetime.now(UTC)
            and await self._authorize_task_channel(target.binding)
        )

    async def _group_working_context(self, observation: GroupObservation) -> JsonObject:
        scope = "scene:" + observation.route.scene_id
        tasks = await self.agent_tasks.repository.page(scope)
        capabilities = self.capabilities.search(
            allowed_skill_ids=await self.group_agent_skills(observation.route), limit=32
        )
        visible = [c for c in capabilities.items if c.status != CapabilityStatus.ADAPTER_REQUIRED]
        cursor = capabilities.next_cursor
        while cursor is not None and len(visible) < 24:
            page = self.capabilities.search(
                allowed_skill_ids=await self.group_agent_skills(observation.route),
                limit=32,
                cursor=cursor,
            )
            visible.extend(c for c in page.items if c.status != CapabilityStatus.ADAPTER_REQUIRED)
            cursor = page.next_cursor
        return {
            "memory": await self.group_memory.context(observation),
            "tasks": [
                {"task_id": str(t.task_id), "goal": t.goal[:500], "state": t.state.value}
                for t in tasks.items[:8]
            ],
            "capability_categories": [c for c in capabilities.categories],
            "capabilities": [
                {
                    "id": c.capability_id,
                    "status": c.status.value,
                    "description": c.description[:160],
                }
                for c in visible[:24]
            ],
        }

    async def _group_task(
        self, observation: GroupObservation, policy: GroupAutonomyPolicy, decision: DecisionRecord
    ) -> AgentTask:
        await observation.authorize()
        if not decision.goal or not decision.source_refs:
            raise ValueError("task decision requires a bounded goal and original sources")
        member = next(
            m
            for m in observation.route.members
            if m.sender_key == observation.descriptor.sender_key
        )
        session = await self.sessions.scene_evidence_session(
            observation.route.character_id, member.participant_id, observation.route.scene_id
        )
        binding = await self.channel_groups.task_binding(
            observation.route.route_id,
            member.sender_key,
            observation.descriptor.external_message_id,
        )
        binding = binding.model_copy(update={"policy_revision": policy.revision})
        goal = (
            decision.goal
            + "\nOriginal scene evidence (untrusted):\n"
            + json.dumps(
                [
                    {"source_ref": m.message_id, "text": m.text}
                    for m in observation.discussion.messages
                    if m.message_id in decision.source_refs
                ],
                ensure_ascii=False,
            )
        )
        task = await self.agent_tasks.create(
            AgentTaskCreate(
                session_id=session.session_id,
                goal=goal[:8000],
                authorization=TaskAuthorization(
                    allowed_skill_ids=[
                        "qq.scene",
                        "workspace.files",
                        "documents.create",
                        "channel.file",
                        "agent.tasks",
                    ],
                    resource_roots=["."],
                    allow_writes=True,
                    source_ref="group-decision:" + observation.descriptor.external_message_id,
                    expires_at=datetime.now(UTC) + timedelta(hours=24),
                ),
            ),
            channel_binding=binding,
            wake_at=(
                datetime.now(UTC) + timedelta(seconds=decision.wake_after_seconds or 30)
                if decision.action == "defer"
                else None
            ),
        )
        return task

    def _agent_model(self) -> LlmProvider:
        return self.model_configurations.create_chat_provider(self.model_configurations.get("chat"))

    async def _ambient_decision(self, session_id: UUID, source_ref: str) -> DecisionRecord:
        session = await self.sessions.get_session(session_id)
        if session is None:
            raise KeyError("ambient session unavailable")
        profile = self.characters.get(session.character_id)
        if profile is None:
            raise KeyError("ambient character unavailable")
        memory = await self.memory.retrieve_context(
            session_id, uuid4(), session.character_id, "当前约定、偏好和未完成事项"
        )
        tasks = await self.agent_tasks.repository.page(session.user_scope)
        return await self.behavior_decisions.decide(
            profile.system_prompt,
            {
                "scene": "desktop",
                "source_ref": source_ref,
                "trigger": "new activity became idle",
                "memory": memory.model_dump(mode="json"),
                "tasks": [t.model_dump(mode="json") for t in tasks.items[-8:]],
                "available_actions": ["wait", "respond", "clarify"],
            },
            frozenset({source_ref}),
        )

    async def _task_calendar_scope(self) -> frozenset[str] | None:
        task_id = authorized_task.get()
        if task_id is None:
            return None
        task = await self.agent_tasks.repository.get(task_id)
        if task is None or task.authorization.expires_at <= datetime.now(UTC):
            raise PermissionError("task calendar authorization expired")
        return (
            frozenset(task.authorization.calendar_ids) if task.authorization.calendar_ids else None
        )

    async def _task_capability_gap(self, task: AgentTask, missing: str) -> UUID | None:
        if task.scope != "local" or not (await self.agent_development.repository.policy()).enabled:
            return None
        try:
            candidate = await self.agent_development.create(
                CandidateCreate(
                    session_id=task.session_id,
                    source_ref=f"task:{task.task_id}",
                    goal=("Missing capability: " + missing + "\nAuthorized goal: " + task.goal)[
                        :4000
                    ],
                )
            )
        except PermissionError:
            return None
        return candidate.candidate_id

    async def _task_wake_decision(self, task: AgentTask, event: AgentEvent) -> DecisionRecord:
        source_refs = frozenset(event.source_refs)
        if not self._agent_model().supports_tool_calling:
            return DecisionRecord(
                action="task", reason="explicit due task", source_refs=list(source_refs)
            )
        persona, memory = await self._agent_task_context(task)
        return await self.behavior_decisions.decide(
            persona,
            {
                "trigger": "authorized task wake",
                "event": event.model_dump(mode="json"),
                "task": task.model_dump(mode="json"),
                "memory": memory,
                "operations": json.dumps(await self.agent_tasks.repository.steps(task.task_id)),
                "available_actions": ["task", "wait", "defer", "capability_gap"],
                "instruction": "Continue the goal unless evidence gives a reason to wait.",
            },
            source_refs,
        )

    def _agent_capability_availability(
        self, skill_id: str, capability: str
    ) -> tuple[CapabilityStatus, str] | None:
        if skill_id == "qq.account" and not self.channel_settings.get().policy.qq_account_enabled:
            return CapabilityStatus.NOT_CONFIGURED, "尚未启用角色 QQ 账号操作权限"
        if skill_id == "qq.scene" and capability == "read_file" and getattr(sys, "frozen", False):
            return CapabilityStatus.NOT_CONFIGURED, "打包版尚未配置资料解析工作进程"
        if skill_id == "qq.scene" and capability not in {
            "get_msg",
            "get_group_info",
            "get_group_root_files",
            "get_group_file_system_info",
            "get_group_files_by_folder",
            "get_group_file_url",
            "get_group_msg_history",
            "read_file",
        }:
            return (
                CapabilityStatus.ADAPTER_REQUIRED,
                "已导入官方接口;仍需审核对象作用域或实现专用适配",
            )
        if skill_id == "documents.create":
            if getattr(sys, "frozen", False):
                return CapabilityStatus.NOT_CONFIGURED, "打包版尚未配置独立文档工作进程"
            if capability == "powerpoint" and shutil.which("node") is None:
                return CapabilityStatus.NOT_CONFIGURED, "运行主机缺少 Node.js 文档工作进程"
            if shutil.which("soffice") is None:
                return CapabilityStatus.NOT_CONFIGURED, "运行主机未配置文档渲染器，无法完成渲染验证"
        if (
            skill_id in {"calendar.read", "google-tasks.read", "agenda.manage"}
            and self.personal_assistant.state != "ready"
        ):
            return CapabilityStatus.NOT_CONFIGURED, "个人助理账户连接尚未配置"
        if (
            skill_id in {"qq.scene", "qq.account", "channel.file"}
            and not self.qq_channels.agent_available
        ):
            return CapabilityStatus.NOT_CONFIGURED, "QQ 尚无已连接的通道;操作还需要当前场景授权"
        if skill_id == "qq.account":
            return CapabilityStatus.AVAILABLE, "已由账号所有者启用 QQ 账号级操作权限"
        return None

    def _channel_supports_audio(self, provider_id: str) -> bool:
        return any(
            item.provider_id == provider_id and "audio" in item.capabilities.outbound_message_kinds
            for item in self.external_channels.providers()
        )

    async def _channel_plan_terminal(self, plan: ChannelDeliveryPlanRecord) -> None:
        await self.channel_voice.on_plan_terminal(plan)
        await self.channel_files.on_terminal(plan)

    async def _agent_task_context(self, task: AgentTask) -> tuple[str, str]:
        session = await self.sessions.get_session(task.session_id)
        if session is None:
            raise KeyError("task session no longer available")
        profile = self.characters.get(session.character_id)
        if profile is None:
            raise KeyError("task character unavailable")
        packet = await self.memory.retrieve_context(
            task.session_id, uuid4(), session.character_id, task.goal
        )
        return profile.system_prompt, json.dumps(
            packet.model_dump(mode="json"), ensure_ascii=False
        )[:8000]

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
                await self.channel_settings.start()
                await self.personal_assistant.start()
                self.audio_assets.recover_staged_removals(
                    await self.experience_reset_repository.all_audio_asset_ids()
                )
                await self._drain_pending_outbox()
                await self.companion_settings.start()
                await self.model_configurations.start()
                await self.tts_configurations.start()
                await self.realtime_configuration.start()
                await self.providers.tts.refresh_capabilities()

                await self.memory.start()
                await self.runtime_skills.start()
                await self.agent_development.start()
                await self.agent_tasks.start()
                self.sticker_library.start()
                await self.photo_semantic.sync_epoch()
                self.photo_semantic.start()
                self.photo_annotations.start()
                self.photo_observer.start()
                await self.spoken_memory_observer.start()
                await self.channel_groups.start()
                await self.group_autonomy.start()
                await self.external_channels.start()
                await self.channel_voice.cleanup()
                await self.channel_management.start()
                await self.qq_channels.start()
                await self.channel_proactive.start()
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

    async def _desktop_proactive_session_allowed(self, session_id: UUID) -> bool:
        session = await self.sessions.get_session(session_id)
        if (
            session is not None
            and session.scene_id is not None
            and await self.channel_group_repository.is_group_scene(session.scene_id)
        ):
            # A route owns its scene before the first sender binding exists.
            # Historical and paused scenes retain this exclusion after resets.
            return False
        return not await self.channel_proactive_repository.is_channel_session(session_id)

    def _channel_active_generation(self, session_id: UUID) -> UUID | None:
        return self.conversation.active_generation_id(session_id)

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
            _CleanupStep("personal_assistant", lambda: self.personal_assistant.close()),
            _CleanupStep("ambient", lambda: self.ambient.stop()),
            _CleanupStep("agent_tasks", lambda: self.agent_tasks.stop()),
            _CleanupStep("agent_development", lambda: self.agent_development.stop()),
            _CleanupStep("group_autonomy", lambda: self.group_autonomy.stop()),
            _CleanupStep("channel_proactive", lambda: self.channel_proactive.stop()),
            _CleanupStep("resources", lambda: self.resources.stop()),
            _CleanupStep("voice_media", lambda: self.voice_media.close()),
        ]
        if self.cloud_realtime_backend is not None:
            backend = self.cloud_realtime_backend
            steps.append(_CleanupStep("cloud_realtime_backend", lambda: backend.close()))
        steps.extend(
            [
                _CleanupStep("qq_channels", lambda: self.qq_channels.stop()),
                _CleanupStep("channel_groups", lambda: self.channel_groups.stop()),
                _CleanupStep("channel_management", lambda: self.channel_management.stop()),
                _CleanupStep("sticker_library", lambda: self.sticker_library.stop()),
                _CleanupStep("spoken_memory_observer", lambda: self.spoken_memory_observer.stop()),
                _CleanupStep("photo_observer", lambda: self.photo_observer.stop()),
                _CleanupStep("photo_annotations", lambda: self.photo_annotations.stop()),
                _CleanupStep("index_rebuild", lambda: self.index_rebuild.stop()),
                _CleanupStep("photo_semantic", lambda: self.photo_semantic.stop()),
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

    def _create_cloud_backend_for_snapshot(
        self, snapshot: RealtimeConnectionSnapshot
    ) -> CloudRealtimeBackend:
        if snapshot.cloud_backend == "fake":
            return FakeCloudRealtimeBackend()
        connector: SocketConnector | None = cast(
            SocketConnector | None,
            getattr(self.cloud_realtime_backend, "_connector", None)
            or getattr(self, "cloud_backend_connector", None),
        )
        api_key_secret = SecretStr(snapshot.api_key) if snapshot.api_key else None
        openai_config = OpenAIRealtimeConfig(
            model=snapshot.model or None,
            api_key=api_key_secret,
            voice=snapshot.voice,
            transcription_model=snapshot.transcription_model,
        )
        if connector is not None:
            return OpenAIRealtimeBackend(openai_config, connector=connector)
        return OpenAIRealtimeBackend(openai_config)

    def _get_or_create_egress_gateway(self) -> CloudEgressGateway:
        if self.cloud_egress_gateway is None:
            self.cloud_egress_gateway = CloudEgressGateway(
                policy_mode=self.settings.privacy.cloud_egress,
                event_store=self.event_store,
                event_hub=self.event_hub,
            )
        return self.cloud_egress_gateway

    def _get_or_create_realtime_admission(self) -> RuntimeRealtimeTurnAdmission:
        if self.realtime_admission is None:
            self.realtime_admission = RuntimeRealtimeTurnAdmission(self.conversation)
        return self.realtime_admission

    def _build_cloud_bridge_factory_for_snapshot(
        self, snapshot: RealtimeConnectionSnapshot
    ) -> Callable[[UUID], Awaitable[CloudRealtimeMediaBridge]]:
        backend = self._create_cloud_backend_for_snapshot(snapshot)
        gateway = self._get_or_create_egress_gateway()
        admission = self._get_or_create_realtime_admission()
        factory = RuntimeCloudRealtimeFactory(
            backend=backend,
            egress_gateway=gateway,
            conversation=self.conversation,
            sessions=self.sessions,
            admission=admission,
            characters=self.characters,
            character_kernel=self.character_kernel,
            memory=self.memory,
            skills_source=self.runtime_skills,
            tools_enabled=snapshot.cloud_tools_enabled,
            event_hub=self.event_hub,
            playback=self.playback,
        )
        return factory.create_bridge


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
