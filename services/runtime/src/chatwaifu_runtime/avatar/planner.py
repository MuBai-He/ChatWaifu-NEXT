"""Conservative semantic cue planner for explicit user-facing signals."""

from collections import defaultdict, deque
from dataclasses import dataclass
from typing import Literal
from uuid import UUID

from chatwaifu_protocol.character import ResponsePlan


@dataclass(frozen=True, slots=True)
class PlannedAvatarCue:
    kind: Literal["expression", "motion"]
    name: str
    priority: int
    duration_ms: int


class SemanticAvatarCuePlanner:
    """Map high-confidence user intent to model-independent avatar semantics.

    The planner intentionally does not parse arbitrary assistant prose and never
    emits Live2D resource identifiers. Unknown or ambiguous turns produce no cue.
    """

    def __init__(self) -> None:
        self._recent_motions: dict[UUID, deque[str]] = defaultdict(lambda: deque(maxlen=3))

    def plan_user_turn(self, text: str) -> tuple[PlannedAvatarCue, ...]:
        normalized = text.casefold().strip()
        expression = self._expression(normalized)
        motion = self._motion(normalized)
        planned: list[PlannedAvatarCue] = []
        if expression is not None:
            planned.append(
                PlannedAvatarCue("expression", expression, priority=58, duration_ms=4_000)
            )
        if motion is not None:
            name, duration_ms = motion
            planned.append(PlannedAvatarCue("motion", name, priority=62, duration_ms=duration_ms))
        return tuple(planned)

    def plan_response(
        self,
        session_id: UUID,
        plan: ResponsePlan,
        capabilities: dict[str, list[str]],
    ) -> tuple[PlannedAvatarCue, ...]:
        expressions = set(capabilities.get("expressions", ()))
        motions = set(capabilities.get("motions", ()))
        expression = plan.expression if plan.expression in expressions else None
        if expression is None and "neutral" in expressions:
            expression = "neutral"
        planned = (
            [PlannedAvatarCue("expression", expression, priority=64, duration_ms=5_000)]
            if expression is not None
            else []
        )
        if plan.motion and plan.motion in motions:
            recent = self._recent_motions[session_id]
            should_emit = not recent or recent[-1] != plan.motion
            recent.append(plan.motion)
            if should_emit:
                duration = {
                    "headpat": 4_500,
                    "stare": 3_200,
                    "flustered": 6_000,
                    "sing": 8_000,
                }.get(plan.motion, 4_000)
                planned.append(
                    PlannedAvatarCue("motion", plan.motion, priority=66, duration_ms=duration)
                )
        return tuple(planned)

    def _expression(self, text: str) -> str | None:
        if _contains_any(text, ("难过", "伤心", "哭", "sad", "upset")):
            return "sad"
        if _contains_any(text, ("生气", "讨厌", "气死", "angry", "mad")):
            return "angry"
        if _contains_any(text, ("吓", "震惊", "居然", "surprise", "shocked")):
            return "surprised"
        if _contains_any(text, ("喜欢你", "可爱", "害羞", "脸红", "love you", "cute")):
            return "shy"
        if _contains_any(text, ("开心", "高兴", "谢谢", "你好", "hello", "thanks", "happy")):
            return "happy"
        if (
            "?" in text
            or "\uff1f" in text
            or _contains_any(text, ("为什么", "怎么", "什么", "who", "why", "how"))
        ):
            return "curious"
        return None

    def _motion(self, text: str) -> tuple[str, int] | None:
        if _contains_any(text, ("唱歌", "唱一首", "sing")):
            return ("sing", 8_000)
        if _contains_any(text, ("摸摸头", "摸头", "摸你", "headpat", "pat your head")):
            return ("headpat", 4_500)
        if _contains_any(text, ("盯着", "看着我", "看我", "stare")):
            return ("stare", 3_200)
        if _contains_any(text, ("害羞", "脸红", "不好意思", "embarrass", "fluster")):
            return ("flustered", 6_000)
        return None


def _contains_any(text: str, needles: tuple[str, ...]) -> bool:
    return any(needle in text for needle in needles)
