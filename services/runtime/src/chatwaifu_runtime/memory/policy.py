"""Deterministic persistence and retrieval policy for memory proposals."""

from enum import StrEnum

from chatwaifu_protocol.base import PrivacyLevel

from chatwaifu_runtime.memory.extractor import ExtractedMemoryCandidate


class MemoryWriteDecision(StrEnum):
    COMMIT = "commit"
    REVIEW = "review"
    REJECT = "reject"


class MemoryPolicy:
    def decide_write(
        self, candidate: ExtractedMemoryCandidate, *, confirmed: bool = False
    ) -> MemoryWriteDecision:
        if candidate.draft.confidence < 0.75:
            return MemoryWriteDecision.REJECT
        if not candidate.draft.namespace.startswith(("character/", "user/", "skill/")):
            return MemoryWriteDecision.REJECT
        if candidate.draft.sensitivity is PrivacyLevel.SENSITIVE and not confirmed:
            return MemoryWriteDecision.REVIEW
        if candidate.explicit or confirmed:
            return MemoryWriteDecision.COMMIT
        return MemoryWriteDecision.REVIEW

    def allow_retrieval(self, sensitivity: PrivacyLevel) -> bool:
        return sensitivity is not PrivacyLevel.SENSITIVE
