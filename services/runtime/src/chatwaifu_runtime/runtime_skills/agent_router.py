"""Deterministic, bounded projection of Runtime Skills into LLM tools."""

from __future__ import annotations

import json
import re
from collections.abc import Callable, Iterable, Mapping
from copy import deepcopy
from dataclasses import dataclass
from typing import cast

from chatwaifu_protocol.base import JsonObject, JsonValue, SideEffect
from chatwaifu_protocol.skills import SkillCapability, SkillDefinition, SkillInvocation

from chatwaifu_runtime.runtime_skills.tool_names import allocate_tool_names

DEFAULT_AGENT_TOOL_LIMIT = 8
MAX_AGENT_TOOL_LIMIT = 16
DEFAULT_AGENT_SCHEMA_BUDGET_BYTES = 24_576
MAX_AGENT_SCHEMA_BUDGET_BYTES = 65_536
MAX_AGENT_TOOL_SCHEMA_BYTES = 12_288
MAX_AGENT_TOOL_DESCRIPTION_BYTES = 768
_ASCII_WORD = re.compile(r"[a-z0-9][a-z0-9_-]*")
_CJK_RUN = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff]+")
_CONTROL = re.compile(r"[\x00-\x1f\x7f]+")
_ASCII_STOPWORDS = frozenset(
    {
        "a",
        "an",
        "and",
        "call",
        "current",
        "for",
        "from",
        "get",
        "in",
        "local",
        "of",
        "on",
        "one",
        "or",
        "please",
        "provided",
        "read",
        "return",
        "service",
        "set",
        "that",
        "the",
        "this",
        "to",
        "tool",
        "with",
    }
)
_CJK_STOPWORDS = frozenset(
    {"一下", "一个", "当前", "可以", "帮我", "现在", "这个", "那个", "返回", "获取", "调用"}
)

# This is a language normalization table, not a capability-name router.  It
# expands user and manifest text symmetrically, so newly installed Skills still
# route from their declared names/descriptions without code changes.
_CONCEPT_TERMS: dict[str, tuple[str, ...]] = {
    "search": (
        "search",
        "browse",
        "lookup",
        "find",
        "internet",
        "web",
        "news",
        "latest",
        "research",
        "搜索",
        "搜一下",
        "查找",
        "查一下",
        "联网",
        "上网",
        "网页",
        "新闻",
        "最新",
        "资料",
    ),
    "status": (
        "status",
        "health",
        "runtime",
        "diagnostic",
        "available",
        "状态",
        "健康",
        "运行情况",
        "可用",
        "正常",
        "诊断",
    ),
    "weather": ("weather", "forecast", "temperature", "天气", "预报", "气温", "温度"),
    "time": ("time", "clock", "timezone", "date", "时间", "几点", "日期", "时区"),
    "file": ("file", "folder", "document", "文件", "目录", "文档"),
    "calendar": ("calendar", "event", "schedule", "日历", "日程", "安排"),
    "note": ("note", "memo", "append", "笔记", "备忘", "记录", "追加"),
    "echo": ("echo", "repeat", "原样返回", "复述", "重复"),
    "wait": ("wait", "delay", "sleep", "等待", "延迟"),
}


@dataclass(frozen=True, slots=True)
class ProjectedSkillTool:
    """One model-visible tool and its immutable Runtime invocation target."""

    name: str
    skill_id: str
    capability: str
    description: str
    input_schema: JsonObject
    side_effect: SideEffect
    confirmation_required: bool

    def to_invocation(self, arguments: JsonObject) -> SkillInvocation:
        """Map provider arguments back to the permissioned Runtime Skill gateway."""

        return SkillInvocation(
            skill_id=self.skill_id,
            capability=self.capability,
            arguments=deepcopy(arguments),
        )


@dataclass(frozen=True, slots=True)
class _Candidate:
    skill: SkillDefinition
    capability: SkillCapability
    description: str
    input_schema: JsonObject
    schema_bytes: int
    score: int

    @property
    def identity(self) -> tuple[str, str]:
        return (self.skill.skill_id, self.capability.name)


class RuntimeSkillRouter:
    """Select only relevant, safe, bounded Runtime Skill schemas for a turn."""

    def __init__(self, definitions: Callable[[], Iterable[SkillDefinition]]) -> None:
        self._definitions = definitions

    def select(
        self,
        query: str,
        *,
        limit: int = DEFAULT_AGENT_TOOL_LIMIT,
        schema_budget_bytes: int = DEFAULT_AGENT_SCHEMA_BUDGET_BYTES,
    ) -> tuple[ProjectedSkillTool, ...]:
        if not query.strip() or limit <= 0 or schema_budget_bytes <= 0:
            return ()
        bounded_limit = min(limit, MAX_AGENT_TOOL_LIMIT)
        bounded_budget = min(schema_budget_bytes, MAX_AGENT_SCHEMA_BUDGET_BYTES)
        candidates: list[_Candidate] = []
        for skill in self._definitions():
            if not skill.enabled:
                continue
            for capability in skill.capabilities:
                candidate = _project_candidate(skill, capability, query=query)
                if candidate is not None:
                    candidates.append(candidate)

        candidates.sort(key=_candidate_sort_key)
        selected: list[_Candidate] = []
        consumed = 0
        for candidate in candidates:
            if len(selected) >= bounded_limit:
                break
            projected_bytes = candidate.schema_bytes + len(candidate.description.encode("utf-8"))
            if projected_bytes > bounded_budget - consumed:
                continue
            selected.append(candidate)
            consumed += projected_bytes

        identities = [candidate.identity for candidate in selected]
        names = allocate_tool_names(identities, max_length=64, opaque_prefix="cw")
        return tuple(
            ProjectedSkillTool(
                name=name,
                skill_id=candidate.skill.skill_id,
                capability=candidate.capability.name,
                description=candidate.description,
                input_schema=deepcopy(candidate.input_schema),
                side_effect=candidate.capability.side_effect,
                confirmation_required=candidate.capability.confirmation_required,
            )
            for candidate, name in zip(selected, names, strict=True)
        )


def _project_candidate(
    skill: SkillDefinition, capability: SkillCapability, *, query: str
) -> _Candidate | None:
    if capability.adapter_operation != "invoke":
        return None
    if skill.source not in {"builtin", "plugin", "mcp_connection"}:
        return None
    if skill.source in {"plugin", "mcp_connection"} and (
        not capability.confirmation_required or not capability.required_permissions
    ):
        # This is a host-owned Agent exposure policy, not a trust decision made by
        # an installable manifest. Third-party code cannot make itself autonomous
        # by labelling a capability read-only; explicit permission plus a fresh
        # confirmation boundary are required before it is even shown to the LLM.
        return None
    if capability.side_effect is not SideEffect.READ and (
        not capability.confirmation_required or not capability.required_permissions
    ):
        # A manifest declaration alone must not allow autonomous side effects.
        return None
    schema = deepcopy(capability.input_schema)
    if not _safe_object_schema(schema):
        return None
    try:
        encoded_schema = json.dumps(
            schema, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
    except (TypeError, ValueError, RecursionError):
        return None
    if len(encoded_schema) > MAX_AGENT_TOOL_SCHEMA_BYTES:
        return None

    score = _relevance_score(query, skill, capability)
    if score <= 0:
        return None
    description = _model_description(skill, capability)
    return _Candidate(
        skill=skill,
        capability=capability,
        description=description,
        input_schema=schema,
        schema_bytes=len(encoded_schema),
        score=score,
    )


def _safe_object_schema(schema: Mapping[str, JsonValue]) -> bool:
    if schema.get("type") != "object":
        return False
    properties = schema.get("properties")
    if properties is not None and not isinstance(properties, dict):
        return False
    stack: list[tuple[JsonValue, int]] = [(cast(JsonValue, schema), 0)]
    visited = 0
    while stack:
        value, depth = stack.pop()
        visited += 1
        if visited > 2_048 or depth > 32:
            return False
        if isinstance(value, dict):
            reference = value.get("$ref")
            if isinstance(reference, str) and not reference.startswith("#/"):
                return False
            stack.extend((child, depth + 1) for child in value.values())
        elif isinstance(value, list):
            stack.extend((child, depth + 1) for child in value)
    return True


def _relevance_score(query: str, skill: SkillDefinition, capability: SkillCapability) -> int:
    query_features = _features(query)
    if not query_features:
        return 0
    identity_text = f"{skill.skill_id} {skill.name} {capability.name}"
    descriptive_text = f"{skill.description[:2_000]} {capability.description[:2_000]}"
    identity_features = _features(identity_text)
    description_features = _features(descriptive_text)
    identity_overlap = query_features & identity_features
    description_overlap = query_features & description_features
    concept_overlap = {
        feature
        for feature in identity_overlap | description_overlap
        if feature.startswith("concept:")
    }
    lexical_identity = identity_overlap - concept_overlap
    lexical_description = description_overlap - concept_overlap
    score = len(concept_overlap) * 12 + len(lexical_identity) * 6 + len(lexical_description) * 2

    normalized_query = " ".join(_ASCII_WORD.findall(query.casefold()))
    capability_name = " ".join(_ASCII_WORD.findall(capability.name.casefold()))
    if len(capability_name) >= 3 and capability_name in normalized_query:
        score += 10
    return score


def _features(text: str) -> set[str]:
    lowered = text.casefold()
    raw_ascii = set(_ASCII_WORD.findall(lowered))
    ascii_tokens = {
        part
        for token in raw_ascii
        for part in (token, *re.split(r"[_-]+", token))
        if len(part) >= 2 and part not in _ASCII_STOPWORDS
    }
    features = {_stem_ascii(token) for token in ascii_tokens}
    for run in _CJK_RUN.findall(lowered):
        if len(run) >= 2:
            features.add(run)
            for length in (2, 3, 4):
                features.update(
                    token
                    for index in range(len(run) - length + 1)
                    if (token := run[index : index + length]) not in _CJK_STOPWORDS
                )
    for concept, terms in _CONCEPT_TERMS.items():
        if any(_matches_concept_term(term, lowered, ascii_tokens) for term in terms):
            features.add(f"concept:{concept}")
    return features


def _matches_concept_term(term: str, lowered: str, ascii_tokens: set[str]) -> bool:
    if term.isascii():
        return _stem_ascii(term) in {_stem_ascii(token) for token in ascii_tokens}
    return term in lowered


def _stem_ascii(token: str) -> str:
    for suffix in ("ing", "ed", "es", "s"):
        if token.endswith(suffix) and len(token) - len(suffix) >= 3:
            return token[: -len(suffix)]
    return token


def _model_description(skill: SkillDefinition, capability: SkillCapability) -> str:
    source = {
        "builtin": "local built-in",
        "plugin": "installed local plugin",
        "mcp_connection": "connected MCP server",
    }[skill.source]
    confirmation = (
        " This operation requires local user confirmation before any side effect executes."
        if capability.confirmation_required
        else ""
    )
    value = (
        f"{_clean_text(skill.name)}: {_clean_text(capability.description)} "
        f"Source: {source}.{confirmation}"
    )
    encoded = value.encode("utf-8")
    if len(encoded) <= MAX_AGENT_TOOL_DESCRIPTION_BYTES:
        return value
    return encoded[:MAX_AGENT_TOOL_DESCRIPTION_BYTES].decode("utf-8", errors="ignore").rstrip()


def _clean_text(value: str) -> str:
    return " ".join(_CONTROL.sub(" ", value).split())


def _candidate_sort_key(candidate: _Candidate) -> tuple[int, int, int, str, str]:
    source_rank = {"builtin": 0, "plugin": 1, "mcp_connection": 2}[candidate.skill.source]
    side_effect_rank = {
        SideEffect.READ: 0,
        SideEffect.WRITE: 1,
        SideEffect.EXTERNAL_COMMUNICATION: 2,
        SideEffect.DESTRUCTIVE: 3,
        SideEffect.DEVICE_CONTROL: 3,
    }[candidate.capability.side_effect]
    return (
        -candidate.score,
        side_effect_rank,
        source_rank,
        candidate.skill.skill_id,
        candidate.capability.name,
    )
