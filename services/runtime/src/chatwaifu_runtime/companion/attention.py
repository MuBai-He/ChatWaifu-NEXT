"""Deterministic address detection after STT and before turn commitment."""

from dataclasses import dataclass
from typing import Literal

from chatwaifu_runtime.companion.models import CompanionSettings

type VoiceActivationMode = Literal["push_to_talk", "open_mic"]


@dataclass(frozen=True, slots=True)
class AttentionDecision:
    accepted: bool
    text: str
    reason: Literal["push_to_talk", "wake_disabled", "wake_phrase", "not_addressed", "empty"]
    wake_phrase: str | None = None


def evaluate_attention(
    text: str,
    activation_mode: VoiceActivationMode,
    settings: CompanionSettings,
) -> AttentionDecision:
    normalized = text.strip()
    if not normalized:
        return AttentionDecision(False, "", "empty")
    if activation_mode == "push_to_talk":
        return AttentionDecision(True, normalized, "push_to_talk")
    if not settings.wake_phrase_enabled:
        return AttentionDecision(True, normalized, "wake_disabled")
    folded = normalized.casefold()
    for phrase in sorted(settings.wake_phrases, key=len, reverse=True):
        target = phrase.casefold()
        index = folded.find(target)
        # Only an actual leading address is accepted; a name mentioned inside nearby
        # conversation must not wake or interrupt the character.
        if index < 0:
            continue
        punctuation = "，,。.!！?？、：:~～ "  # noqa: RUF001 - intentional CJK punctuation
        prefix = normalized[:index].strip(punctuation).casefold()
        if prefix not in {"", "喂", "嗯", "那个", "嗨", "hey"}:
            continue
        remainder = normalized[index + len(phrase) :].lstrip(punctuation)
        if not remainder:
            return AttentionDecision(False, "", "empty", phrase)
        return AttentionDecision(True, remainder, "wake_phrase", phrase)
    return AttentionDecision(False, normalized, "not_addressed")
