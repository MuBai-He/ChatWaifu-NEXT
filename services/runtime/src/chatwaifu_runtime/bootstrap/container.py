"""Composition root and ordered Runtime lifecycle."""

from chatwaifu_runtime import __version__
from chatwaifu_runtime.audio.store import AudioAssetStore
from chatwaifu_runtime.characters.service import CharacterService
from chatwaifu_runtime.config.settings import Settings
from chatwaifu_runtime.conversation.service import ConversationService
from chatwaifu_runtime.eventing.hub import EventHub
from chatwaifu_runtime.eventing.publisher import EventPublisher
from chatwaifu_runtime.memory.service import MemoryService
from chatwaifu_runtime.persistence.database import Database
from chatwaifu_runtime.persistence.event_store import EventStore
from chatwaifu_runtime.providers.factory import build_providers
from chatwaifu_runtime.realtime.pipecat.session import PipecatMediaAdapter
from chatwaifu_runtime.realtime.service import VoiceMediaService
from chatwaifu_runtime.realtime.stt import build_stt_backend
from chatwaifu_runtime.runtime_skills.service import RuntimeSkillService
from chatwaifu_runtime.sessions.service import SessionService


class RuntimeContainer:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.database = Database(settings.database_path, settings.storage)
        self.event_hub = EventHub(settings.runtime.event_queue_size)
        self.event_store = EventStore(self.database)
        self.event_publisher = EventPublisher(self.event_store, self.event_hub)
        self.sessions = SessionService(self.database, self.event_store, self.event_hub)
        self.providers = build_providers(settings)
        self.audio_assets = AudioAssetStore(settings.data_dir / "audio")
        self.characters = CharacterService(settings.characters_dir)
        self.memory = MemoryService(self.database, self.event_publisher)
        self.stt = build_stt_backend(settings)
        self.runtime_skills = RuntimeSkillService(
            settings.skills_dir,
            settings.data_dir,
            self.database,
            self.event_publisher,
            self.providers,
            self.stt.kind,
            __version__,
        )
        self.conversation = ConversationService(
            self.database,
            self.event_store,
            self.event_publisher,
            self.sessions,
            self.providers,
            self.audio_assets,
            self.characters,
            self.memory,
        )
        self.voice_media = VoiceMediaService(
            PipecatMediaAdapter(
                config=settings.realtime,
                publisher=self.event_publisher,
                event_hub=self.event_hub,
                conversation=self.conversation,
                audio_assets=self.audio_assets,
                stt=self.stt,
            )
        )
        self._started = False

    async def start(self) -> None:
        if self._started:
            return
        self.characters.start()
        self.audio_assets.start()
        await self.database.open()
        await self.runtime_skills.start()
        self._started = True
        for event in await self.event_store.pending_outbox():
            await self.event_hub.publish(event)
            event_id = event.get("event_id")
            if event_id is not None:
                await self.event_store.mark_published(str(event_id))

    async def stop(self) -> None:
        if not self._started:
            return
        self._started = False
        await self.voice_media.close()
        await self.conversation.stop()
        await self.runtime_skills.stop()
        await self.stt.close()
        await self.providers.tts.close()
        await self.event_hub.close()
        await self.database.close()
