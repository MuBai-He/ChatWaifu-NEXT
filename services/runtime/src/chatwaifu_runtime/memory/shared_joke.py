"""Domain helper for bounded shared-joke mutual uptake and candidate validation."""

from __future__ import annotations

import re
from datetime import datetime
from typing import Literal
from uuid import UUID

from chatwaifu_protocol.base import PrivacyLevel
from chatwaifu_protocol.memory import MemoryRecordDraft

from chatwaifu_runtime.memory.extractor import ExtractedMemoryCandidate
from chatwaifu_runtime.memory.repository import PrecedingAssistantEvidence

_EXPLICIT_SHARED_JOKE_MARKERS = (
    # Chinese
    "这是我们的梗",
    "这就是我们的梗",
    "以后就是我们的梗",
    "我们的梗",
    "这是梗",
    "当成我们的梗",
    "当做我们的梗",
    "成梗了",
    "变成梗",
    "成为梗",
    "属于我们的梗",
    "专属梗",
    "这是暗号",
    "这是我们的暗号",
    "这就是暗号",
    "这就是我们的暗号",
    "定个暗号",
    "定为暗号",
    "作为暗号",
    "当成暗号",
    "当做暗号",
    "约定暗号",
)

_EXPLICIT_SHARED_JOKE_ENGLISH_MARKERS = (
    "inside joke",
    "shared joke",
    "our joke",
    "our running joke",
    "running joke",
    "secret code",
    "code word",
)

_IMPLICIT_UPTAKE_MARKERS = (
    # Chinese
    "哈哈",
    "笑死",
    "太逗",
    "好好笑",
    "真逗",
    "乐死",
    "笑抽",
    "太搞笑了",
    "太好笑了",
    "太有意思了",
)

_IMPLICIT_UPTAKE_ENGLISH_MARKERS = (
    "hhhh",
    "2333",
    "haha",
    "hahaha",
    "lmao",
    "rofl",
    "lol",
    "hilarious",
    "cracked me up",
    "too funny",
)

_NEGATED_SHARED_JOKE_PATTERNS = (
    re.compile(
        r"(?:不要|别|不准|禁止|并非|不是|不算|不记得|记不清|忘记|忘掉|忘了)"
        r".{0,16}(?:梗|暗号)"
    ),
    re.compile(r"(?:梗|暗号).{0,16}(?:不要记|别记|别保存|忘记|忘掉|不算)"),
    re.compile(
        r"\b(?:do\s+not|don't|dont|not|never|forget|no|hardly|barely|"
        r"isn't|isnt|wasn't|wasnt|aren't|arent|weren't|werent)\b.{0,40}"
        r"\b(?:inside\s+joke|shared\s+joke|running\s+joke|secret\s+code|"
        r"code\s+word|hilarious|funny)\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"(?:一点也|一点都|并|根本|真)?(?:不|没|没有|不太|没那么)"
        r".{0,12}(?:好笑|搞笑|有意思|逗|笑死)"
    ),
)

_QUESTION_SHARED_JOKE_PATTERNS = (
    re.compile(r"(?:什么|哪个|怎么|为什么|为何|是不是|算不算).{0,16}(?:梗|暗号)"),
    re.compile(
        r"(?:梗|暗号).{0,16}(?:是什么|什么意思|吗|么|呢|如何|怎么|为什么|为何|是不是|算不算)"
    ),
    re.compile(
        r"^(?:what|which|why|how|is|are|do|does|did|can|could|would|should)\b"
        r".{0,48}\b(?:inside\s+joke|shared\s+joke|running\s+joke|secret\s+code|code\s+word)\b",
        re.IGNORECASE,
    ),
)

_SENSITIVE = re.compile(
    r"密码|口令|身份证|银行卡|住址|手机号|phone number|\b1[3-9]\d{9}\b|"
    r"[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}",
    re.IGNORECASE,
)

_MAX_CONTEXT_CHARS = 240

_QUOTED_CUE_PATTERNS = (
    re.compile(r"“([^”\r\n]{2,80})”"),
    re.compile(r"「([^」\r\n]{2,80})」"),
    re.compile(r"『([^』\r\n]{2,80})』"),
    re.compile(r'"([^"\r\n]{2,80})"'),
    re.compile(r"'([^'\r\n]{2,80})'"),
)


def classify_uptake(text: str) -> tuple[Literal["explicit", "implicit"] | None, float]:
    """Classify user uptake marker and return corresponding confidence threshold.

    Returns:
        (uptake_kind, min_confidence_threshold)
        Explicit declaration: >= 0.80
        Implicit laughter/callback: >= 0.90
        Neither: (None, 1.0)
    """
    lower = text.casefold().strip()
    if "?" in lower or "？" in lower:
        return None, 1.0
    if any(
        pattern.search(lower)
        for pattern in (*_NEGATED_SHARED_JOKE_PATTERNS, *_QUESTION_SHARED_JOKE_PATTERNS)
    ):
        return None, 1.0
    for marker in _EXPLICIT_SHARED_JOKE_MARKERS:
        if marker.casefold() in lower:
            return "explicit", 0.80
    if any(
        _contains_english_marker(lower, marker) for marker in _EXPLICIT_SHARED_JOKE_ENGLISH_MARKERS
    ):
        return "explicit", 0.80

    for marker in _IMPLICIT_UPTAKE_MARKERS:
        if marker.casefold() in lower:
            return "implicit", 0.90
    if any(_contains_english_marker(lower, marker) for marker in _IMPLICIT_UPTAKE_ENGLISH_MARKERS):
        return "implicit", 0.90

    return None, 1.0


def _contains_english_marker(text: str, marker: str) -> bool:
    return re.search(rf"(?<!\w){re.escape(marker.casefold())}(?!\w)", text) is not None


def normalize_cue(cue: str) -> str:
    """Normalize cue for deterministic identity predicate."""
    cleaned = re.sub(r"[^\w\u4e00-\u9fff]+", "_", cue.strip().casefold())
    cleaned = cleaned.strip("_")
    return cleaned[:80] or "unnamed"


def _normalize_grounding_text(text: str) -> str:
    return " ".join(text.casefold().split())


def _contains_grounded_cue(cue: str, text: str) -> bool:
    normalized_cue = _normalize_grounding_text(cue)
    normalized_text = _normalize_grounding_text(text)
    if not normalized_cue or not normalized_text:
        return False
    if normalized_cue.isascii():
        return re.search(rf"(?<!\w){re.escape(normalized_cue)}(?!\w)", normalized_text) is not None
    return normalized_cue in normalized_text


def is_cue_grounded(cue: str, user_text: str, assistant_text: str) -> bool:
    """Check cue occurrence without joining or matching through source word boundaries."""
    visible_cue = "".join(character for character in cue if character.isalnum())
    if len(visible_cue) < 2:
        return False
    return _contains_grounded_cue(cue, user_text) or _contains_grounded_cue(cue, assistant_text)


def is_sensitive_content(*texts: str) -> bool:
    """Check if any text matches sensitive information patterns."""
    combined = " ".join(texts)
    return _SENSITIVE.search(combined) is not None


def is_shared_joke_draft(draft: MemoryRecordDraft) -> bool:
    """Check if candidate draft claims to represent a shared joke."""
    if draft.kind != "episodic.shared_event":
        return False
    if draft.predicate and (
        draft.predicate == "shared_joke" or draft.predicate.startswith("shared_joke.")
    ):
        return True
    if isinstance(draft.value, dict):
        if draft.value.get("kind") == "shared_joke":
            return True
    return False


def _extract_cue_and_context(draft: MemoryRecordDraft) -> tuple[str, str] | None:
    cue = ""
    context = ""
    if isinstance(draft.value, dict):
        raw_cue = draft.value.get("cue")
        raw_context = draft.value.get("context", draft.value.get("callback", ""))
        if raw_cue is not None and not isinstance(raw_cue, str):
            return None
        if raw_context is not None and not isinstance(raw_context, str):
            return None
        cue = (raw_cue or "").strip()
        context = (raw_context or "").strip()
    if not cue and draft.predicate and draft.predicate.startswith("shared_joke."):
        cue = draft.predicate[len("shared_joke.") :].strip()
    if len(context) > _MAX_CONTEXT_CHARS:
        return None
    return cue, context


def ensure_factual_joke_text(cue: str) -> str:
    """Build deterministic wording without trusting model-authored event claims."""
    return f"用户将“{cue}”认作了双方的共同梗或暗号。"


def extract_explicit_quoted_shared_joke(
    *,
    user_text: str,
    preceding_assistant: PrecedingAssistantEvidence | None,
    source_event_id: UUID,
    namespace: str,
    observed_at: datetime,
) -> ExtractedMemoryCandidate | None:
    """Extract an unambiguous quoted cue from explicit mutual uptake.

    This deterministic path covers phrases such as ``把“流星伞”当成我们的梗``
    without trusting the memory-extraction model to notice an explicit agreement.
    The quoted cue must also occur in the immediately preceding presented assistant
    text, so user-authored or ambiguous quoted phrases still fail closed.
    """
    uptake_kind, _threshold = classify_uptake(user_text)
    if uptake_kind != "explicit" or preceding_assistant is None:
        return None

    grounded: dict[str, str] = {}
    for pattern in _QUOTED_CUE_PATTERNS:
        for match in pattern.finditer(user_text):
            cue = match.group(1).strip()
            if (
                2 <= len(cue) <= 80
                and is_cue_grounded(cue, "", preceding_assistant.presented_text)
                and not is_sensitive_content(user_text, preceding_assistant.presented_text, cue)
            ):
                grounded.setdefault(normalize_cue(cue), cue)
    if len(grounded) != 1:
        return None

    cue = next(iter(grounded.values()))
    raw = ExtractedMemoryCandidate(
        draft=MemoryRecordDraft(
            namespace=namespace,
            kind="episodic.shared_event",
            subject_id="relationship",
            predicate=f"shared_joke.{normalize_cue(cue)}",
            value={"cue": cue, "context": "", "kind": "shared_joke"},
            text=ensure_factual_joke_text(cue),
            observed_at=observed_at,
            confidence=1.0,
            importance=0.7,
            sensitivity=PrivacyLevel.PRIVATE,
        ),
        explicit=False,
        rationale="deterministic explicit quoted shared-joke uptake",
    )
    return validate_and_transform_shared_joke(
        raw,
        user_text=user_text,
        preceding_assistant=preceding_assistant,
        source_event_id=source_event_id,
    )


def validate_and_transform_shared_joke(
    candidate: ExtractedMemoryCandidate,
    *,
    user_text: str,
    preceding_assistant: PrecedingAssistantEvidence | None,
    source_event_id: UUID,
) -> ExtractedMemoryCandidate | None:
    """Validate shared joke against strict uptake, delivery proof, grounding and confidence gates.

    Fails closed: returns None if any criterion is unmet.
    """
    draft = candidate.draft
    if not is_shared_joke_draft(draft):
        return None

    # Delivery proof: requires preceding presented assistant evidence in the same session
    if preceding_assistant is None or not preceding_assistant.presented_text.strip():
        return None

    # Mutual uptake classification
    uptake_kind, min_confidence = classify_uptake(user_text)
    if uptake_kind is None:
        return None

    # Strict confidence threshold
    if draft.confidence < min_confidence:
        return None

    # Cue extraction & length bounds (2..80 chars)
    extracted = _extract_cue_and_context(draft)
    if extracted is None:
        return None
    cue, model_context = extracted
    if not (2 <= len(cue.strip()) <= 80):
        return None

    # Grounding in user text or preceding presented assistant text
    if not is_cue_grounded(cue, user_text, preceding_assistant.presented_text):
        return None

    # Combined content sensitivity check
    if is_sensitive_content(
        user_text,
        preceding_assistant.presented_text,
        cue,
        model_context,
        draft.text,
    ):
        return None

    # Deterministic cue-specific identity predicate
    normalized_cue = normalize_cue(cue)
    predicate = f"shared_joke.{normalized_cue}"

    # Factual wording and structured value
    factual_text = ensure_factual_joke_text(cue)
    presented_context = preceding_assistant.presented_text.strip()[:_MAX_CONTEXT_CHARS]
    structured_value: dict[str, object] = {
        "cue": cue,
        "context": presented_context,
        "kind": "shared_joke",
    }

    # Bounded multi-event provenance: current user turn + preceding presented assistant
    evidence_event_ids = (source_event_id, preceding_assistant.event_id)

    updated_draft = draft.model_copy(
        update={
            "predicate": predicate,
            "subject_id": "relationship",
            "value": structured_value,
            "text": factual_text,
            "sensitivity": PrivacyLevel.PRIVATE,
        }
    )

    return ExtractedMemoryCandidate(
        draft=updated_draft,
        explicit=False,
        rationale=f"shared joke mutual uptake ({uptake_kind}): {candidate.rationale}",
        evidence_event_ids=evidence_event_ids,
        auto_commit=True,
    )
