"""Conservative deterministic candidate extraction for the first memory projection."""

import re
from dataclasses import dataclass
from datetime import datetime

from chatwaifu_protocol.base import PrivacyLevel
from chatwaifu_protocol.memory import MemoryRecordDraft

_REMEMBER_PATTERNS = (
    re.compile(r"^(?:请|帮我)?记住(?:一下)?[\s:\uff1a]*(.+)$", re.IGNORECASE),
    re.compile(r"^(?:please\s+)?remember[\s:]+(.+)$", re.IGNORECASE),
)
_FORGET_PATTERNS = (
    re.compile(r"^(?:请|帮我)?忘记(?:掉)?[\s:\uff1a]*(.+)$", re.IGNORECASE),
    re.compile(r"^(?:please\s+)?forget[\s:]+(.+)$", re.IGNORECASE),
)
_NAME = re.compile(r"^(?:我叫|我的名字是)[\s:\uff1a]*([^，。,.!?\s]{1,40})")
_PROFILE = re.compile(r"^我的([^，。,.!?]{1,24})是[\s:\uff1a]*([^。.!?]{1,160})")
_PREFERENCE = re.compile(r"^我(最喜欢|喜欢|不喜欢|讨厌)[\s:\uff1a]*([^。.!?]{1,160})")
_PROSPECTIVE = re.compile(r"^(?:请)?提醒我[\s:\uff1a]*([^。.!?]{1,200})")
_PROCEDURAL = re.compile(r"^(?:以后|下次)(?:请|要)?[\s:\uff1a]*([^。.!?]{1,200})")
_SENSITIVE_PATTERNS = (
    re.compile(r"\b1[3-9]\d{9}\b"),
    re.compile(r"\b\d{17}[\dXx]\b"),
    re.compile(r"[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}"),
    re.compile(r"密码|口令|身份证|银行卡|住址|家庭地址|手机号|phone number", re.IGNORECASE),
)
_COLORS = frozenset(
    ("红色", "橙色", "黄色", "绿色", "蓝色", "紫色", "粉色", "黑色", "白色", "灰色")
)


@dataclass(frozen=True, slots=True)
class ExplicitMemoryCommand:
    operation: str
    content: str


@dataclass(frozen=True, slots=True)
class ExtractedMemoryCandidate:
    draft: MemoryRecordDraft
    explicit: bool
    rationale: str


class DeterministicMemoryExtractor:
    def parse_explicit_command(self, text: str) -> ExplicitMemoryCommand | None:
        normalized = text.strip()
        for pattern in _REMEMBER_PATTERNS:
            if match := pattern.fullmatch(normalized):
                content = match.group(1).strip()
                if content:
                    return ExplicitMemoryCommand("remember", content)
        for pattern in _FORGET_PATTERNS:
            if match := pattern.fullmatch(normalized):
                content = match.group(1).strip()
                if content:
                    return ExplicitMemoryCommand("forget", content)
        return None

    def extract(
        self,
        text: str,
        *,
        namespace: str,
        observed_at: datetime,
        explicit: bool = False,
    ) -> ExtractedMemoryCandidate | None:
        content = text.strip()
        if not content:
            return None
        sensitivity = (
            PrivacyLevel.SENSITIVE
            if any(pattern.search(content) for pattern in _SENSITIVE_PATTERNS)
            else PrivacyLevel.PRIVATE
        )
        base: dict[str, object] = {
            "namespace": namespace,
            "observed_at": observed_at,
            "confidence": 0.98 if explicit else 0.86,
            "importance": 0.65,
            "sensitivity": sensitivity,
            "text": content,
            "subject_id": "user",
        }
        if match := _NAME.match(content):
            return self._candidate(
                base,
                explicit,
                kind="semantic.fact",
                predicate="profile.name",
                value=match.group(1).strip(),
                importance=0.9,
                rationale="stable user profile statement",
            )
        if match := _PROFILE.match(content):
            field = _normalize_identity(match.group(1))
            return self._candidate(
                base,
                explicit,
                kind="semantic.fact",
                predicate=f"profile.{field}",
                value=match.group(2).strip(),
                importance=0.75,
                rationale="structured user profile fact",
            )
        if match := _PREFERENCE.match(content):
            verb, value = match.groups()
            value = value.strip()
            positive = verb in {"喜欢", "最喜欢"}
            if verb == "最喜欢" and value in _COLORS:
                predicate = "favorite.color"
                stored_value: object = value
            else:
                predicate = f"preference.like.{_normalize_identity(value)}"
                stored_value = positive
            return self._candidate(
                base,
                explicit,
                kind="semantic.preference",
                predicate=predicate,
                value=stored_value,
                importance=0.72,
                rationale="explicitly phrased user preference",
            )
        if match := _PROSPECTIVE.match(content):
            return self._candidate(
                base,
                explicit,
                kind="prospective.commitment",
                predicate=f"commitment.{_normalize_identity(match.group(1))}",
                value=match.group(1).strip(),
                importance=0.8,
                rationale="future commitment candidate",
            )
        if match := _PROCEDURAL.match(content):
            return self._candidate(
                base,
                explicit,
                kind="procedural.preference",
                predicate=f"procedure.{_normalize_identity(match.group(1))}",
                value=match.group(1).strip(),
                importance=0.7,
                rationale="future interaction preference",
            )
        if not explicit:
            return None
        return self._candidate(
            base,
            explicit,
            kind="semantic.fact",
            predicate=None,
            value=content,
            importance=0.7,
            rationale="user explicitly requested durable memory",
        )

    def _candidate(
        self,
        base: dict[str, object],
        explicit: bool,
        *,
        kind: str,
        predicate: str | None,
        value: object,
        importance: float,
        rationale: str,
    ) -> ExtractedMemoryCandidate:
        return ExtractedMemoryCandidate(
            draft=MemoryRecordDraft.model_validate(
                {
                    **base,
                    "kind": kind,
                    "predicate": predicate,
                    "value": value,
                    "importance": importance,
                }
            ),
            explicit=explicit,
            rationale=rationale,
        )


def _normalize_identity(text: str) -> str:
    normalized = "".join(character for character in text.casefold() if character.isalnum())
    return normalized[:80] or "unknown"
