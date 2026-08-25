"""Schema-validated LLM candidate extraction layered behind memory policy."""

from __future__ import annotations

import json
import re
from datetime import datetime

from chatwaifu_protocol.base import PrivacyLevel
from chatwaifu_protocol.memory import MemoryRecord, MemoryRecordDraft
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from chatwaifu_runtime.memory.extractor import ExtractedMemoryCandidate
from chatwaifu_runtime.providers.model_config import ModelConfigurationService

_SENSITIVE = re.compile(
    r"密码|口令|身份证|银行卡|住址|手机号|phone number|\b1[3-9]\d{9}\b|"
    r"[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}",
    re.IGNORECASE,
)


class _InferredMemory(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: str
    subject_id: str | None = "user"
    predicate: str | None = None
    value: object = None
    text: str = Field(min_length=1, max_length=2_000)
    confidence: float = Field(ge=0, le=1)
    importance: float = Field(ge=0, le=1)
    sensitivity: PrivacyLevel = PrivacyLevel.PRIVATE
    rationale: str = Field(min_length=1, max_length=500)


class _InferenceEnvelope(BaseModel):
    model_config = ConfigDict(extra="forbid")

    memories: list[_InferredMemory] = Field(
        default_factory=lambda: list[_InferredMemory](), max_length=8
    )


class LlmMemoryCandidateExtractor:
    def __init__(self, models: ModelConfigurationService) -> None:
        self._models = models

    async def extract(
        self,
        text: str,
        *,
        namespace: str,
        observed_at: datetime,
        related: list[MemoryRecord],
    ) -> list[ExtractedMemoryCandidate]:
        config = self._models.get("memory_extraction")
        if not config.enabled or config.provider == "disabled" or _SENSITIVE.search(text):
            return []
        related_payload = [
            {
                "memory_id": str(item.memory_id),
                "kind": item.kind,
                "predicate": item.predicate,
                "text": item.text,
            }
            for item in related[:12]
        ]
        response = await self._models.complete(
            "memory_extraction",
            system=(
                "Extract only durable user facts, preferences, commitments, interaction "
                "preferences, relationship signals, or shared events. Do not store greetings, "
                "temporary requests, secrets, passwords, or character canon. Return strict JSON "
                'with shape {"memories":[{kind,subject_id,predicate,value,text,confidence,'
                "importance,sensitivity,rationale}]}. Allowed kinds: semantic.fact, "
                "semantic.preference, episodic.shared_event, procedural.preference, "
                "relationship.signal, prospective.commitment. Use an empty list when nothing "
                "is durable. Never instruct deletion."
            ),
            user=json.dumps(
                {"new_user_text": text, "related_existing_memories": related_payload},
                ensure_ascii=False,
            ),
        )
        if not response:
            return []
        try:
            parsed = _InferenceEnvelope.model_validate_json(_json_body(response))
        except (ValidationError, ValueError, json.JSONDecodeError):
            return []
        candidates: list[ExtractedMemoryCandidate] = []
        for item in parsed.memories:
            try:
                draft = MemoryRecordDraft.model_validate(
                    {
                        "namespace": namespace,
                        "kind": item.kind,
                        "subject_id": item.subject_id,
                        "predicate": item.predicate,
                        "value": item.value,
                        "text": item.text,
                        "observed_at": observed_at,
                        "confidence": item.confidence,
                        "importance": item.importance,
                        "sensitivity": item.sensitivity,
                    }
                )
            except ValidationError:
                continue
            candidates.append(
                ExtractedMemoryCandidate(
                    draft=draft,
                    explicit=False,
                    rationale=f"model-assisted extraction: {item.rationale}",
                )
            )
        return candidates


def _json_body(value: str) -> str:
    stripped = value.strip()
    if stripped.startswith("```"):
        stripped = re.sub(r"^```(?:json)?\s*", "", stripped, flags=re.IGNORECASE)
        stripped = re.sub(r"\s*```$", "", stripped)
    start = stripped.find("{")
    end = stripped.rfind("}")
    if start < 0 or end < start:
        raise ValueError("model response does not contain a JSON object")
    return stripped[start : end + 1]
