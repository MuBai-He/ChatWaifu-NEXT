"""Manifest-driven, allowlisted Runtime Skill execution for the basic demo."""

from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID, uuid4

from chatwaifu_protocol.base import PrivacyLevel
from chatwaifu_protocol.events import GenericCoreEvent
from chatwaifu_protocol.skills import SkillDefinition, SkillResult

from chatwaifu_runtime.eventing.publisher import EventPublisher
from chatwaifu_runtime.providers.factory import ProviderSet


class RuntimeSkillService:
    def __init__(
        self,
        root: Path,
        publisher: EventPublisher,
        providers: ProviderSet,
        stt_provider: str,
        version: str,
    ) -> None:
        self._root = root
        self._publisher = publisher
        self._providers = providers
        self._stt_provider = stt_provider
        self._version = version
        self._definitions: dict[str, SkillDefinition] = {}

    def start(self) -> None:
        definitions: dict[str, SkillDefinition] = {}
        for path in sorted((self._root / "builtin").glob("*/skill.json")):
            definition = SkillDefinition.model_validate_json(path.read_text(encoding="utf-8"))
            if definition.skill_id in definitions:
                raise ValueError(f"duplicate Runtime Skill id: {definition.skill_id}")
            definitions[definition.skill_id] = definition
        self._definitions = definitions

    def list(self) -> list[SkillDefinition]:
        return list(self._definitions.values())

    async def run_status(self, session_id: UUID) -> SkillResult:
        definition = self._definitions.get("runtime.status")
        if definition is None:
            raise KeyError("runtime.status skill is not installed")
        skill_run_id = uuid4()
        await self._emit(
            session_id,
            "skill.run_started",
            {"skill_id": definition.skill_id, "skill_run_id": str(skill_run_id)},
            skill_run_id,
        )
        providers = self._providers.public_status()
        result = SkillResult(
            status="succeeded",
            data={
                "runtime_version": self._version,
                "llm_provider": providers["llm"],
                "tts_provider": providers["tts"],
                "stt_provider": self._stt_provider,
                "transport": "pipecat_smallwebrtc",
                "persistence": "sqlite_wal",
            },
            spoken_summary=(
                f"Runtime {self._version} 正常，语言模型使用 {providers['llm']}，"
                f"语音合成使用 {providers['tts']}，语音识别使用 {self._stt_provider}。"
            ),
            provenance=["runtime.composition_root"],
        )
        await self._emit(
            session_id,
            "skill.run_completed",
            {
                "skill_id": definition.skill_id,
                "skill_run_id": str(skill_run_id),
                "result": result.model_dump(mode="json"),
            },
            skill_run_id,
        )
        return result

    async def _emit(
        self,
        session_id: UUID,
        event_type: str,
        payload: dict[str, object],
        skill_run_id: UUID,
    ) -> None:
        event = GenericCoreEvent.model_validate(
            {
                "event_id": uuid4(),
                "event_type": event_type,
                "session_id": session_id,
                "skill_run_id": skill_run_id,
                "occurred_at": datetime.now(UTC),
                "source": "runtime.skills",
                "privacy": PrivacyLevel.LOCAL,
                "payload": payload,
            }
        )
        await self._publisher.emit(event)
